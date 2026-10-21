"""Runtime of CHEM-166 instrument.eppendorf_cellxpertc170i - Eppendorf CellXpert C170i CO2 incubator (with the O2 1-20 %, relative-humidity
and water-level options).

Process model (SI; the host clock is the only time source).  step(dt_s) integrates on a fixed 1 s grid aligned to absolute time and updates the
cultures on a 60 s grid, so any split of the same interval gives identical results:
  - chamber heat: air and wall (inner chamber, shelves, rack, tray) heat capacities, 6-sided wall heating (cascade control: an air-temperature
    PI sets a wall target within the set point -1 / +1.5 K, the heater follows the wall target), losses to the room through the insulation and
    the doors, air exchange with the room (gasket leak, unplugged access ports; outer door alone: small exchange behind the inner glass door; both
    doors open: fast exchange fitted to the manufacturer recovery chart), cultures exchanging heat with the air and the shelves;
  - chamber gas (moles): CO2 injected through the solenoid valve (PI on the NDIR reading), N2 injected to lower O2 (PI on the ZrO2 reading),
    water vapour from the water tray (natural-convection evaporation with the tray water temperature as a node), condensation on the inner glass
    door when the vapour pressure exceeds its saturation pressure; the chamber vents or draws room air to stay at the room pressure; the sensors
    read partial pressures (displayed as % of 101.325 kPa) with first-order lags and noise;
  - supply: CO2 / N2 inlet pressure (0.05-0.15 MPa; warning < 0.04 MPa, control stopped < 0.02 MPa with the pressure monitor on, inlet valve
    closed > 0.18 MPa);
  - operation (operating manual): set points 22-50 C (at least 4 K above the room), CO2 0.1-20 %, O2 1-20 %; gas injection paused while the outer
    door is open; sensor initialisation after switch-on / HTD (CO2 30 min, O2 10 min); after a power interruption the CO2 valve stays off until
    the temperature set point is reached again; alarms with the factory limits and pauses; temperature limiter (+10 K / -5 K); control-timeout
    errors; BMS relays; high-temperature disinfection (checks, heating ramp to 180 C, 2 h hold, cooling, sensor re-initialisation; door opening
    or power loss aborts it);
  - cultures (vessels from the Thermo Fisher table, HiMedia media, Cellosaurus cell lines): medium temperature, dissolved CO2 / O2 approaching
    Henry equilibrium through the headspace and the liquid layer, pH from the charge balance (bicarbonate / CO2, HEPES, phosphate, lactate),
    phenol-red colour, evaporation and osmolality, glucose and lactate, exponential growth after a lag with temperature / pH / glucose /
    osmolality / oxygen / lactate / contact factors, death; on the bench a vessel relaxes to room conditions.
Generic schema / capability / session framework follows the CHEM-164 / CHEM-165 runtimes.

Front panel (panel.touch / panel.switch / panel.capture / panel.frame): the VisioNize touch screen of the manual (panel_ui.json beside the contract)
run by the generic engine _panel_ui.py on the capabilities below, including the device functions behind its menus (settings, clock, users and rights,
information bar, relays, screen lock, recurring tasks, operation records, export).
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.eppendorf_cellxpertc170i"
CATALOG_ID = "CHEM-166"
ASSET_VERSION = '1.2.0'
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/eppendorf_cellxpertc170i'
LIBS = {'media': 'media_library.json', 'cells': 'cell_line_library.json', 'vessels': 'vessel_library.json', 'equilibrium': 'equilibrium_model.json',
        'model': 'incubator_model.json'}

# ---------------------------------------------------------------- constants (SI)
R = 8.314462618
P_REF = 101325.0                        # NDIR / ZrO2 readings are expressed as % of 101.325 kPa (no barometric compensation assumed)
CP_GAS = 29.1                           # J/(mol K), moist air
LV = 43400.0                            # J/mol, latent heat of water near 37 C
C_WATER = 4180.0                        # J/(kg K)
RHO_WATER = 993.3                       # kg/m3 at 37 C
M_WATER = 0.01801528
X_CO2_DRY_AIR = 420e-6
X_O2_DRY_AIR = 0.2095
H_TICK = 1.0                            # physics grid (s)
CULTURE_DT = 60.0                       # culture grid (s)
CHART_DT = 60.0
V_CHAMBER = 0.167                       # manual: total volume 167 L
TRAY_AREA = 0.42 * 0.32                 # water tray (engineering size; capacity 2.5 L documented)
TRAY_CAPACITY = 2.5e-3
TRAY_LOW = 0.5e-3                       # water level hint below 0.5 L (manual 5.2.6)
TRAY_STEEL_J_K = 750.0
GLASS_AREA = 0.55 * 0.70
SHELVES = 4
SHELF_AREA = 0.522 * 0.428
SHELF_MAX_KG = 10.0
T_SET_RANGE = (295.15, 323.15)          # 22-50 C
CO2_RANGE = (0.001, 0.20)
O2_RANGE = (0.01, 0.20)                 # the 1-20 % O2 option
HTD_WALL_K = 453.15
HTD_HOLD_S = 7200.0
HTD_HEAT_TIMEOUT_S = 6 * 3600.0
HOT_SENSOR_K = 323.15                   # sensors stay off above 50 C (restart after an interrupted HTD)
INIT_S = {'co2': 1800.0, 'o2': 600.0}
DOOR_OPEN_RAD = 1.745                   # 100 deg
DOOR_RATE = DOOR_OPEN_RAD / 1.5         # a door is swung open / closed in ~1.5 s (host / robot motion)
LATCH_OPEN_RAD = math.pi / 2
LATCH_RATE = LATCH_OPEN_RAD / 0.5
DOOR_SIGNALS = (15.0, 30.0, 45.0, 60.0, 90.0)
LONG_DOOR_S = 300.0
ALARM_DEFAULTS = dict(temperature=dict(low_k=0.5, high_k=0.5, enabled=True), co2=dict(low=0.005, high=0.005, enabled=True), o2=dict(low=0.005, high=0.005, enabled=True),
                      rh=dict(low=0.80, high=None, enabled=True), door=dict(signal_s=30.0, enabled=True), water_level=dict(enabled=True))
PAUSE = dict(door=dict(temperature=900.0, co2=900.0, o2=900.0, rh=3.5 * 3600, water=2 * 3600.0),        # after closing the outer door
             switch_on=dict(temperature=7200.0, co2=7200.0, o2=7200.0, rh=5 * 3600.0, water=3600.0),
             setpoint=dict(temperature=1800.0, co2=1800.0, o2=1800.0),
             htd=dict(temperature=7200.0, co2=7200.0, o2=7200.0, rh=7200.0, water=7200.0))
CONTROL_TIMEOUT_S = 3600.0              # CO2 / O2 control error when the set point is not reached (engineering)
SETPOINT_NOT_REACHED_S = 4 * 3600.0     # 6731FW.108
SUBNORMAL_S = 3600.0
TASK_NAMES = {'high_temperature_disinfection': 'High-temperature disinfection', 'performance_check': 'Performance check', 'chamber_cleaning': 'Chamber cleaning',
              'water_refill': 'Water refill'}
TASKS = {'high_temperature_disinfection': dict(procedure=True, interval_s=None), 'performance_check': dict(procedure=True, interval_s=None),
         'chamber_cleaning': dict(procedure=False, interval_s=None), 'water_refill': dict(procedure=False, interval_s=14 * 86400.0)}
MAX_EVENTS, MAX_RECORDS, MAX_VESSELS, MAX_CHART = 1000, 200, 60, 20160
# ---- device functions behind the touch screen (operating manual sections 6-10): settings, clock, users and rights, messages, relays, screen lock,
# recurring tasks, operation records.  Values the manual does not give are assumptions (listed in the contract limitations and panel_ui.json).
EPOCH_S = 1767225600.0                  # device clock at host time 0: 2026-01-01 00:00:00 UTC (assumption; clock.set moves it)
TIMEZONES = {'Europe/Berlin': 1.0, 'Europe/Paris': 1.0, 'Europe/London': 0.0, 'America/New_York': -5.0, 'America/Chicago': -6.0, 'America/Los_Angeles': -8.0,
             'Asia/Shanghai': 8.0, 'Asia/Singapore': 8.0, 'Asia/Tokyo': 9.0, 'Asia/Kolkata': 5.5, 'Australia/Sydney': 10.0, 'Africa/Johannesburg': 2.0, 'UTC': 0.0}
RELAY_EVENTS = ['temperature_alarm', 'temperature_high_alarm', 'temperature_low_alarm', 'co2_alarm', 'co2_high_alarm', 'co2_low_alarm', 'o2_alarm',
                'o2_high_alarm', 'o2_low_alarm', 'humidity_alarm', 'water_level_alarm', 'warnings', 'error', 'any', 'off']
HOME_FUNCTIONS = ['temperature', 'co2', 'o2', 'rh']
ROLES = ['restricted', 'user', 'administrator']
_ADMIN, _USER_UP = ('administrator',), ('user', 'administrator')
RIGHTS = {'change_parameters': _USER_UP, 'change_alarm_limits': _ADMIN, 'change_settings': _ADMIN, 'change_relay_settings': _ADMIN,
          'change_alarm_volume': _USER_UP, 'change_home_screen': _USER_UP, 'change_own_secret': tuple(ROLES), 'change_user_name': _ADMIN,
          'change_user_rights': _ADMIN, 'ack_alarm': _USER_UP, 'ack_error': _ADMIN, 'ack_warning': tuple(ROLES), 'start_htd': _USER_UP,
          'start_performance_check': _ADMIN, 'start_relay_test': _ADMIN, 'export': tuple(ROLES), 'configure_network': _ADMIN, 'manage_users': _ADMIN}
TASK_INTERVALS = {'daily': 86400.0, 'weekly': 7 * 86400.0, 'monthly': 30 * 86400.0, 'yearly': 365 * 86400.0}
MAX_CUSTOM_TASKS, MAX_USERS, MAX_OP_RECORDS = 4, 999, 1000
RELAY_TEST_S = 15.0                     # manual p.81: the relay is active for 15 s
CX_SETTINGS = [   # key, type, default, options / range, manual page; written with settings.configure, shown in device.settings
    dict(key='about.name', type='text', default='CellXpert C170i', max_len=24, page='p.74'),
    dict(key='about.registration', type='text', default='', max_len=24, page='p.74'),
    dict(key='about.location', type='text', default='', max_len=24, page='p.74'),
    dict(key='datetime.automatic', type='bool', default=False, page='p.75'),
    dict(key='datetime.timezone', type='enum', default='Europe/Berlin', options=list(TIMEZONES), page='p.75-76'),
    dict(key='network.remote_access', type='bool', default=True, page='p.77'),       # 1.2.0: on, so the agent API (the external-software interface) works
    dict(key='network.opc_rest', type='bool', default=False, page='p.77'),
    dict(key='network.dhcp', type='bool', default=True, page='p.77'),
    dict(key='network.ip', type='ip', default='192.168.0.20', page='p.77'),
    dict(key='network.subnet', type='ip', default='255.255.255.0', page='p.77'),
    dict(key='network.gateway', type='ip', default='192.168.0.1', page='p.77'),
    dict(key='network.dns', type='ip', default='192.168.0.1', page='p.77'),
    dict(key='acoustic.mode', type='enum', default='alarms_and_interface', options=['alarms_and_interface', 'alarms'], page='p.79'),
    dict(key='acoustic.volume', type='int', default=50, min=0, max=100, page='p.79'),
    dict(key='display.brightness', type='int', default=80, min=10, max=100, page='p.79'),
    dict(key='home.functions', type='list', default=list(HOME_FUNCTIONS), options=list(HOME_FUNCTIONS), page='p.79-80'),
    dict(key='relay.ch1', type='enum', default='temperature_alarm', options=RELAY_EVENTS, page='p.80'),
    dict(key='relay.ch2', type='enum', default='co2_alarm', options=RELAY_EVENTS, page='p.80'),
    dict(key='relay.ch3', type='enum', default='o2_alarm', options=RELAY_EVENTS, page='p.80'),
    dict(key='relay.ch4', type='enum', default='water_level_alarm', options=RELAY_EVENTS, page='p.80'),
    dict(key='records.company', type='text', default='', max_len=40, page='p.81'),
    dict(key='screenlock.auto', type='bool', default=False, page='p.101'),
    dict(key='screenlock.timer_s', type='enum_num', default=300.0, options=[60.0, 120.0, 300.0, 600.0, 1800.0], page='p.101'),
    dict(key='users.auto_logout_s', type='enum_num', default=600.0, options=[0.0, 60.0, 300.0, 600.0, 1800.0, 3600.0], page='p.107'),
    dict(key='users.login_mode', type='enum', default='pin', options=['pin', 'password'], page='p.105-107'),
    dict(key='users.grant', type='enum', default='none', options=['none', 'restricted', 'user'], page='p.107'),
]
SETTINGS_DEFAULT = {d['key']: copy.deepcopy(d['default']) for d in CX_SETTINGS}
_TEMP_CODES, _CO2_HIGH, _CO2_LOW = ('E_6731FW111', 'E_6731W112', 'E_6731FW108'), ('CO2_HIGH', 'CO2_PRESSURE_HIGH'), ('CO2_LOW', 'CO2_PRESSURE_LOW')
RELAY_MATCH = {   # alarm / error codes that switch a relay for each selectable event (p.80); the factory choice per relay is the earlier fixed mapping
    'temperature_alarm': lambda k: k.startswith('TEMP') or k in _TEMP_CODES, 'temperature_high_alarm': lambda k: k in ('TEMP_HIGH', 'E_6731FW111'),
    'temperature_low_alarm': lambda k: k in ('TEMP_LOW', 'E_6731W112'), 'co2_alarm': lambda k: k.startswith('CO2') or k == 'E_6731FW214',
    'co2_high_alarm': lambda k: k in _CO2_HIGH, 'co2_low_alarm': lambda k: k in _CO2_LOW,
    'o2_alarm': lambda k: k.startswith('O2') or k.startswith('N2') or k == 'E_6731FW314', 'o2_high_alarm': lambda k: k == 'O2_HIGH',
    'o2_low_alarm': lambda k: k == 'O2_LOW', 'humidity_alarm': lambda k: k == 'RH_LOW', 'water_level_alarm': lambda k: k == 'WATER_LOW',
    'warnings': lambda k: k.endswith('_WARNING'), 'error': lambda k: k.startswith('E_'), 'any': lambda k: True, 'off': lambda k: False}
# growth-model constants (engineering values of common cell-culture practice)
GROWTH = dict(lag_s=12 * 3600.0, q10=2.5, arrest_below_k=7.0, decline_above_k=1.5, arrest_above_k=4.0, heat_death_above_k=5.0, heat_death_per_s_k=3e-5,
              ph_band=(7.0, 7.7), ph_sigma=0.30, contact_exponent=4.0, acid_death_ph=6.6, acid_death_per_s=6e-6, glc_km=0.5, q_glc_mol_s=5.6e-17, y_lac=1.6, lac_ki=40.0,
              osm_ok=0.38, osm_stop=0.48, osm_death=0.52, osm_death_per_s=1.4e-5, our_mol_s=4.2e-17, rq=0.9, o2_km=0.005, starve_below=0.1, starve_death_per_s=1.4e-5,
              base_death_per_s=2e-7, freeze_k=272.15, bacteria_doubling_s=1800.0, bacteria_q_glc=5e-19, bacteria_turbid_m3=1e13, bacteria_death_per_s=8e-5,
              bacteria_stop_ph=5.0, bacteria_full_ph=5.5)
GROWTH_UNITS = dict(lag_s='s', q10='1', arrest_below_k='K', decline_above_k='K', arrest_above_k='K', heat_death_above_k='K', heat_death_per_s_k='1/(s K)', ph_band='1',
                    ph_sigma='1', contact_exponent='1', acid_death_ph='1', acid_death_per_s='1/s', glc_km='mol/m3', q_glc_mol_s='mol/s', y_lac='1', lac_ki='mol/m3',
                    osm_ok='mol/kg', osm_stop='mol/kg', osm_death='mol/kg', osm_death_per_s='1/s', our_mol_s='mol/s', rq='1', o2_km='mol/m3', starve_below='mol/m3',
                    starve_death_per_s='1/s', base_death_per_s='1/s', freeze_k='K', bacteria_doubling_s='s', bacteria_q_glc='mol/s', bacteria_turbid_m3='1/m3',
                    bacteria_death_per_s='1/s', bacteria_stop_ph='1', bacteria_full_ph='1')
GROWTH_ZH = dict(lag_s='接种后的滞后期', q10='低于最适温度时的Q10', arrest_below_k='低于最适温度多少K时停止生长', decline_above_k='高于最适温度时生长开始下降的温差',
                 arrest_above_k='高于最适温度时停止生长的温差', heat_death_above_k='高于最适温度时开始热死亡的温差', heat_death_per_s_k='热死亡率（每K）',
                 ph_band='生长最适pH区间', ph_sigma='最适区外pH因子的高斯宽度', contact_exponent='接触抑制指数', acid_death_ph='酸致死的pH阈值',
                 acid_death_per_s='酸致死率', glc_km='葡萄糖Monod常数', q_glc_mol_s='每个细胞的葡萄糖消耗率', y_lac='乳酸/葡萄糖摩尔产率', lac_ki='乳酸抑制常数',
                 osm_ok='渗透压不影响生长的上限', osm_stop='生长停止的渗透压', osm_death='开始渗透压致死的渗透压', osm_death_per_s='渗透压致死率',
                 our_mol_s='每个细胞的耗氧率', rq='呼吸商（CO2/O2）', o2_km='细胞周围氧的Monod常数', starve_below='饥饿死亡的葡萄糖阈值', starve_death_per_s='饥饿死亡率',
                 base_death_per_s='基础死亡率（倍增时间为净值，生长率已加回）', freeze_k='冻结温度', bacteria_doubling_s='污染细菌的倍增时间',
                 bacteria_q_glc='每个细菌的葡萄糖消耗率', bacteria_turbid_m3='培养基可见浑浊的细菌浓度', bacteria_death_per_s='细菌达浑浊后细胞的死亡率',
                 bacteria_stop_ph='细菌停止生长的pH', bacteria_full_ph='细菌全速生长的最低pH')
# the library doubling time is a net population doubling time: mu_max = ln2 / Td + base death, so the reference condition (37 C, pH 7.0-7.7,
# osmolality <= 0.38 osmol/kg, glucose and O2 in excess, sparse culture, after the lag) reproduces Td


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- libraries and the calibrated model
@functools.lru_cache(maxsize=None)
def _lib(name):
    return json.loads((ASSET_DIR / LIBS[name]).read_text())


def media():
    return _lib('media')['substances']


def cell_lines():
    return _lib('cells')['substances']


def vessel_types():
    return _lib('vessels')['vessels']


def eqm():
    return _lib('equilibrium')


MODEL_DEFAULTS = dict(
    c_wall_j_k=40000.0, ua_wall_w_k=4.0, g_air_wall_w_k=7.0, g_door_w_k=0.3, heater_max_w=1100.0, electronics_w=25.0,
    k_leak_per_s=1.5e-5, k_port_per_s=3e-4, k_gasket_fault_per_s=2e-3, k_unlatched_per_s=5e-6, k_outer_only_per_s=2e-4, k_door_open_per_s=0.12,
    q_co2_max_mol_s=2.5e-3, q_n2_max_mol_s=1.37e-2, gas_restart_delay_s=10.0,
    tau_t_sensor_s=8.0, tau_ndir_s=10.0, tau_o2_sensor_s=8.0, tau_rh_sensor_s=60.0, tau_rh_covered_s=3600.0,
    noise_t_k=0.02, noise_co2=0.0002, noise_o2=0.0004, noise_rh=0.003,
    h_m_tray_m_s=2.0e-3, g_tray_w_k=6.0, h_m_glass_m_s=1.0e-3, dt_glass_latched_k=0.3, dt_glass_unlatched_k=2.5,
    g_cold_spot_m3_s=2.6e-4, dt_cold_spot_k=3.0, internal_heat_k=4.0,
    kp_air=1.2, ki_air=0.004, wall_band_lo_k=-1.0, wall_band_hi_k=1.5, kp_wall=1.0, door_hold_s=300.0,
    kp_co2=60.0, ki_co2=0.02, kp_o2=40.0, ki_o2=0.02)
MODEL_EVIDENCE = {}
MODEL_PARAM_ZH = dict(
    c_wall_j_k='内腔、搁板、搁板架与水盘（含部分保温层）的热容', ua_wall_w_k='内腔壁到室内的总热导（保温层、门与框）', g_air_wall_w_k='腔内空气与内壁（含搁板）的换热热导',
    g_door_w_k='空气经玻璃内门与外门直接到室内的热导', heater_max_w='六面加热器最大功率', electronics_w='电子部件与门框加热的耗电（其中internal_heat_k×UA进入腔体）',
    k_leak_per_s='关门时腔体气体的泄漏换气率', k_port_per_s='每个未插堵头接入孔增加的换气率', k_gasket_fault_per_s='门封故障时增加的换气率',
    k_unlatched_per_s='内门关着但门闩未扣时增加的换气率', k_outer_only_per_s='只开外门（玻璃内门关着）时的换气率', k_door_open_per_s='两门全开时的换气率',
    q_co2_max_mol_s='CO2阀全开时的流量', q_n2_max_mol_s='N2阀全开时的流量（约20 L/min）', gas_restart_delay_s='关外门后恢复注气前的等待时间',
    tau_t_sensor_s='温度传感器时间常数', tau_ndir_s='NDIR CO2传感器时间常数', tau_o2_sensor_s='ZrO2 O2传感器时间常数', tau_rh_sensor_s='湿度传感器时间常数',
    tau_rh_covered_s='盖着高温消毒保护罩时湿度传感器的时间常数', noise_t_k='温度读数噪声（标准差）', noise_co2='CO2读数噪声（标准差，分数）',
    noise_o2='O2读数噪声（标准差，分数）', noise_rh='湿度读数噪声（标准差，分数）', h_m_tray_m_s='水盘水面的蒸发传质系数', g_tray_w_k='内壁（底面）到水盘水的换热热导',
    h_m_glass_m_s='玻璃内门表面的凝结传质系数', dt_glass_latched_k='门闩扣好时玻璃内门比空气低的温差', dt_glass_unlatched_k='门闩未扣时玻璃内门比空气低的温差',
    g_cold_spot_m3_s='最冷表面（门框/密封条一带）的凝结传导（传质系数×面积）', dt_cold_spot_k='最冷表面比空气低的温差（按稳态湿度92.5 %标定）',
    internal_heat_k='不受控内部热（电子与门框）造成的最小温升，即设定值至少高于室温4 K的来源', kp_air='空气温度外环比例增益（K/K）', ki_air='空气温度外环积分增益',
    wall_band_lo_k='外环给出的壁温目标下限（相对设定值）', wall_band_hi_k='外环给出的壁温目标上限（相对设定值）', kp_wall='壁温内环比例增益（加热器占空比/K）',
    door_hold_s='关外门后外环保持冻结的时间（避免开门后过冲）', kp_co2='CO2阀比例增益（占空比/分数）', ki_co2='CO2阀积分增益', kp_o2='N2阀比例增益（占空比/分数）',
    ki_o2='N2阀积分增益')


def _model():
    m = dict(MODEL_DEFAULTS)
    p = ASSET_DIR / LIBS['model']
    if p.exists():
        cal = json.loads(p.read_text())
        m.update({k: v['value'] for k, v in cal['parameters'].items() if k in m})
        MODEL_EVIDENCE.update({k: v.get('evidence', 'assumption') for k, v in cal['parameters'].items()})
    return m


MODEL = _model()


# ---------------------------------------------------------------- physical chemistry
def p_sat(T):
    """Saturation vapour pressure of water (Pa), Buck 1981."""
    t = T - 273.15
    return 611.21 * math.exp((18.678 - t / 234.5) * t / (257.14 + t))


def _henry(key, T):
    e = eqm()[key]
    return e['H_ref_mol_m3_pa'] * math.exp(e['dlnH_d1overT_k'] * (1.0 / T - 1.0 / e['T_ref_k'])) * e['salting_out_factor']


def henry_co2(T):
    return _henry('co2_henry', T)


def henry_o2(T):
    return _henry('o2_henry', T)


def pk1_apparent(T):
    e = eqm()['carbonic_pk1']
    f = lambda x: e['a'] / x + e['b'] * x + e['c']                               # noqa: E731
    return e['apparent_pk_310k'] + f(T) - f(310.15)


def pka_hepes(T):
    e = eqm()['hepes']
    return e['pKa_293k'] + e['dpKa_dT_per_k'] * (T - 293.15)


def diffusion(gas, T):
    d = eqm()['diffusion'][f'{gas}_m2_s_310k']
    mu = lambda x: 0.02939e-3 * math.exp(507.88 / (x - 149.3))                  # noqa: E731
    return d * (T / 310.15) * mu(310.15) / mu(T)


def _constants(T):
    """Apparent constants at the medium temperature: carbonic K1', HEPES, phosphate K2', water, carbonate K2, lactic acid."""
    return (10 ** -pk1_apparent(T), 10 ** -pka_hepes(T), 10 ** -eqm()['phosphate_pk2']['apparent_pk_310k'], 10 ** -eqm()['water_pkw_310k']['value'], 10 ** -10.1,
            10 ** -eqm()['lactic_acid']['pKa'])


def _bases(h, hepes, phos, lac, k):
    """Charge carried by the non-carbonate buffers at [H+] = h (mol/m3): HEPES-, HPO4 2- (from H2PO4-), lactate-, OH- minus H+."""
    _, kh, k2p, kw, _, kl = k
    return hepes * kh / (kh + h) + phos * k2p / (k2p + h) + lac * kl / (kl + h) + 1e3 * (kw / h - h)


def _bisect_ph(f, lo=2.0, hi=12.0):
    if f(lo) > 0:
        return lo
    if f(hi) < 0:
        return hi
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def medium_ph(alk, co2_aq, hepes, phos, T, lac=0.0):
    """pH (activity scale) of a medium with the dissolved CO2 fixed by its gas phase: charge balance
    alk = [HCO3-] + 2[CO3 2-] + [HEPES-] + [HPO4 2-] + [lactate-] + [OH-] - [H+] (mol/m3; the base equivalents alk are counted from CO2, the HEPES acid,
    H2PO4- and lactic acid), apparent constants at the medium temperature; bisection on pH 2-12."""
    k = _constants(T)
    k1, k2c = k[0], k[4]
    return _bisect_ph(lambda ph: k1 * co2_aq / 10 ** -ph * (1 + 2 * k2c / 10 ** -ph) + _bases(10 ** -ph, hepes, phos, lac, k) - alk)


def sealed_state(alk, n_c, V, V_hs, hepes, phos, lac, T):
    """Sealed vessel: the total CO2 n_c (mol: dissolved inorganic carbon of the medium V plus the CO2 of the headspace V_hs) partitioned at equilibrium;
    returns (pH, dissolved CO2 mol/m3). alk, hepes, phos, lac in mol/m3."""
    k = _constants(T)
    k1, k2c = k[0], k[4]
    gas = V_hs / (henry_co2(T) * R * T)                                         # headspace moles per (mol/m3 dissolved CO2)

    def co2(h):
        return n_c / (V * (1 + k1 / h + k1 * k2c / (h * h)) + gas)
    ph = _bisect_ph(lambda p: k1 * co2(10 ** -p) / 10 ** -p * (1 + 2 * k2c / 10 ** -p) + _bases(10 ** -p, hepes, phos, lac, k) - alk)
    return ph, co2(10 ** -ph)


def co2_for_ph(ph, alk, hepes, phos, lac, T):
    """Dissolved CO2 (mol/m3) that gives the medium the pH ph (inverse of medium_ph; 0 when the buffers alone are above that pH)."""
    k = _constants(T)
    h = 10 ** -ph
    hco3 = (alk - _bases(h, hepes, phos, lac, k)) / (1 + 2 * k[4] / h)
    return max(0.0, hco3 * h / k[0])


def hepes_base_fraction():
    e = eqm()['hepes']['titration']
    kh = 10 ** -pka_hepes(e['T_k'])
    return kh / (kh + 10 ** -e['pH'])


def phenol_red(ph, present):
    if not present:
        return 'colourless', [0.93, 0.92, 0.88]
    name = 'yellow'
    for lim, nm in eqm()['phenol_red']['colour_classes']:                      # lower bounds of the classes above yellow
        if ph >= lim:
            name = nm
    b = 1.0 / (1.0 + 10 ** (eqm()['phenol_red']['pKa'] - ph))
    acid, base = (0.96, 0.80, 0.18), (0.80, 0.06, 0.30)
    return name, [round(acid[i] + (base[i] - acid[i]) * b, 3) for i in range(3)]


_MASK = (1 << 64) - 1


def _mix(z):
    z = (z + 0x9E3779B97F4A7C15) & _MASK
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
    return z ^ (z >> 31)


def _gauss(seed, tick, ch):
    """Deterministic standard normal for (seed, tick, channel): identical for any split of the host steps."""
    a = _mix((seed * 1000003 + tick * 16 + ch) & _MASK)
    b = _mix(a)
    u1 = ((a >> 11) + 0.5) / 9007199254740992.0
    u2 = ((b >> 11) + 0.5) / 9007199254740992.0
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _lag(x, target, dt, tau):
    return target + (x - target) * math.exp(-dt / tau) if tau > 0 else target


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


# ---------------------------------------------------------------- schema helpers (as CHEM-164 / CHEM-165)
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
FREEOBJ = {'type': 'object'}
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


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
LIBID = dict(type='string', minLength=1, maxLength=80)
FAULTS = ['power_loss', 'heater_failure', 'heater_stuck_on', 'co2_valve_stuck_closed', 'gasket_leak', 'ndir_drift', 'vessel_contamination']
# 1.2.0 (sweep 10-19): the device errors of manual chapter 11 (p.136-145) that were listed as not simulated; fault: (code, sensor it disables, cleared only by
# a restart of the incubator).  Sensor keys: T temperature, c CO2, o O2, rh humidity, wl water level; 'c_ctl' / 'o_ctl' stop that gas control.
DEVICE_FAULTS = {'temp_sensor_out_of_range': ('E_6731FW107', 'T', False), 'co2_control_software_error': ('E_6731FW213', 'c_ctl', True),
                 'o2_control_software_error': ('E_6731FW313', 'o_ctl', True), 'water_level_sensor_error': ('E_6731FW454', 'wl', True),
                 'co2_sensor_init_failed': ('E_6731FW203', 'c', True), 'o2_sensor_init_failed': ('E_6731FW303', 'o', True),
                 'rh_sensor_init_failed': ('E_6731FW403', 'rh', True), 'water_level_sensor_init_failed': ('E_6731FW453', 'wl', True),
                 'co2_over_range': ('E_6731FW204H', 'c', True), 'co2_under_range': ('E_6731FW204L', 'c', True), 'o2_over_range': ('E_6731FW304H', 'o', True),
                 'o2_under_range': ('E_6731FW304L', 'o', True), 'water_level_over_range': ('E_6731FW404H', 'wl', True),
                 'water_level_under_range': ('E_6731FW404L', 'wl', True), 'fatal_error': ('FATAL', None, True)}
FAULTS += list(DEVICE_FAULTS)
ALARM_KINDS = ['temperature', 'co2', 'o2', 'rh', 'door', 'water_level']
GASES = ['co2', 'o2']
EXPORT_KINDS = ['events', 'chart', 'htd_protocols', 'performance_checks']
CHART_FN = {'temperature': 'temperature_k', 'co2': 'co2_fraction', 'o2': 'o2_fraction', 'rh': 'rh'}      # chart functions (8.5.2) and their stored columns
EVENT_CATS = ['alarm', 'error', 'warning', 'setting', 'door', 'power', 'maintenance']                    # event-list filter categories (8.4.2)
_EVENT_CAT = dict(power='power', door='door', setting='setting', alarm='alarm', error='error', htd='maintenance', maintenance='maintenance')
PLANT_EXAMPLE = dict(room_k=295.15, room_rh=0.40, ambient_pressure_pa=101325.0, mains_voltage_v=230.0, co2_supply_pa=1.0e5, n2_supply_pa=1.0e5, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 313.15), room_rh=number('1', 0.0, 1.0), ambient_pressure_pa=number('Pa', 70000.0, 110000.0),
                 mains_voltage_v=number('V', 0.0, 264.0), co2_supply_pa=number('Pa', 0.0, 3.0e5), n2_supply_pa=number('Pa', 0.0, 3.0e5), noise_seed=integer(0, 2 ** 31 - 1)))
VESSEL_TYPES = ['dish_35mm', 'dish_60mm', 'dish_100mm', 'dish_150mm', 'plate_6', 'plate_12', 'plate_24', 'plate_96', 'plate_384', 'flask_t25', 'flask_t75', 'flask_t175',
                'flask_t225']
CLOSURES = ['lid', 'vented_cap', 'plug_seal_closed']
VESSEL = obj(dict(vessel_id=SID, vessel_type=enum(VESSEL_TYPES), medium_id=LIBID, medium_volume_m3=number('m3', 1e-8, 1e-4), cell_line_id=nullable(LIBID),
                  seeded_cells=number('1', 0.0, 1e10), temperature_k=number('K', 273.15, 313.15), closure=enum(CLOSURES),
                  initial_gas=enum(['as_supplied', 'air', 'incubator'])),
             optional=('cell_line_id', 'closure', 'initial_gas'))
VESSEL_EXAMPLE = dict(vessel_id='flask_1', vessel_type='flask_t75', medium_id='himedia_al111', medium_volume_m3=1.5e-5, cell_line_id='hela', seeded_cells=2.1e6,
                      temperature_k=295.15, closure='vented_cap', initial_gas='as_supplied')
OPERATION = obj(dict(operation_id=TEXT))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'door', 'setting', 'alarm', 'error', 'htd', 'culture', 'maintenance', 'fault']), code=TEXT, message_zh=TEXT))
CHART_ROW = obj(dict(time_s=number('s'), temperature_k=nullable(number('K')), co2_fraction=nullable(number('1')), o2_fraction=nullable(number('1')), rh=nullable(number('1')),
                     door_open=BOOL))
OBSERVE = obj(dict(vessel_id=TEXT, location=TEXT, medium_colour=TEXT, medium_rgb=array(number('1'), 3, 3), turbid=BOOL, medium_volume_m3=number('m3'),
                   lid_condensation=BOOL))
TRUTH = obj(dict(location=TEXT, medium_id=TEXT, cell_line_id=nullable(TEXT), temperature_k=number('K'), pH=number('1'), medium_volume_m3=number('m3'),
                  co2_aq_mol_m3=number('mol/m3'), o2_aq_mol_m3=number('mol/m3'), pericellular_o2_mol_m3=number('mol/m3'), glucose_mol_m3=number('mol/m3'),
                  lactate_mol_m3=number('mol/m3'), alkalinity_mol_m3=number('mol/m3'), osmolality_mol_kg=number('mol/kg'), cells_live=number('1'), cells_dead=number('1'),
                  viability=nullable(number('1')), confluence=nullable(number('1')), suspension_density_per_m3=nullable(number('1/m3')),
                  phase=enum(['empty', 'lag', 'growth', 'stationary', 'death']), bacteria=number('1'), contaminated=BOOL, evaporation_mol_s=number('mol/s'),
                  headspace_co2_fraction=nullable(number('1'))))
INSPECT = obj(dict(vessel_id=TEXT, truth=TRUTH))
LEDGER_KEYS = ['electrical_energy_j', 'heater_energy_j', 'co2_injected_mol', 'n2_injected_mol', 'co2_exchanged_out_mol', 'co2_exchanged_in_mol', 'water_evaporated_tray_mol',
               'water_evaporated_vessels_mol', 'water_condensed_mol', 'water_drained_to_tray_mol', 'water_vented_mol', 'water_filled_m3', 'water_removed_m3', 'heat_loss_wall_j', 'heat_loss_exchange_j',
               'heat_loss_door_j', 'heat_to_vessels_j', 'latent_tray_j', 'gas_heating_j', 'glucose_consumed_mol', 'lactate_produced_mol', 'cells_divided']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'mol' if k.endswith('_mol') else 'm3' if k.endswith('_m3') else '1') for k in LEDGER_KEYS})
RECORD_DETAILS = obj(dict(phases=nullable(obj(dict(heating=number('s'), holding=number('s'), cooling=number('s')), optional=('heating', 'holding', 'cooling'))),
                          max_wall_k=nullable(number('K')), hold_at_180_s=nullable(number('s')), quantity=nullable(TEXT), instrument=nullable(TEXT),
                           displayed_temperature_k=nullable(number('K')), reference_temperature_k=nullable(number('K')), deviation_k=nullable(number('K')),
                           displayed_fraction=nullable(number('1')), reference_fraction=nullable(number('1')), deviation_fraction=nullable(number('1'))),
                      optional=('phases', 'max_wall_k', 'hold_at_180_s', 'quantity', 'instrument', 'displayed_temperature_k', 'reference_temperature_k', 'deviation_k',
                                'displayed_fraction', 'reference_fraction', 'deviation_fraction'))
RECORD = obj(dict(record_id=TEXT, kind=enum(['htd', 'performance_check']), status=TEXT, started_s=number('s'), finished_s=nullable(number('s')), details=RECORD_DETAILS))

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['base_plinth'], '宿主声明实验室条件：室温、室内相对湿度、大气压、电源电压、CO2与N2进气压力（表压）、噪声种子',
    'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['switch_mains', 'rear_panel'], '主电源开/关（右后侧电源开关）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['switch_mains'], '按一下电源开关（翻转开/关）')
cap('temperature.set', obj(dict(setpoint_k=number('K', *T_SET_RANGE))), obj(dict(setpoint_k=number('K'), warnings=array(TEXT, 0, 4))), dict(setpoint_k=310.15),
    ['touch_screen', 'display_temperature_digits'], '设定腔内温度（22–50 °C，0.1 K步进；应至少高于室温4 K）')
cap('co2.set', obj(dict(setpoint_fraction=number('1', *CO2_RANGE))), obj(dict(setpoint_fraction=number('1'))), dict(setpoint_fraction=0.05), ['touch_screen', 'display_co2_digits'],
    '设定CO2浓度（0.1–20 %，0.1 %步进；降低设定值时需开门放气）')
cap('co2.control', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['touch_screen'], '启用/停用CO2控制（停用时只控温度）')
cap('o2.set', obj(dict(setpoint_fraction=number('1', *O2_RANGE))), obj(dict(setpoint_fraction=number('1'), warnings=array(TEXT, 0, 4))), dict(setpoint_fraction=0.05), ['touch_screen'],
    '设定O2浓度（1–20 %，0.1 %步进，靠N2降低；升高O2需开门）')
cap('o2.control', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['touch_screen'], '启用/停用O2控制（N2）')
cap('gas.pressure_monitor', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['touch_screen'], '启用/停用低气压监测（停用后低压不报警也不停控，可能导致控制超时）')
cap('gas.reset', obj(dict(gas=enum(GASES))), obj(dict(gas=TEXT)), dict(gas='co2'), ['touch_screen'], '控制出错（设定值红色）后点红色X重启CO2或O2控制')
ALARM_SETTINGS = obj(dict(enabled=BOOL, low_k=nullable(number('K')), high_k=nullable(number('K')), low=nullable(number('1')), high=nullable(number('1')),
                           signal_s=nullable(number('s'))), optional=('low_k', 'high_k', 'low', 'high', 'signal_s'))
cap('alarm.configure', obj(dict(kind=enum(ALARM_KINDS), enabled=BOOL, low_k=nullable(number('K', 0.0, 10.0)), high_k=nullable(number('K', 0.0, 10.0)),
                                low=nullable(number('1', 0.0, 1.0)), high=nullable(number('1', 0.0, 1.0)), signal_s=nullable(number('s', 0.0, 90.0))),
                           optional=('low_k', 'high_k', 'low', 'high', 'signal_s')),
    obj(dict(kind=TEXT, settings=ALARM_SETTINGS)), dict(kind='temperature', low_k=0.5, high_k=0.5, enabled=True), ['touch_screen'],
    '设定报警：温度上下限偏差low_k/high_k（K，0.1–5 K）、CO2/O2偏差low/high（分数，0.001–0.05）、RH下限low（分数，0.5–0.95）、门报警时间signal_s（15/30/45/60/90 s）、水位报警开关')
cap('alarm.mute', obj({}), obj(dict(sound=BOOL)), {}, ['touch_screen'], '点提示关闭报警声音（报警在条件恢复前保持）')
cap('htd.start', obj({}), OPERATION, {}, ['touch_screen', 'chamber'], '启动180 °C高温消毒（约14 h：升温、180 °C保持2 h、降到培养温度；需取出样品、倒空水盘、盖上湿度传感器保护罩、关好门）')
cap('contamination.set_status', obj(dict(contaminated=BOOL)), obj(dict(status=TEXT)), dict(contaminated=True), ['touch_screen'], '在信息栏标记“已污染/未污染”（复位后应做高温消毒）')
cap('task.configure', obj(dict(task=enum(list(TASKS)), interval_s=nullable(number('s', 3600.0, 3.2e7)))), obj(dict(task=TEXT, interval_s=nullable(number('s')))),
    dict(task='water_refill', interval_s=7 * 86400.0), ['touch_screen'], '设定周期任务的提醒间隔（null关闭提醒）')
cap('task.complete', obj(dict(task=enum(list(TASKS)))), obj(dict(task=TEXT, completed_s=number('s'))), dict(task='chamber_cleaning'), ['touch_screen'],
    '确认完成一个手动周期任务（腔体清洁、加水；高温消毒与性能检查由各自程序完成）')
cap('performance_check.record', obj(dict(kind=enum(['temperature', 'co2', 'o2']), instrument=TEXT, reference_temperature_k=nullable(number('K', 273.15, 373.15)),
                                         reference_fraction=nullable(number('1', 0.0, 1.0))), optional=('reference_temperature_k', 'reference_fraction')),
    obj(dict(record_id=TEXT, displayed_temperature_k=nullable(number('K')), deviation_k=nullable(number('K')), displayed_fraction=nullable(number('1')),
             deviation_fraction=nullable(number('1')))), dict(kind='temperature', reference_temperature_k=310.1, instrument='probe_1'),
    ['touch_screen', 'inner_door'], '性能检查：在默认值（37 °C / 5 % CO2 / 10 % O2）稳定后输入外部仪器读数（温度reference_temperature_k，CO2/O2用reference_fraction），记录显示值与偏差')
cap('data.export', obj(dict(kind=enum(EXPORT_KINDS), span_s=number('s', 3600.0, 7 * 86400.0), parameters=array(enum(list(CHART_FN)), 1, 4),
                            filters=obj({c: BOOL for c in EVENT_CATS}, optional=tuple(EVENT_CATS))), optional=('span_s', 'parameters', 'filters')),
    obj(dict(file=TEXT, bytes=INT, rows=INT)), dict(kind='chart'), ['usb_port', 'touch_screen'],
    '把事件、曲线、高温消毒记录或性能检查导出到U盘（CSV）；曲线可选span_s（所选时间跨度）与parameters（所选函数），事件可选filters（所选类别）：只导出所选部分（手册8.5.5、8.4.4）')
cap('door.outer', obj(dict(open=BOOL)), obj(dict(target_rad=number('rad'))), dict(open=True), ['outer_door', 'door_handle'], '拉把手打开/关上外门（约1.5 s；关外门前内门须关好）', 'agent', 'robot_skill')
cap('door.latch', obj(dict(open=BOOL)), obj(dict(target_rad=number('rad'))), dict(open=True), ['inner_door_latch'], '内门门闩向上转90°打开/转回水平锁住（需外门打开）', 'agent', 'robot_skill')
cap('door.inner', obj(dict(open=BOOL)), obj(dict(target_rad=number('rad'))), dict(open=True), ['inner_door'], '打开/关上内玻璃门（开门需外门全开、门闩已开）', 'agent', 'robot_skill')
cap('water_tray.fill', obj(dict(volume_m3=number('m3', 1e-5, TRAY_CAPACITY), temperature_k=number('K', 278.15, 333.15))), obj(dict(volume_m3=number('m3'), warnings=array(TEXT, 0, 4))),
    dict(volume_m3=2.0e-3, temperature_k=309.15), ['water_tray'], '往水盘加温热的无菌蒸馏水（总量1.5–2.5 L；双门打开）', 'agent', 'robot_skill')
cap('water_tray.empty', obj(dict(clean=BOOL)), obj(dict(removed_m3=number('m3'))), dict(clean=True), ['water_tray'], '倒空水盘（clean：并用70 %异丙醇清洁）；双门打开', 'agent', 'robot_skill')
cap('rh_sensor.cover', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['rh_sensor'], '给湿度传感器盖上/取下高温消毒保护罩（双门打开）', 'agent', 'robot_skill')
cap('access_port.plug', obj(dict(port=enum(['upper', 'lower']), plugged=BOOL)), obj(dict(port=TEXT, plugged=BOOL)), dict(port='upper', plugged=True), ['access_ports'],
    '后部接入孔（内径25 mm）插上/拔下堵头（拔下时漏气）', 'agent', 'robot_skill')
cap('vessel.provide', VESSEL, obj(dict(vessel_id=TEXT, location=TEXT)), VESSEL_EXAMPLE, ['shelf_1'],
    '在实验台上准备一个培养容器：类型（Thermo Fisher表）、培养基（HiMedia库）、体积、细胞系（Cellosaurus库）与接种数、培养基温度、盖子类型、'
    '初始溶解CO2（as_supplied：按厂家QC pH中值的密封瓶状态，默认；air：在空气中放置平衡；incubator：已在本培养箱当前CO2下平衡）',
    'agent', 'robot_skill')
cap('vessel.place', obj(dict(vessel_id=SID, shelf=integer(1, SHELVES))), obj(dict(vessel_id=TEXT, shelf=INT)), dict(vessel_id='flask_1', shelf=2), ['shelf_1', 'shelf_2', 'shelf_3', 'shelf_4'],
    '把容器放到某层搁板上（双门打开；每层≤10 kg、面积0.522×0.428 m）', 'agent', 'robot_skill')
cap('vessel.remove', obj(dict(vessel_id=SID)), obj(dict(vessel_id=TEXT)), dict(vessel_id='flask_1'), ['shelf_1'], '把容器从培养箱取回实验台（双门打开）', 'agent', 'robot_skill')
cap('vessel.observe', obj(dict(vessel_id=SID)), OBSERVE, dict(vessel_id='flask_1'), ['shelf_1', 'inner_door'], '目视容器：培养基颜色（酚红）、浑浊、液量、盖上凝水（不含细胞计数）', 'agent', 'robot_skill')
cap('vessel.discard', obj(dict(vessel_id=SID)), obj(dict(vessel_id=TEXT)), dict(vessel_id='flask_1'), ['shelf_1'], '丢弃实验台上的容器（从仿真中移除）', 'agent', 'robot_skill')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), ['touch_screen'], '读事件日志', 'agent', 'query')
cap('chart.read', obj(dict(since_s=number('s', 0.0), max_points=integer(1, 1440))), obj(dict(rows=array(CHART_ROW, 0, 1440))), dict(since_s=0.0, max_points=240), ['touch_screen'],
    '读曲线（每分钟：温度、CO2、O2、RH、开门）', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(RECORD, 0, MAX_RECORDS))), {}, ['touch_screen'], '列出高温消毒记录与性能检查记录', 'agent', 'query')
cap('tasks.list', obj({}), obj(dict(tasks=array(obj(dict(task=TEXT, interval_s=nullable(number('s')), last_done_s=nullable(number('s')), due=BOOL)), 0, 8))), {}, ['touch_screen'],
    '列出周期任务与到期提醒', 'agent', 'query')
cap('alarms.list', obj({}), obj(dict(alarms=array(obj(dict(code=TEXT, since_s=number('s'), message_zh=TEXT)), 0, 40), sound=BOOL, relays=array(BOOL, 4, 4))), {}, ['touch_screen'],
    '列出当前报警/错误与BMS继电器状态', 'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(path=TEXT, sha256=TEXT, count=INT)), 1, 8))), {}, ['base_plinth'], '模板库与标定参数的路径与sha256', 'agent', 'query')
cap('vessel.inspect', obj(dict(vessel_id=SID)), INSPECT, dict(vessel_id='flask_1'), ['shelf_1'],
    '评测者读容器真值：温度、pH、溶解CO2/O2、葡萄糖、乳酸、渗透压、活/死细胞、汇合度、生长阶段、细菌', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['base_plinth'], '读守恒账目（电能与热流、CO2/N2用量与交换、水的蒸发/冷凝/排出、葡萄糖/乳酸/分裂数）', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL, vessel_id=nullable(SID)), optional=('vessel_id',)), obj(dict(faults=array(TEXT, 0, 16))),
    dict(fault='gasket_leak', active=True), ['base_plinth', 'chamber'], '评测者故障注入：断电、加热器失效/常通、CO2阀卡闭、门封泄漏、NDIR漂移、某容器染菌；'
    '1.2.0起手册第11章的设备错误（6731FW.107温度传感器超范围、213/313气体控制软件错误、.454水位传感器错误、.203/.303/.403/.453传感器初始化失败、'
    '.204/.304/.404测量值超上/下限、致命错误红屏）：在下一个1 s网格报出错误、相应传感器读数无效、气体控制停止，需重启的错误要关机重开才消失（原因仍在则重开后再报）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), ['base_plinth'], '推进宿主时钟', 'evaluator', 'simulation')
NAME = dict(type='string', minLength=1, maxLength=24)
SECRET = dict(type='string', minLength=4, maxLength=16)
DATE = dict(type='string', pattern='^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
TASK_ID = dict(type='string', minLength=1, maxLength=40)
USER_ID = integer(1, MAX_USERS)
INTERVAL = enum(list(TASK_INTERVALS))
EXPORT_FILE_KINDS = EXPORT_KINDS + ['service_data', 'operation_records', 'user_data']
OPREC = obj(dict(record_id=TEXT, name=TEXT, start_date=TEXT, finish_date=TEXT, created_s=number('s'), user=nullable(TEXT), result=TEXT,
                 events=array(obj(dict(time_s=number('s'), kind=TEXT, code=TEXT, message_zh=TEXT)), 0, 200), initial=FREEOBJ, company=TEXT))
_TS = ['touch_screen']
cap('settings.configure', obj(dict(settings=FREEOBJ)), obj(dict(settings=FREEOBJ)), dict(settings={'display.brightness': 60}), _TS,
    '设备设置（键值）：设备名称/登记号/位置、自动日期时间与时区、网络（远程访问、OPC/REST、DHCP与手动IP）、声音模式与音量、屏幕亮度、主页显示的功能与顺序、'
    'BMS继电器1–4的事件、运行记录公司名、自动屏幕锁定与时间、自动注销时间、登录方式、全体用户权限')
cap('clock.set', obj(dict(date=DATE, time=dict(type='string', pattern='^[0-9]{2}:[0-9]{2}$'))), obj(dict(clock=TEXT)), dict(date='2026-10-19', time='08:30'), _TS,
    '手动设定设备日期与时间（自动日期时间关闭时）')
cap('network.create_certificate', obj({}), obj(dict(fingerprint=TEXT)), {}, _TS, '生成自签名证书（网络设置）')
cap('relay.test', obj({}), obj(dict(until_s=number('s'))), {}, ['touch_screen', 'rear_connectors'], '测试BMS继电器：4个继电器动作15 s')
cap('screen.lock', obj(dict(locked=BOOL)), obj(dict(locked=BOOL)), dict(locked=True), _TS, '锁定触摸屏（清洁屏幕）或解锁（按住Unlock Screen）')
cap('users.enable', obj(dict(login_mode=enum(['pin', 'password']), admin_name=NAME, secret=SECRET)), obj(dict(user_id=INT)),
    dict(login_mode='pin', admin_name='admin', secret='4711'), _TS, '启用用户管理：登录方式与第一个管理员（用户名、PIN/密码）；管理员随即登录')
cap('users.disable', obj(dict(secret=SECRET)), obj(dict(management=BOOL)), dict(secret='4711'), _TS, '停用用户管理（已登录的管理员输入自己的PIN/密码；删除全部账户）')
cap('user.login', obj(dict(user_id=USER_ID, secret=SECRET)), obj(dict(user_id=INT, role=TEXT)), dict(user_id=1, secret='4711'), _TS,
    '用户登录（用户ID与PIN/密码）')
cap('user.logout', obj({}), obj(dict(logged_in=BOOL)), {}, _TS, '注销当前用户')
cap('user.create', obj(dict(name=NAME)), obj(dict(user_id=INT, secret=TEXT)), dict(name='operator'), _TS,
    '管理员新建用户账户（受限用户组；设备生成初始PIN/密码）')
cap('user.update', obj(dict(user_id=USER_ID, full_name=dict(type='string', maxLength=40), email=dict(type='string', maxLength=60), role=enum(ROLES),
                            new_user_id=USER_ID), optional=('full_name', 'email', 'role', 'new_user_id')),
    obj(dict(user=FREEOBJ)), dict(user_id=2, role='user'), _TS, '编辑用户账户：全名、电子邮件（本人或管理员）；用户ID与角色（管理员）')
cap('user.delete', obj(dict(user_id=USER_ID)), obj(dict(user_id=INT)), dict(user_id=2), _TS, '管理员删除用户账户')
cap('user.reset_secret', obj(dict(user_id=USER_ID)), obj(dict(user_id=INT, secret=TEXT)), dict(user_id=2), _TS, '管理员重置用户的PIN/密码（设备生成新的）')
cap('user.change_secret', obj(dict(current=SECRET, new=SECRET)), obj(dict(changed=BOOL)), dict(current='4711', new='1234'), _TS, '已登录用户修改自己的PIN/密码')
cap('message.acknowledge', obj(dict(all=BOOL)), obj(dict(acknowledged=array(TEXT, 0, 200))), dict(all=False), _TS,
    '确认信息栏的当前消息（all：确认全部未确认的通知）')
cap('recurring_task.edit', obj(dict(task=TASK_ID, warn=BOOL, interval=nullable(INTERVAL), reminder_days=integer(0, 30))),
    obj(dict(task=TEXT, interval_s=nullable(number('s')))), dict(task='water_refill', warn=True, interval='weekly', reminder_days=0), _TS,
    '编辑周期任务：逾期提醒开/关、间隔（每年/每月/每周/每天）、提前提醒天数')
cap('recurring_task.delete', obj(dict(task=TASK_ID)), obj(dict(task=TEXT)), dict(task='custom_1'), _TS, '删除自定义周期任务')
cap('recurring_task.confirm', obj(dict(task=TASK_ID)), obj(dict(task=TEXT, completed_s=number('s'))), dict(task='custom_1'), _TS,
    '确认执行了一个手动周期任务（腔体清洁、加水或自定义任务）')
cap('operation_record.create', obj(dict(name=NAME, start_date=DATE, finish_date=DATE)), obj(dict(record_id=TEXT)),
    dict(name='Exp 1', start_date='2026-01-01', finish_date='2026-01-02'), _TS, '新建运行记录（实验名称、开始与结束日期；记录初始参数与期间的事件）')
cap('operation_records.list', obj({}), obj(dict(records=array(OPREC, 0, MAX_OP_RECORDS))), {}, _TS, '列出运行记录（结果按期间的警告、报警与错误）', 'agent', 'query')
cap('export.files', obj(dict(kinds=array(enum(EXPORT_FILE_KINDS), 1, len(EXPORT_FILE_KINDS)))),
    obj(dict(files=array(obj(dict(kind=TEXT, file=TEXT, bytes=INT, rows=INT)), 1, len(EXPORT_FILE_KINDS)))), dict(kinds=['chart', 'events']), ['usb_port', 'touch_screen'],
    '导出到U盘（Menu > Export）：曲线、事件、高温消毒与性能检查记录、服务数据、运行记录（PDF）、用户数据（TXT）')
cap('recurring_task.define', obj(dict(name=NAME, warn=BOOL, interval=nullable(INTERVAL), reminder_days=integer(0, 30))), obj(dict(task=TEXT)),
    dict(name='CO2 cylinder check', warn=True, interval='monthly', reminder_days=0), _TS, '新建自定义周期任务（名称、逾期提醒、间隔、提前提醒天数；最多4个）')
UNSUPPORTED = {
    'visionize.connect': 'VisioNize Lab Suite云连接、短信/邮件通知未建模',
    'door_segment.open': '分段内门（4/8段）为选件，本配置未装',
    'service.calibrate': '传感器校准调整只能由厂家服务进行（手册§13.4.6）',
    'hardware.connect': '没有真实CellXpert的硬件接口',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['base_plinth'], _why, 'agent', 'device_command')


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.eppendorf_cellxpertc170i (tests, Matterix bridge)
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
DEVICE_CAPS = set(CAPS)
PANEL_SPEC = _panel_ui.load_spec(ASSET_DIR / 'panel_ui.json', capabilities=DEVICE_CAPS)
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC)                    # panel.touch / switch / capture / frame: part of the command set
CAPS.update(PANEL_CAPS)
_GESTURES = [k for k in PANEL_CAPS if k not in ('panel.capture', 'panel.frame')]
DEVICE_FAULT_ZH = {   # 1.2.0: runtime messages of the device errors of manual chapter 11 (cause and remedy as in the manual)
    'E_6731FW107': '6731FW.107 温度传感器超出范围：检查适应时间与环境条件（冷室<10 °C须适应至少12 h；传感器损坏联系Eppendorf）；无有效温度读数时不加热',
    'E_6731FW213': '6731FW213 CO2控制软件错误：检查气源连接与泄漏、接入孔是否关好，重启培养箱', 'E_6731FW313': '6731FW313 O2控制软件错误：检查气源连接与泄漏、接入孔是否关好，重启培养箱',
    'E_6731FW454': '6731FW.454 水位传感器错误（测量值超出范围）：重启培养箱', 'E_6731FW203': '传感器错误：CO2传感器开机初始化失败（6731FW.203），重启培养箱',
    'E_6731FW303': '传感器错误：O2传感器开机初始化失败（6731FW.303），重启培养箱', 'E_6731FW403': '传感器错误：RH传感器开机初始化失败（6731FW.403），重启培养箱',
    'E_6731FW453': '传感器错误：水位传感器开机初始化失败（6731FW.453），重启培养箱',
    'E_6731FW204H': 'CO2测量值超上限（6731FW.204），重启培养箱', 'E_6731FW204L': 'CO2测量值超下限（6731FW.204），重启培养箱',
    'E_6731FW304H': 'O2测量值超上限（6731FW.304），重启培养箱', 'E_6731FW304L': 'O2测量值超下限（6731FW.304），重启培养箱',
    'E_6731FW404H': '水位测量值超上限（6731FW.404），重启培养箱', 'E_6731FW404L': '水位测量值超下限（6731FW.404），重启培养箱'}
EVENT_EN = {   # English texts of the screen (event log and information bar); the runtime keeps its Chinese messages for the API
    'POWER_ON': 'Device switched on', 'POWER_OFF': 'Device switched off', 'OUTER_OPEN': 'Outer door opened', 'OUTER_CLOSED': 'Outer door closed',
    'TEMPERATURE_SET': 'Temperature set-point changed', 'CO2_SET': 'CO2 Concentration set-point changed', 'O2_SET': 'O2 Concentration set-point changed',
    'CO2_CONTROL': 'CO2 control switched', 'O2_CONTROL': 'O2 control switched', 'CO2_RESET': 'CO2 control restarted', 'O2_RESET': 'O2 control restarted',
    'ALARM_CONFIG': 'Alarm parameter changed', 'DOOR_OPEN': 'Door longer open than the signal time', 'DOOR_LONG': '6731FW.50 Long door opening. Close the door.',
    'TEMP_LOW': 'Temperature below alarm level', 'TEMP_HIGH': 'Temperature above alarm level', 'CO2_LOW': 'CO2 Concentration below alarm level',
    'CO2_HIGH': 'CO2 Concentration above alarm level', 'O2_LOW': 'O2 Concentration below alarm level', 'O2_HIGH': 'O2 Concentration above alarm level',
    'RH_LOW': 'Relative humidity below alarm level', 'WATER_LOW': 'Water level below 0.5 L. Refill the water tray.',
    'CO2_PRESSURE_WARNING': 'CO2 pressure below warning level of 0.04 MPa', 'CO2_PRESSURE_LOW': 'CO2 pressure below alarm level of 0.02 MPa',
    'CO2_PRESSURE_HIGH': 'CO2 pressure above alarm level of 0.18 MPa', 'N2_PRESSURE_WARNING': 'N2 pressure below warning level of 0.04 MPa',
    'N2_PRESSURE_LOW': 'N2 pressure below alarm level of 0.02 MPa', 'N2_PRESSURE_HIGH': 'N2 pressure above alarm level of 0.18 MPa',
    'E_6731FW108': '6731FW.108 Setpoint not reached, cooldown too slow', 'E_6731FW110': '6731FW.110 Temperature not reached in time',
    'E_6731FW111': '6731FW.111 Over-temperature detected by temperature limiter', 'E_6731W112': '6731W.112 Subnormal temperature detected by temperature limiter',
    'E_6731FW214': '6731FW214 CO2 control timeout', 'E_6731FW314': '6731FW314 O2 control timeout',
    'E_6731FW202': '6731FW.202 CO2 sensor off due to high temperature', 'E_6731FW302': '6731FW.302 O2 sensor off due to high temperature',
    'E_6731FW402': '6731FW.402 RH sensor off due to high temperature', 'E_6731FW452': '6731FW.452 Water level sensor off due to high temperature',
    'E_6731FW751': '6731FW.751 HTD interrupted due to power loss', 'E_6731FW753': '6731FW.753 Door opened during HTD',
    'E_6731FW107': '6731FW.107 Temperature sensor is out of range. Check acclimatization and ambient conditions.',
    'E_6731FW213': '6731FW213 CO2 control software error', 'E_6731FW313': '6731FW313 O2 control software error',
    'E_6731FW454': '6731FW.454 Water level sensor error', 'E_6731FW203': 'Sensor error: CO2 (6731FW.203)', 'E_6731FW303': 'Sensor error: O2 (6731FW.303)',
    'E_6731FW403': 'Sensor error: RH (6731FW.403)', 'E_6731FW453': 'Sensor error: Water level (6731FW.453)',
    'E_6731FW204H': 'CO2 value over range (6731FW.204)', 'E_6731FW204L': 'CO2 value under range (6731FW.204)',
    'E_6731FW304H': 'O2 value over range (6731FW.304)', 'E_6731FW304L': 'O2 value under range (6731FW.304)',
    'E_6731FW404H': 'Water level value over range (6731FW.404)', 'E_6731FW404L': 'Water level value under range (6731FW.404)',
    'HTD_START': 'High-temperature disinfection started', 'HTD_HOLD': 'Disinfection temperature 180 C reached', 'HTD_COOL': 'Cooling down after disinfection',
    'HTD_DONE': 'High-temperature disinfection successful', 'HTD_FAILED': 'High-temperature disinfection failed', 'HTD_CANCELLED': 'High-temperature disinfection cancelled',
    'CONTAMINATION_STATUS': 'Contamination status changed', 'EXPORTED': 'Data exported to USB', 'PERFORMANCE_CHECK': 'Performance check recorded',
    'RELAY_TEST': 'Relay test (15 s)', 'TASK_DEFINED': 'Recurring task added', 'TASK_DONE': 'Recurring task executed', 'WATER_EMPTIED': 'Water tray emptied',
    'WATER_FILLED': 'Water tray filled', 'CERTIFICATE': 'Self signed certificate created', 'CLOCK_SET': 'Date and time changed', 'DEVICE_SETTINGS': 'Settings changed',
    'LOGIN': 'User logged in', 'LOGOUT': 'User logged out', 'USER_CREATED': 'User account created', 'USER_DELETED': 'User account deleted',
    'USER_MANAGEMENT_ON': 'User Management enabled', 'USER_MANAGEMENT_OFF': 'User Management disabled', 'USER_SECRET_RESET': 'Password/PIN reset',
    'ACKNOWLEDGED': 'Message acknowledged'}


def _text_en(code):
    if code.startswith('TASK_DUE:'):
        return 'Task due: ' + TASK_NAMES.get(code[9:], code[9:])
    if code.endswith('_CLEARED'):
        return EVENT_EN.get(code[:-8], code[:-8]) + ' - cleared'
    return EVENT_EN.get(code, code.replace('_', ' ').capitalize())


def _hook_home_list(panel, ctx, s):
    """Home Screen Configuration: the function list after a switch (toggle) or an arrow key (up / down) on function s['f']."""
    cur = list(panel.host.dev['home.functions'])
    f = s['f']
    if s['op'] == 'toggle':
        if f in cur:
            return [x for x in cur if x != f]
        return [x for x in HOME_FUNCTIONS if x in cur or x == f] if not cur else cur + [f]
    if f not in cur:
        return cur
    i = cur.index(f)
    j = i - 1 if s['op'] == 'up' else i + 1
    if 0 <= j < len(cur):
        cur[i], cur[j] = cur[j], cur[i]
    return cur


def _hook_home_pos(panel, ctx, s):
    cur = panel.host.dev['home.functions']
    return f'{cur.index(s["f"]) + 1}.' if s['f'] in cur else '-'


def _hook_text(panel, ctx, s):
    return ''.join('' if v is None else str(v) for v in (panel._src(p, ctx) for p in s['parts']))


def _hook_chart_pick(panel, ctx, a):
    """Charts: the time under the finger (x across the plot rect, the shown span ending now) and the stored values nearest to it."""
    h = panel.host
    x0, _, ww, _ = a['rect']
    span = float(panel.ui['vars'].get('chart_span') or 24.0)
    t = h.t - 3600.0 * span * (1.0 - min(max((ctx['ev']['x'] - x0) / max(1.0, ww - 1.0), 0.0), 1.0))
    row = min(h.chart, key=lambda r: abs(r['time_s'] - t)) if h.chart else None
    fmt = lambda v, sc, off, u: '-' if v is None else f'{v * sc + off:.1f} {u}'      # noqa: E731
    panel.ui['vars']['chart_pick'] = None if row is None else dict(
        time=h._clock_text(h._clock_s() - h.t + row['time_s'])[:16], temperature=fmt(row['temperature_k'], 1.0, -273.15, '°C'),
        co2=fmt(row['co2_fraction'], 100.0, 0.0, '%'), o2=fmt(row['o2_fraction'], 100.0, 0.0, '%'), rh=fmt(row['rh'], 100.0, 0.0, '%'))
    return row is not None


def _hook_rec_visible(panel, ctx, s):
    """Operation Records filters (8.8.3): Result, From date (the record starts on or after it), Until date (it finishes on or before it),
    Operation name (part of the name, any case) and User; empty filters let every record through."""
    v, r = panel.ui['vars'], ctx.get('row') or {}
    name = (v.get('rec_name') or '').strip().lower()
    return ((v.get('rec_filter') is None or r.get('result') == v['rec_filter']) and (not v.get('rec_from') or r.get('start_date', '') >= v['rec_from'])
            and (not v.get('rec_until') or r.get('finish_date', '') <= v['rec_until']) and (not name or name in r.get('name', '').lower())
            and (not v.get('rec_user') or r.get('user') == v['rec_user']))


PANEL_HOOKS = {'cx_home_list': _hook_home_list, 'cx_home_pos': _hook_home_pos, 'cx_text': _hook_text, 'cx_chart_pick': _hook_chart_pick,
               'cx_rec_visible': _hook_rec_visible}

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


_PN = lambda u: nullable(number(u))                                             # noqa: E731
slot('clock.time_s', number('s'), 's', 'derived', 'base_plinth', '仿真时钟（物理量在1 s网格上积分）')
slot('plant.configured', BOOL, '1', 'setpoint', 'base_plinth', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'derived', 'switch_mains', '整机通电（开关打开且市电正常）')
slot('setpoint.temperature_k', number('K'), 'K', 'setpoint', 'touch_screen', '温度设定值')
slot('setpoint.co2_fraction', number('1'), '1', 'setpoint', 'touch_screen', 'CO2设定值（体积分数，按101.325 kPa的分压表示）')
slot('setpoint.o2_fraction', number('1'), '1', 'setpoint', 'touch_screen', 'O2设定值（体积分数）')
slot('control.co2_enabled', BOOL, '1', 'setpoint', 'touch_screen', 'CO2控制启用')
slot('control.o2_enabled', BOOL, '1', 'setpoint', 'touch_screen', 'O2控制启用')
slot('gas.pressure_monitor_enabled', BOOL, '1', 'setpoint', 'touch_screen', '低气压监测启用')
slot('sensor.temperature_k', _PN('K'), 'K', 'sensor', 'chamber_sensors', '腔内温度传感器读数（断电时null）', reference='腔内空气温度，一阶滞后')
slot('sensor.co2_fraction', _PN('1'), '1', 'sensor', 'chamber_sensors', 'NDIR CO2读数（初始化30 min内与断电时null）', reference='CO2分压/101.325 kPa，未做气压补偿')
slot('sensor.o2_fraction', _PN('1'), '1', 'sensor', 'chamber_sensors', 'ZrO2 O2读数（初始化10 min内null）', reference='O2分压/101.325 kPa')
slot('sensor.rh', _PN('1'), '1', 'sensor', 'rh_sensor', '湿度传感器读数（相对湿度分数；盖着保护罩时响应极慢）', reference='对腔内空气温度的相对湿度')
slot('sensor.water_low', nullable(BOOL), '1', 'sensor', 'water_tray', '水位传感器：水盘水量低于0.5 L（水温高于腔温1 K以上也会误报低）')
slot('sensor.water_present', nullable(BOOL), '1', 'sensor', 'water_tray', '水位传感器检测到水盘里有水（高于约50 mL；高温消毒联锁）；断电时为空')
slot('sensor.co2_supply_pa', _PN('Pa'), 'Pa', 'sensor', 'rear_connectors', 'CO2进气压力（表压）', reference='相对大气压的表压')
slot('sensor.n2_supply_pa', _PN('Pa'), 'Pa', 'sensor', 'rear_connectors', 'N2进气压力（表压）', reference='相对大气压的表压')
slot('status.co2_initializing', BOOL, '1', 'derived', 'touch_screen', 'CO2传感器初始化中（不显示过程值，不能改设定）')
slot('status.o2_initializing', BOOL, '1', 'derived', 'touch_screen', 'O2传感器初始化中')
slot('status.co2_control_failed', BOOL, '1', 'derived', 'touch_screen', 'CO2控制出错（设定值显示红色，需gas.reset）')
slot('status.o2_control_failed', BOOL, '1', 'derived', 'touch_screen', 'O2控制出错')
slot('status.heating_shutdown', BOOL, '1', 'derived', 'touch_screen', '温度限制器动作（超温+10 K或欠温−5 K），加热停止，需重启')
slot('status.fatal_error', BOOL, '1', 'derived', 'touch_screen', '致命错误（软件错误）：红屏Fatal Error – Restart the device，控制输出全关，关机重开才消失（1.2.0）')
slot('display.temperature_c', _PN('°C'), '°C', 'derived', 'display_temperature_digits', '屏幕显示的腔温（面板显示值，°C，0.1位；场景仿真数字镜像）', reference='面板显示值例外：摄氏度')
slot('display.co2_percent', _PN('%'), '%', 'derived', 'display_co2_digits', '屏幕显示的CO2（面板显示值，%，0.1位；场景仿真数字镜像）', reference='面板显示值例外：百分数')
slot('door.outer_angle_rad', number('rad'), 'rad', 'derived', 'outer_door', '外门开角（0关闭，1.745全开；场景门铰链镜像）', reference='绕−Z（左侧铰链），正值向操作者方向打开')
slot('door.inner_angle_rad', number('rad'), 'rad', 'derived', 'inner_door', '内玻璃门开角（场景镜像）', reference='绕−Z（左侧铰链）')
slot('door.latch_angle_rad', number('rad'), 'rad', 'derived', 'inner_door_latch', '内门门闩转角（0水平锁住，π/2向上打开；场景镜像）', reference='绕+Y')
slot('door.outer_state', enum(['closed', 'opening', 'open', 'closing']), '1', 'derived', 'outer_door', '外门状态')
slot('door.inner_state', enum(['closed', 'opening', 'open', 'closing']), '1', 'derived', 'inner_door', '内门状态')
slot('door.latched', BOOL, '1', 'derived', 'inner_door_latch', '内门关好并锁住（门闩水平）')
slot('door.outer_open_s', number('s'), 's', 'derived', 'outer_door', '外门本次打开的时长（关着为0）')
slot('water_tray.volume_m3', number('m3'), 'm3', 'process_truth', 'water_tray', '水盘水量真值（真机只有低水位传感器，agent读sensor.water_low）', 'evaluator')
slot('water_tray.level_m', number('m'), 'm', 'process_truth', 'water_surface', '水盘水面高度真值（场景水面关节镜像）', 'evaluator', reference='水盘底面以上')
slot('alarms.active', array(TEXT, 0, 40), '1', 'derived', 'touch_screen', '当前报警/错误代码')
slot('alarm.sound', BOOL, '1', 'derived', 'touch_screen', '报警声音开着')
slot('bms.relays', array(BOOL, 4, 4), '1', 'derived', 'rear_connectors', 'BMS继电器1–4（温度、CO2、O2、水位报警；关机或断电时4个继电器都处于报警位）')
slot('htd.phase', enum(['idle', 'heating', 'holding', 'cooling', 'post_htd_cooling']), '1', 'derived', 'touch_screen', '高温消毒阶段')
slot('htd.remaining_s', _PN('s'), 's', 'derived', 'touch_screen', '高温消毒估计剩余时间')
slot('htd.last_result', nullable(enum(['successful', 'failed', 'cancelled'])), '1', 'derived', 'touch_screen', '最近一次高温消毒结果')
slot('contamination.status', enum(['not_contaminated', 'contaminated']), '1', 'setpoint', 'touch_screen', '信息栏的污染状态标记')
slot('tasks.due', array(TEXT, 0, 8), '1', 'derived', 'touch_screen', '到期的周期任务')
slot('vessels.in_chamber', array(TEXT, 0, MAX_VESSELS), '1', 'derived', 'shelf_1', '培养箱内的容器（容器ID@搁板层）')
slot('vessels.on_bench', array(TEXT, 0, MAX_VESSELS), '1', 'derived', 'shelf_1', '实验台上的容器')
slot('rh_sensor.cover_on', BOOL, '1', 'derived', 'rh_sensor', '湿度传感器保护罩已盖上')
slot('access_ports.unplugged', array(TEXT, 0, 2), '1', 'derived', 'access_ports', '未插堵头的接入孔')
slot('operation.kind', nullable(TEXT), '1', 'derived', 'touch_screen', '进行中的操作（htd）')
slot('operation.progress', number('1'), '1', 'derived', 'touch_screen', '操作进度0–1（按估计总时长）')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'touch_screen', '进行中的操作属于另一个agent')
slot('device.settings', FREEOBJ, '1', 'setpoint', 'touch_screen', '设备设置（settings.configure的键值：名称、日期时间、网络、声音、亮度、主页功能、继电器事件、屏幕锁定、用户管理）')
slot('device.clock', TEXT, '1', 'derived', 'touch_screen', '设备本地日期时间（YYYY-MM-DD HH:MM:SS，按时区与手动设定）')
slot('screen.locked', BOOL, '1', 'derived', 'touch_screen', '触摸屏已锁定（Screen Lock或自动屏幕锁定）')
slot('users.state', FREEOBJ, '1', 'derived', 'touch_screen', '用户管理：是否启用、登录方式、当前用户与角色、权限表、账户列表（不含PIN/密码）')
slot('messages.unacknowledged', array(FREEOBJ, 0, 200), '1', 'derived', 'touch_screen', '信息栏未确认的消息（报警、错误、警告、到期任务；最新在前）')
slot('truth.air_temperature_k', number('K'), 'K', 'process_truth', 'chamber', '腔内空气温度真值', 'evaluator')
slot('truth.wall_temperature_k', number('K'), 'K', 'process_truth', 'chamber', '内腔壁（含搁板）温度真值', 'evaluator')
slot('truth.water_temperature_k', number('K'), 'K', 'process_truth', 'water_tray', '水盘水温真值', 'evaluator')
slot('truth.co2_fraction', number('1'), '1', 'process_truth', 'chamber', '腔内CO2摩尔分数真值', 'evaluator')
slot('truth.o2_fraction', number('1'), '1', 'process_truth', 'chamber', '腔内O2摩尔分数真值', 'evaluator')
slot('truth.rh', number('1'), '1', 'process_truth', 'chamber', '腔内相对湿度真值', 'evaluator')
slot('truth.condensate_kg', number('kg'), 'kg', 'process_truth', 'inner_door', '内玻璃门上的凝结水', 'evaluator')
slot('truth.heater_power_w', number('W'), 'W', 'process_truth', 'chamber', '加热功率', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'base_plinth', '注入的故障', 'evaluator')


# ---------------------------------------------------------------- runtime
class EppendorfCellxpertC170iRuntime:
    """CellXpert C170i: chamber heat / gas / humidity physics, control, alarms, HTD, cultures (SI units)."""

    def __init__(self, instance_id='incubator_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.tick = 0
        self.revision = 0
        self.m = dict(MODEL)
        self.plant = None
        self.switch_on = False
        self.power = False
        self.setp = dict(T=310.15, co2=0.05, o2=0.20)
        self.ctrl = dict(co2=True, o2=False, monitor=True)
        self.alarm_cfg = copy.deepcopy(ALARM_DEFAULTS)
        self.ch = None
        self.door = dict(outer=dict(a0=0.0, t0=0.0, target=0.0), inner=dict(a0=0.0, t0=0.0, target=0.0), latch=dict(a0=0.0, t0=0.0, target=0.0),
                         open_flag=False, opened_at=None, closed_at=-1e9)
        self.tray = dict(V=0.0, T=None, cleaned=False)
        self.rh_cover = False
        self.ports = dict(upper=True, lower=True)
        self.sens = dict(T=None, c=None, o=None, rh=None)
        self.status = dict(co2_init_until=None, o2_init_until=None, co2_failed=False, o2_failed=False, shutdown=False, sensors_hot=False, co2_gate=False,
                           reached_setpoint=False)
        self.dev_errors = set()                                               # 1.2.0: device errors seen since the last switch-on (restart-type ones stay until a restart)
        self.pid = dict(I_T=0.0, I_c=0.0, I_o=0.0)
        self.u = dict(h=0.0, c=0.0, n=0.0)
        self.timers = dict(co2_off=0.0, o2_off=0.0, t_high=0.0, subnormal=0.0)
        self.pause = dict(temperature=0.0, co2=0.0, o2=0.0, rh=0.0, water=0.0)
        self.alarms = {}
        self.sound = False
        self.htd = dict(phase='idle', last=None, t_phase=0.0, hold_s=0.0, max_k=0.0)
        self.contamination = 'not_contaminated'
        self.tasks = {k: dict(interval_s=v['interval_s'], last_done_s=None, warn=True, reminder_s=0.0, custom=False, name=TASK_NAMES[k]) for k, v in TASKS.items()}
        self.vessels = {}
        self.vapour_src = 0.0                        # mol/s from the vessels (culture grid)
        self.faults = set()
        self.vessel_faults = set()
        self.events, self.events_dropped = [], 0
        self.chart = []
        self.records = {}
        self.operations = {}
        self.op = None
        self.seq = dict(op=0, rec=0)
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self._principals, self._requests, self._owners = {}, {}, {}
        self._caller = None
        self.dev = copy.deepcopy(SETTINGS_DEFAULT)                              # device settings behind the touch screen (settings.configure)
        self.certificate = None
        self.clock_offset = 0.0                                                 # manual date / time (clock.set) relative to the host clock
        self.screen = dict(locked=False, touch_t=0.0)                           # Screen Lock and the last touch (automatic lock / logout)
        self.users = dict(management=False, accounts={}, logged=None)
        self.msgs = []                                                          # unacknowledged messages of the information bar
        self.acked_due = set()                                                  # task warnings acknowledged until the task is done again
        self.relay_test_until = -1.0
        self.op_records = []
        self.recent = dict(temperature=[], co2=[], o2=[])                       # last used set points (fine adjustment history)
        self.contamination_changed_s = None
        self.power_epoch = 0
        self.seq.update(user=0, task=0, oprec=0, secret=0)
        self.last_credentials = None                                            # shown in the User credentials window
        self.panel_capture_dir = None                                           # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
        self.panel = _panel_ui.PanelUI(PANEL_SPEC, self, hooks=PANEL_HOOKS)

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
        return EppendorfCellxpertC170iSession(self, name)

    def _event(self, kind, code, message, ref=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _ref=ref))

    def _seed(self):
        return self.plant['noise_seed'] if self.plant else 0

    def _room(self):
        return self.plant['room_k'] if self.plant else 295.15

    def _P(self):
        return self.plant['ambient_pressure_pa'] if self.plant else P_REF

    def _ambient_x(self):
        P, Tr = self._P(), self._room()
        xw = min(0.2, self.plant['room_rh'] * p_sat(Tr) / P) if self.plant else 0.01
        return dict(c=X_CO2_DRY_AIR * (1 - xw), o=X_O2_DRY_AIR * (1 - xw), w=xw)

    def _n_total(self, Ta):
        return self._P() * V_CHAMBER / (R * Ta)

    def _xs(self):
        c = self.ch
        n = c['nc'] + c['no'] + c['nw'] + c['nr']
        return c['nc'] / n, c['no'] / n, c['nw'] / n

    def _rh_true(self):
        _, _, xw = self._xs()
        return xw * self._P() / p_sat(self.ch['Ta'])

    def _mains_ok(self):
        v = self.plant['mains_voltage_v']
        return 'power_loss' not in self.faults and (90.0 <= v <= 139.7 or 198.0 <= v <= 264.0)

    def _ang(self, k, t=None):
        """Door / latch angle at time t: an analytic ramp at the swing rate from the angle and time of the last command (smooth for the scene,
        identical for any split of the host steps)."""
        d = self.door[k]
        t = self.t if t is None else t
        run = (LATCH_RATE if k == 'latch' else DOOR_RATE) * max(0.0, t - d['t0'])
        return min(d['target'], d['a0'] + run) if d['target'] >= d['a0'] else max(d['target'], d['a0'] - run)

    def _set_door(self, k, target):
        self.door[k] = dict(a0=self._ang(k), t0=self.t, target=target)

    def _cross_time(self, k, level):
        d = self.door[k]
        return d['t0'] + abs(level - d['a0']) / (LATCH_RATE if k == 'latch' else DOOR_RATE)

    def _doors_open(self):
        return self._ang('outer') > 0.02

    def _door_state(self, which):
        a, tgt = self._ang(which), self.door[which]['target']
        if abs(a - tgt) > 1e-9:
            return 'opening' if tgt > a else 'closing'
        return 'open' if a > 0.02 else 'closed'

    def _latched(self):
        return self._ang('inner') <= 1e-9 and self._ang('latch') <= 1e-9

    def _in_chamber(self):
        return sorted(k for k, v in self.vessels.items() if v['location'] != 'bench')

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _need_no_htd(self):
        if self.htd['phase'] in ('heating', 'holding', 'cooling'):
            raise ActionRejected('HTD_RUNNING')

    def _need_cool_chamber(self):
        if self.htd['phase'] != 'idle':                                        # HTD running or the hot chamber after an aborted HTD (burn / scald risk)
            raise ActionRejected('HTD_RUNNING')

    def _need_doors_open(self):
        self._need_plant()
        if self._door_state('outer') != 'open' or self._door_state('inner') != 'open':
            raise ActionRejected('DOORS_NOT_OPEN')

    # ---------------------------------------------------------------- read
    def _display(self):
        if not self.power or self.sens['T'] is None:
            return None, None
        dt = round(self.sens['T'] - 273.15, 1)
        dc = round(100.0 * self.sens['c'], 1) if self.sens['c'] is not None else None
        return dt, dc

    def read(self, visibility='agent'):
        c = self.ch
        dt, dc = self._display()
        xs = self._xs() if c else (None, None, None)
        op = self.op
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power,
            'setpoint.temperature_k': self.setp['T'], 'setpoint.co2_fraction': self.setp['co2'], 'setpoint.o2_fraction': self.setp['o2'],
            'control.co2_enabled': self.ctrl['co2'], 'control.o2_enabled': self.ctrl['o2'], 'gas.pressure_monitor_enabled': self.ctrl['monitor'],
            'sensor.temperature_k': self.sens['T'] if self.power else None, 'sensor.co2_fraction': self.sens['c'] if self.power else None,
            'sensor.o2_fraction': self.sens['o'] if self.power else None, 'sensor.rh': self.sens['rh'] if self.power else None,
            'sensor.water_low': self._water_low() if self.power and not self.status['sensors_hot'] and not self._dead('wl') else None,
            'sensor.water_present': bool(self.tray['V'] > 5e-5) if self.power else None,
            'sensor.co2_supply_pa': self.plant['co2_supply_pa'] if self.power else None, 'sensor.n2_supply_pa': self.plant['n2_supply_pa'] if self.power else None,
            'status.co2_initializing': self._initializing('co2'), 'status.o2_initializing': self._initializing('o2'),
            'status.co2_control_failed': self.status['co2_failed'], 'status.o2_control_failed': self.status['o2_failed'], 'status.heating_shutdown': self.status['shutdown'],
            'status.fatal_error': bool(self.power and 'FATAL' in self.dev_errors),
            'display.temperature_c': dt, 'display.co2_percent': dc,
            'door.outer_angle_rad': self._ang('outer'), 'door.inner_angle_rad': self._ang('inner'), 'door.latch_angle_rad': self._ang('latch'),
            'door.outer_state': self._door_state('outer'), 'door.inner_state': self._door_state('inner'), 'door.latched': self._latched(),
            'door.outer_open_s': (self.t - self.door['opened_at']) if self.door['open_flag'] else 0.0,
            'water_tray.volume_m3': self.tray['V'], 'water_tray.level_m': self.tray['V'] / TRAY_AREA,
            'alarms.active': sorted(self.alarms), 'alarm.sound': self.sound, 'bms.relays': self._relays(),
            'htd.phase': self.htd['phase'], 'htd.remaining_s': self._htd_remaining(), 'htd.last_result': self.htd['last'],
            'contamination.status': self.contamination, 'tasks.due': self._tasks_due(),
            'vessels.in_chamber': [f'{k}@{self.vessels[k]["location"]}' for k in self._in_chamber()],
            'vessels.on_bench': sorted(k for k, v in self.vessels.items() if v['location'] == 'bench'),
            'rh_sensor.cover_on': self.rh_cover, 'access_ports.unplugged': sorted(k for k, v in self.ports.items() if not v),
            'operation.kind': op['kind'] if op else None, 'operation.progress': self._progress(), 'operation.started_by_other_agent': False,
            'device.settings': dict(copy.deepcopy(self.dev), **{'network.certificate': self.certificate}), 'device.clock': self._clock_text(),
            'screen.locked': self._screen_locked(), 'users.state': self._users_state(), 'messages.unacknowledged': self._messages(),
            'truth.air_temperature_k': c['Ta'] if c else self._room(), 'truth.wall_temperature_k': c['Tw'] if c else self._room(),
            'truth.water_temperature_k': self.tray['T'] if self.tray['T'] is not None else (c['Tw'] if c else self._room()),
            'truth.co2_fraction': xs[0] if c else X_CO2_DRY_AIR, 'truth.o2_fraction': xs[1] if c else X_O2_DRY_AIR, 'truth.rh': self._rh_true() if c else 0.0,
            'truth.condensate_kg': c['cond'] * M_WATER if c else 0.0, 'truth.heater_power_w': self.u['h'] * self.m['heater_max_w'],
            'truth.faults': sorted(self.faults) + sorted(f'vessel_contamination:{v}' for v in self.vessel_faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _progress(self):
        if not self.op:
            return 0.0
        rem = self._htd_remaining() or 0.0
        el = self.t - self.op['start']
        return float(min(1.0, max(0.0, el / max(el + rem, 1e-9))))

    def _htd_remaining(self):
        ph = self.htd['phase']
        if ph not in ('heating', 'holding', 'cooling'):
            return None
        c = self.ch
        cool = self._cool_estimate(c['Tw'] if ph != 'heating' else HTD_WALL_K)
        if ph == 'heating':
            P, ua = self.m['heater_max_w'], self.m['ua_wall_w_k']
            Tr = self._room()
            a = max(P - ua * (c['Tw'] - Tr), 1.0)
            b = max(P - ua * (HTD_WALL_K - Tr), 1.0)
            heat = self.m['c_wall_j_k'] / ua * math.log(a / b) if HTD_WALL_K > c['Tw'] else 0.0
            return heat + HTD_HOLD_S + cool
        if ph == 'holding':
            return max(0.0, HTD_HOLD_S - self.htd['hold_s']) + cool
        return cool

    def _htd_floor_k(self):
        m = self.m
        return self._room() + m['internal_heat_k'] * m['ua_wall_w_k'] / (m['ua_wall_w_k'] + m['g_door_w_k'])

    def _htd_end_k(self):
        """Wall temperature that ends the HTD cool-down: 0.3 K above the set point, or above the lowest wall temperature the uncontrolled internal heat
        allows (set points closer than ~4 K to the room)."""
        return max(self.setp['T'], self._htd_floor_k()) + 0.3

    def _cool_estimate(self, Tw):
        target, asym = self._htd_end_k(), self._htd_floor_k()
        if Tw <= target:
            return 0.0
        tau = self.m['c_wall_j_k'] / (self.m['ua_wall_w_k'] + self.m['g_door_w_k'])
        return tau * math.log(max(Tw - asym, 1e-6) / max(target - asym, 1e-3))

    def _initializing(self, g):
        u = self.status[f'{g}_init_until']
        return bool(self.power and (self.status['sensors_hot'] or (u is not None and self.t < u)))

    def _dead(self, key):
        """1.2.0: a device error of manual chapter 11 disables this sensor (or gas control) now."""
        return any(DEVICE_FAULTS[f][1] == key for f in DEVICE_FAULTS if DEVICE_FAULTS[f][0] in self.dev_errors)

    def _device_faults_tick(self):
        """1.2.0: report the injected device errors (manual chapter 11, p.136-145).  Restart-type errors stay until the incubator is switched off and on
        (they are reported again at the next tick when the cause remains); 6731FW.107 follows its cause (acclimatisation / ambient conditions)."""
        for f, (code, _key, restart) in DEVICE_FAULTS.items():
            if f in self.faults:
                self.dev_errors.add(code)
            elif not restart:
                self.dev_errors.discard(code)
            if code == 'FATAL':
                continue
            if code in self.dev_errors:
                self._raise(code, DEVICE_FAULT_ZH[code])
            else:
                self._clear(code)

    def _water_low(self):
        return bool(self.tray['V'] < TRAY_LOW or (self.tray['V'] > 0 and self.tray['T'] is not None and self.ch is not None and self.tray['T'] > self.ch['Ta'] + 1.0))

    def _relays(self):
        if not self.power:                                                     # coil de-energised: switched off or mains failure -> all relays in alarm
            return [True, True, True, True]
        if self.t < self.relay_test_until:                                     # Test Relay: active for 15 s
            return [True, True, True, True]
        a = set(self.alarms)
        return [any(RELAY_MATCH[self.dev[f'relay.ch{i}']](k) for k in a) for i in (1, 2, 3, 4)]   # events chosen per relay (factory: T, CO2, O2, water)

    def _task_due_s(self, v):
        return (v['last_done_s'] if v['last_done_s'] is not None else 0.0) + v['interval_s'] - v['reminder_s']

    def _tasks_due(self):
        out = []
        for k, v in self.tasks.items():
            if v['interval_s'] is not None and v['warn'] and self.t >= self._task_due_s(v):     # 'warn when overdue' off: an interval without a warning
                out.append(k)
        return out

    # ---- device functions behind the touch screen
    def _clock_s(self):
        return EPOCH_S + self.t + (0.0 if self.dev['datetime.automatic'] else self.clock_offset) + 3600.0 * TIMEZONES[self.dev['datetime.timezone']]

    def _clock_text(self, s=None):
        import datetime
        d = datetime.datetime.fromtimestamp(math.floor(self._clock_s() if s is None else s), tz=datetime.timezone.utc)
        return d.strftime('%Y-%m-%d %H:%M:%S')

    def _local_date(self, time_s):
        return self._clock_text(self._clock_s() - self.t + time_s)[:10]

    def _screen_locked(self):
        sc = self.screen
        return bool(self.power and (sc['locked'] or (self.dev['screenlock.auto'] and self.t - sc['touch_t'] >= self.dev['screenlock.timer_s'] - 1e-9)))

    def _logged_id(self):
        lid, lim = self.users['logged'], self.dev['users.auto_logout_s']
        if lid is None or (lim > 0 and self.t - self.screen['touch_t'] >= lim - 1e-9):   # automatic logout after lim s without a touch
            return None
        return lid

    def _role(self):
        """Rights of whoever stands at the screen: without user management everyone has administrator rights (p.103); logged out: the 'grant all
        users' role or none (view only)."""
        if not self.users['management']:
            return 'administrator'
        lid = self._logged_id()
        if lid is not None:
            return self.users['accounts'][lid]['role']
        g = self.dev['users.grant']
        return None if g == 'none' else g

    def _rights(self):
        r = self._role()
        return {k: r in v for k, v in RIGHTS.items()}

    def _users_state(self):
        u, lid = self.users, self._logged_id()
        acc = u['accounts'].get(lid) if lid is not None else None
        return dict(management=u['management'], login_mode=self.dev['users.login_mode'], logged_in=lid, user_name=acc['name'] if acc else None,
                    role=self._role(), grant=self.dev['users.grant'], rights=self._rights(),
                    accounts=[{k: a[k] for k in ('user_id', 'name', 'full_name', 'email', 'role')} for _, a in sorted(u['accounts'].items())])

    def _messages(self):
        out = [dict(code=m['code'], severity=m['severity'], time_s=m['time_s'], text=m['text']) for m in self.msgs]
        for k in self._tasks_due():
            v = self.tasks[k]
            if f'{k}@{v["last_done_s"]}' not in self.acked_due:
                out.append(dict(code='TASK_DUE:' + k, severity='warning', time_s=max(0.0, self._task_due_s(v)), text=f'周期任务到期：{v["name"]}'))
        return sorted(out, key=lambda m: (-m['time_s'], m['code']))

    def _touch(self):
        """A touch on the screen (panel layer): an automatic lock or logout that is due takes effect first, then the inactivity timers restart."""
        if self._screen_locked():
            self.screen['locked'] = True
        if self.users['logged'] is not None and self._logged_id() is None:
            self.users['logged'] = None
        self.screen['touch_t'] = self.t

    def _remember(self, g, v):
        self.recent[g] = ([v] + [x for x in self.recent[g] if abs(x - v) > 1e-9])[:6]

    def _o2_on(self):
        return 'o2' in self.dev['home.functions']

    def _secret_ok(self, secret, mode=None):
        mode = mode or self.dev['users.login_mode']
        return (secret.isdigit() and 4 <= len(secret) <= 8) if mode == 'pin' else 6 <= len(secret) <= 16

    def _new_secret(self):
        self.seq['secret'] += 1
        z = _mix((self._seed() * 7919 + self.seq['secret'] * 104729) & _MASK)
        if self.dev['users.login_mode'] == 'pin':
            return f'{z % 10000:04d}'
        abc = 'abcdefghjkmnpqrstuvwxyz23456789'
        return ''.join(abc[(z >> (5 * i)) % len(abc)] for i in range(8))

    def _need_admin(self):
        self._need_power()
        if not self.users['management']:
            raise ActionRejected('NO_USER_MANAGEMENT')
        lid = self._logged_id()
        if lid is None or self.users['accounts'][lid]['role'] != 'administrator':
            raise ActionRejected('NOT_ADMIN')
        return lid

    def _account(self, user_id):
        if user_id not in self.users['accounts']:
            raise ActionRejected('UNKNOWN_USER')
        return self.users['accounts'][user_id]

    # ---------------------------------------------------------------- invoke
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        props = CAPS[capability_id]['input_schema'].get('properties', {})
        a = {k: (int(v) if props.get(k, {}).get('type') == 'integer' and v is not None else v) for k, v in a.items()}
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if CAPS[capability_id]['kind'] not in ('query',) and capability_id != 'sim.step':
            self.revision += 1
        return out

    # ---------------------------------------------------------------- plant, power
    def _cmd_plant_configure(self, room_k, room_rh, ambient_pressure_pa, mains_voltage_v, co2_supply_pa, n2_supply_pa, noise_seed):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(room_k=room_k, room_rh=room_rh, ambient_pressure_pa=ambient_pressure_pa, mains_voltage_v=mains_voltage_v, co2_supply_pa=co2_supply_pa,
                          n2_supply_pa=n2_supply_pa, noise_seed=noise_seed)
        if first:
            xa = self._ambient_x()
            n = self._n_total(room_k)
            self.ch = dict(Ta=room_k, Tw=room_k, nc=xa['c'] * n, no=xa['o'] * n, nw=xa['w'] * n, nr=(1 - xa['c'] - xa['o'] - xa['w']) * n, cond=0.0)
            self.tray['T'] = room_k
        if self.power and not self._mains_ok():
            self._power_off('mains')
        elif self.switch_on and not self.power and self._mains_ok():
            self._power_up()
        return dict(plant=dict(self.plant))

    def _power_up(self):
        self.power = True
        c = self.ch
        st = self.status
        st['sensors_hot'] = c['Tw'] > HOT_SENSOR_K
        if st['sensors_hot']:
            for code in ('E_6731FW202', 'E_6731FW302', 'E_6731FW402', 'E_6731FW452'):
                self._raise(code, '传感器因温度过高已关闭（中断的高温消毒后余温>50 °C），冷却到50 °C以下后自动初始化')
        st['co2_init_until'] = None if st['sensors_hot'] else self.t + INIT_S['co2']
        st['o2_init_until'] = None if st['sensors_hot'] else self.t + INIT_S['o2']
        st['shutdown'] = False
        st['reached_setpoint'] = False
        st['co2_gate'] = c['Ta'] < self.setp['T'] - 0.5          # after a power interruption the CO2 valve waits for the temperature set point
        self.sens = dict(T=c['Ta'], c=None, o=None, rh=self._rh_true())
        self._sens_state = dict(T=c['Ta'], c=self._xs()[0] * self._P() / P_REF, o=self._xs()[1] * self._P() / P_REF, rh=self._rh_true())
        self.pid = dict(I_T=0.0, I_c=0.0, I_o=0.0)
        for k, v in PAUSE['switch_on'].items():
            self.pause[k] = max(self.pause[k], self.t + v)
        self.power_epoch += 1
        self.users['logged'] = None                                             # a restart logs the user out; the screen starts unlocked
        self.screen = dict(locked=False, touch_t=self.t)
        if not self._o2_on():
            st['o2_init_until'] = None
        self._event('power', 'POWER_ON', 'CellXpert通电：显示屏点亮，CO2传感器初始化30 min、O2传感器10 min')

    def _power_off(self, cause):
        if self.htd['phase'] in ('heating', 'holding', 'cooling'):
            self._htd_fail('E_6731FW751', '高温消毒因断电中断（6731FW.751）：重新启动高温消毒')
        self.power = False
        self.u = dict(h=0.0, c=0.0, n=0.0)
        self.dev_errors = set()                                               # a restart clears the device errors (they come back if the cause remains)
        for k in [k for k in self.alarms]:
            self.alarms.pop(k)
        self.sound = False
        self._event('power', 'POWER_OFF', '断电（开关关闭）' if cause == 'switch' else '断电（市电中断或电压超范围）')

    def _set_power(self, enabled):
        self._need_plant()
        if enabled:
            if not self.power:
                if not self._mains_ok():
                    raise ActionRejected('MAINS_OUT_OF_RANGE')
                self.switch_on = True
                self._power_up()
        else:
            self.switch_on = False
            if self.power:
                self._power_off('switch')
        return dict(enabled=self.power)

    def _cmd_power_set(self, enabled):
        return self._set_power(enabled)

    def _cmd_power_press_switch(self):
        return self._set_power(not self.switch_on)

    # ---------------------------------------------------------------- settings
    @staticmethod
    def _step_ok(v, step):
        return abs(v / step - round(v / step)) < 1e-6

    def _cmd_temperature_set(self, setpoint_k):
        self._need_power()
        self._need_no_htd()
        if not self._step_ok(setpoint_k - 273.15, 0.1):
            raise ActionRejected('INVALID_INCREMENT')
        self.setp['T'] = round(setpoint_k, 2)
        self._remember('temperature', round(setpoint_k - 273.15, 1))
        self.pause['temperature'] = max(self.pause['temperature'], self.t + PAUSE['setpoint']['temperature'])
        self.status['reached_setpoint'] = False
        self.timers.update(t_high=0.0, subnormal=0.0)
        warn = ['setpoint_below_ambient_plus_4k'] if setpoint_k < self._room() + 4.0 else []
        self._event('setting', 'TEMPERATURE_SET', f'温度设定值 {setpoint_k - 273.15:.1f} °C')
        return dict(setpoint_k=self.setp['T'], warnings=warn)

    def _gas_set(self, g, v, step):
        self._need_power()
        self._need_no_htd()
        if self._initializing(g):
            raise ActionRejected('SENSOR_INITIALIZING')
        if not self._step_ok(v, step):
            raise ActionRejected('INVALID_INCREMENT')
        self.setp[g] = round(v, 4)
        self._remember(g, round(100.0 * v, 1))
        self.pause[g] = max(self.pause[g], self.t + PAUSE['setpoint'][g])
        self.timers[f'{g}_off'] = 0.0
        self._event('setting', f'{g.upper()}_SET', f'{g.upper()}设定值 {100 * v:.1f} %')

    def _cmd_co2_set(self, setpoint_fraction):
        self._gas_set('co2', setpoint_fraction, 0.001)
        return dict(setpoint_fraction=self.setp['co2'])

    def _o2_max(self):
        xc = self.setp['co2'] if self.ctrl['co2'] else X_CO2_DRY_AIR
        xw = min(0.2, 0.95 * p_sat(self.setp['T']) / self._P())
        return X_O2_DRY_AIR * (1 - xc - xw) * self._P() / P_REF

    def _cmd_o2_set(self, setpoint_fraction):
        self._gas_set('o2', setpoint_fraction, 0.001)
        warn = ['o2_setpoint_above_achievable'] if setpoint_fraction > self._o2_max() else []
        return dict(setpoint_fraction=self.setp['o2'], warnings=warn)

    def _cmd_co2_control(self, enabled):
        self._need_power()
        self._need_no_htd()
        self.ctrl['co2'] = bool(enabled)
        self.timers['co2_off'] = 0.0
        self._event('setting', 'CO2_CONTROL', 'CO2控制' + ('启用' if enabled else '停用'))
        return dict(enabled=self.ctrl['co2'])

    def _cmd_o2_control(self, enabled):
        self._need_power()
        self._need_no_htd()
        self.ctrl['o2'] = bool(enabled)
        self.timers['o2_off'] = 0.0
        self._event('setting', 'O2_CONTROL', 'O2控制' + ('启用' if enabled else '停用'))
        return dict(enabled=self.ctrl['o2'])

    def _cmd_gas_pressure_monitor(self, enabled):
        self._need_power()
        self.ctrl['monitor'] = bool(enabled)
        return dict(enabled=self.ctrl['monitor'])

    def _cmd_gas_reset(self, gas):
        self._need_power()
        if not self.status[f'{gas}_failed']:
            raise ActionRejected('NO_CONTROL_ERROR')
        self.status[f'{gas}_failed'] = False
        self.timers[f'{gas}_off'] = 0.0
        self.pid['I_c' if gas == 'co2' else 'I_o'] = 0.0
        self._clear('E_6731FW214' if gas == 'co2' else 'E_6731FW314')
        self._event('setting', f'{gas.upper()}_RESET', f'{gas.upper()}控制已重启')
        return dict(gas=gas)

    def _cmd_alarm_configure(self, kind, enabled, low_k=None, high_k=None, low=None, high=None, signal_s=None):
        self._need_power()
        new = dict(self.alarm_cfg[kind])
        new['enabled'] = bool(enabled)
        given = {k for k, v in dict(low_k=low_k, high_k=high_k, low=low, high=high, signal_s=signal_s).items() if v is not None}
        allowed = dict(temperature={'low_k', 'high_k'}, co2={'low', 'high'}, o2={'low', 'high'}, rh={'low'}, door={'signal_s'}, water_level=set())[kind]
        if given - allowed:
            raise ActionRejected('INVALID_ALARM_LIMIT')                        # a limit that does not belong to this alarm kind
        if kind == 'door' and signal_s is not None:
            if float(signal_s) not in DOOR_SIGNALS:
                raise ActionRejected('INVALID_DOOR_SIGNAL')
            new['signal_s'] = float(signal_s)
        lim = dict(temperature=(0.1, 5.0), co2=(0.001, 0.05), o2=(0.001, 0.05), rh=(0.5, 0.95)).get(kind)
        for key, v in (('low_k', low_k), ('high_k', high_k), ('low', low), ('high', high)):
            if v is not None:
                if not lim[0] <= v <= lim[1]:
                    raise ActionRejected('INVALID_ALARM_LIMIT')
                new[key] = float(v)
        self.alarm_cfg[kind] = new
        self._event('setting', 'ALARM_CONFIG', f'报警设置 {kind}: {json.dumps(new, sort_keys=True)}')
        return dict(kind=kind, settings=dict(new))

    def _cmd_alarm_mute(self):
        self._need_power()
        self.sound = False
        return dict(sound=False)

    def _cmd_contamination_set_status(self, contaminated):
        self._need_power()
        self.contamination = 'contaminated' if contaminated else 'not_contaminated'
        self.contamination_changed_s = self.t
        self._event('maintenance', 'CONTAMINATION_STATUS', '信息栏：已污染' if contaminated else '信息栏：未污染（请进行高温消毒）')
        return dict(status=self.contamination)

    def _cmd_task_configure(self, task, interval_s):
        self._need_power()
        self.tasks[task]['interval_s'] = None if interval_s is None else float(interval_s)
        return dict(task=task, interval_s=self.tasks[task]['interval_s'])

    def _cmd_task_complete(self, task):
        self._need_power()
        if TASKS[task]['procedure']:
            raise ActionRejected('TASK_REQUIRES_PROCEDURE')
        self.tasks[task]['last_done_s'] = self.t
        self._event('maintenance', 'TASK_DONE', f'周期任务完成：{task}')
        return dict(task=task, completed_s=self.t)

    def _new_record(self, kind, **details):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq['rec'] += 1
        rid = f'R{self.seq["rec"]:04d}'
        self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, details=details)
        if self._caller is not None:
            self._owners[rid] = self._caller
        return rid

    def _cmd_performance_check_record(self, kind, instrument, reference_temperature_k=None, reference_fraction=None):
        self._need_power()
        self._need_no_htd()
        ref = reference_temperature_k if kind == 'temperature' else reference_fraction
        if ref is None or (reference_fraction if kind == 'temperature' else reference_temperature_k) is not None:
            raise ActionRejected('MISSING_REFERENCE')                          # temperature: reference_temperature_k; CO2 / O2: reference_fraction
        default = dict(temperature=('T', 310.15, 'sensor.temperature_k'), co2=('co2', 0.05, 'sensor.co2_fraction'), o2=('o2', 0.10, 'sensor.o2_fraction'))[kind]
        if abs(self.setp[default[0]] - default[1]) > 1e-9:
            raise ActionRejected('NOT_AT_DEFAULT_SETPOINT')
        if kind == 'o2' and not self.ctrl['o2']:
            raise ActionRejected('NOT_AT_DEFAULT_SETPOINT')
        shown = self.read()[default[2]]
        if shown is None or self._doors_open():
            raise ActionRejected('NOT_STABLE')
        if kind == 'temperature':
            vals = dict(displayed_temperature_k=float(shown), reference_temperature_k=float(ref), deviation_k=float(shown - ref))
        else:
            vals = dict(displayed_fraction=float(shown), reference_fraction=float(ref), deviation_fraction=float(shown - ref))
        rid = self._new_record('performance_check', quantity=kind, instrument=instrument, **vals)
        r = self.records[rid]
        r.update(status='completed', finished_s=self.t)
        self.tasks['performance_check']['last_done_s'] = self.t
        self._event('maintenance', 'PERFORMANCE_CHECK', f'性能检查{kind}：显示{shown:.4g}，外部{ref:.4g}', ref=rid)
        out = dict(record_id=rid, displayed_temperature_k=None, deviation_k=None, displayed_fraction=None, deviation_fraction=None)
        out.update({k: v for k, v in vals.items() if not k.startswith('reference')})
        return out

    def _cmd_data_export(self, kind, span_s=None, parameters=None, filters=None):
        """Export to the USB stick.  Charts: the selected time span and parameters (8.5.5); events: the currently filtered event log (8.4.4);
        without these arguments everything stored (as before)."""
        self._need_power()
        if (kind != 'chart' and (span_s is not None or parameters is not None)) or (kind != 'events' and filters is not None):
            raise ActionRejected('INVALID_ARGUMENT')
        chart = [r for r in self.chart if span_s is None or r['time_s'] >= self.t - span_s]
        cats = [c for c in EVENT_CATS if (filters or {}).get(c)]
        events = self.events if not cats else [e for e in self.events if self._event_cat(e) in cats]
        rows = dict(events=len(events), chart=len(chart), htd_protocols=sum(1 for r in self.records.values() if r['kind'] == 'htd'),
                    performance_checks=sum(1 for r in self.records.values() if r['kind'] == 'performance_check'))[kind]
        per = dict(events=90, chart=64 if parameters is None else 16 + 12 * len(set(parameters)), htd_protocols=400, performance_checks=160)[kind]   # time + 12 B per function
        name = f'CellXpert_{kind}_{int(self.t)}.csv'
        self._event('maintenance', 'EXPORTED', f'{name}导出到U盘')
        return dict(file=name, bytes=int(200 + per * rows), rows=int(rows))

    # ---------------------------------------------------------------- device functions behind the touch screen (manual sections 6-10)
    def _cmd_settings_configure(self, settings):
        self._need_power()
        defs = {d['key']: d for d in CX_SETTINGS}
        new = copy.deepcopy(self.dev)
        for k, v in settings.items():
            d = defs.get(k)
            if d is None:
                raise ActionRejected('UNKNOWN_SETTING')
            t = d['type']
            num = isinstance(v, (int, float)) and not isinstance(v, bool)
            ok = {'bool': lambda: isinstance(v, bool), 'text': lambda: isinstance(v, str) and len(v) <= d['max_len'], 'enum': lambda: v in d['options'],
                  'enum_num': lambda: num and float(v) in d['options'], 'int': lambda: num and float(v).is_integer() and d['min'] <= v <= d['max'],
                  'ip': lambda: isinstance(v, str) and len(v.split('.')) == 4 and all(p.isdigit() and int(p) <= 255 for p in v.split('.')),
                  'list': lambda: isinstance(v, list) and len(set(v)) == len(v) and set(v) <= set(d['options'])}[t]()
            if not ok:
                raise ActionRejected('INVALID_SETTING')
            new[k] = float(v) if t == 'enum_num' else int(v) if t == 'int' else copy.deepcopy(v)
        o2_was = self._o2_on()
        if new['datetime.automatic'] and not self.dev['datetime.automatic']:
            self.clock_offset = 0.0                                             # automatic: the time server's (host) clock
        self.dev = new
        if o2_was and not self._o2_on():                                        # O2 switched off on the home screen: the sensor is off (p.50, p.80)
            self.sens['o'] = None
            self.status['o2_init_until'] = None
        elif self._o2_on() and not o2_was and self.power:                       # switched on again: the sensor re-initialises (10 min)
            self.sens['o'] = None
            self.status['o2_init_until'] = self.t + INIT_S['o2']
        self._event('setting', 'DEVICE_SETTINGS', '设备设置：' + ', '.join(sorted(settings)))
        return dict(settings=copy.deepcopy(self.dev))

    def _cmd_clock_set(self, date, time):
        self._need_power()
        if self.dev['datetime.automatic']:
            raise ActionRejected('AUTOMATIC_TIME')
        import datetime
        try:
            d = datetime.datetime.strptime(f'{date} {time}', '%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            raise ActionRejected('INVALID_DATE') from None
        self.clock_offset = d.timestamp() - (EPOCH_S + self.t + 3600.0 * TIMEZONES[self.dev['datetime.timezone']])
        self._event('setting', 'CLOCK_SET', f'日期时间设为{date} {time}')
        return dict(clock=self._clock_text())

    def _cmd_network_create_certificate(self):
        self._need_power()
        self.seq['secret'] += 1
        self.certificate = hashlib.sha256(f'{self._seed()}:{self.seq["secret"]}:{self.t}'.encode()).hexdigest()[:40]
        self._event('setting', 'CERTIFICATE', '已生成自签名证书')
        return dict(fingerprint=self.certificate)

    def _cmd_relay_test(self):
        self._need_power()
        self.relay_test_until = self.t + RELAY_TEST_S
        self._event('maintenance', 'RELAY_TEST', 'BMS继电器测试15 s')
        return dict(until_s=self.relay_test_until)

    def _cmd_screen_lock(self, locked):
        self._need_power()
        self.screen = dict(locked=bool(locked), touch_t=self.t)
        return dict(locked=self._screen_locked())

    def _cmd_users_enable(self, login_mode, admin_name, secret):
        self._need_power()
        if self.users['management']:
            raise ActionRejected('USER_MANAGEMENT_ON')
        if not self._secret_ok(secret, login_mode):
            raise ActionRejected('INVALID_SECRET')
        self.dev['users.login_mode'] = login_mode
        self.users = dict(management=True, accounts={1: dict(user_id=1, name=admin_name, full_name='', email='', role='administrator', secret=secret)}, logged=1)
        self.screen['touch_t'] = self.t
        self._event('setting', 'USER_MANAGEMENT_ON', f'用户管理已启用（第一个管理员：{admin_name}）')
        return dict(user_id=1)

    def _cmd_users_disable(self, secret):
        lid = self._need_admin()
        if secret != self.users['accounts'][lid]['secret']:
            raise ActionRejected('WRONG_SECRET')
        self.users = dict(management=False, accounts={}, logged=None)
        self._event('setting', 'USER_MANAGEMENT_OFF', '用户管理已停用，全部账户已删除')
        return dict(management=False)

    def _cmd_user_login(self, user_id, secret):
        self._need_power()
        if not self.users['management']:
            raise ActionRejected('NO_USER_MANAGEMENT')
        if self._account(user_id)['secret'] != secret:
            raise ActionRejected('WRONG_SECRET')
        self.users['logged'] = user_id
        self.screen['touch_t'] = self.t
        self._event('setting', 'LOGIN', f'用户{user_id}登录')
        return dict(user_id=user_id, role=self.users['accounts'][user_id]['role'])

    def _cmd_user_logout(self):
        self._need_power()
        was = self._logged_id() is not None
        self.users['logged'] = None
        if was:
            self._event('setting', 'LOGOUT', '用户注销')
        return dict(logged_in=False)

    def _cmd_user_create(self, name):
        self._need_admin()
        acc = self.users['accounts']
        if len(acc) >= MAX_USERS:
            raise ActionRejected('USERS_FULL')
        if any(a['name'] == name for a in acc.values()):
            raise ActionRejected('USER_EXISTS')
        uid = next(i for i in range(1, MAX_USERS + 1) if i not in acc)
        secret = self._new_secret()
        acc[uid] = dict(user_id=uid, name=name, full_name='', email='', role='restricted', secret=secret)
        self.last_credentials = dict(user_id=uid, name=name, secret=secret)
        self._event('setting', 'USER_CREATED', f'新建用户{uid}（{name}）')
        return dict(user_id=uid, secret=secret)

    def _cmd_user_update(self, user_id, full_name=None, email=None, role=None, new_user_id=None):
        self._need_power()
        if not self.users['management']:
            raise ActionRejected('NO_USER_MANAGEMENT')
        a = self._account(user_id)
        lid = self._logged_id()
        admin = lid is not None and self.users['accounts'][lid]['role'] == 'administrator'
        if not admin and (lid != user_id or role is not None or new_user_id is not None):   # users edit only their own full name and e-mail (p.110)
            raise ActionRejected('NOT_ADMIN')
        if new_user_id is not None and new_user_id != user_id and new_user_id in self.users['accounts']:
            raise ActionRejected('USER_EXISTS')
        if full_name is not None:
            a['full_name'] = full_name
        if email is not None:
            a['email'] = email
        if role is not None:
            a['role'] = role
        if new_user_id is not None and new_user_id != user_id:
            self.users['accounts'][new_user_id] = dict(self.users['accounts'].pop(user_id), user_id=new_user_id)
            if self.users['logged'] == user_id:
                self.users['logged'] = new_user_id
            a = self.users['accounts'][new_user_id]
        return dict(user={k: a[k] for k in ('user_id', 'name', 'full_name', 'email', 'role')})

    def _cmd_user_delete(self, user_id):
        lid = self._need_admin()
        self._account(user_id)
        if user_id == lid:
            raise ActionRejected('SELF_DELETE')
        del self.users['accounts'][user_id]
        self._event('setting', 'USER_DELETED', f'删除用户{user_id}')
        return dict(user_id=user_id)

    def _cmd_user_reset_secret(self, user_id):
        self._need_admin()
        a = self._account(user_id)
        a['secret'] = self._new_secret()
        self.last_credentials = dict(user_id=user_id, name=a['name'], secret=a['secret'])
        self._event('setting', 'USER_SECRET_RESET', f'重置用户{user_id}的PIN/密码')
        return dict(user_id=user_id, secret=a['secret'])

    def _cmd_user_change_secret(self, current, new):
        self._need_power()
        lid = self._logged_id() if self.users['management'] else None
        if lid is None:
            raise ActionRejected('NOT_LOGGED_IN')
        a = self.users['accounts'][lid]
        if a['secret'] != current:
            raise ActionRejected('WRONG_SECRET')
        if not self._secret_ok(new):
            raise ActionRejected('INVALID_SECRET')
        a['secret'] = new
        return dict(changed=True)

    def _cmd_message_acknowledge(self, all):
        self._need_power()
        items = self._messages()
        if not items:
            raise ActionRejected('NO_MESSAGE')
        chosen = items if all else items[:1]                                    # the current message is the newest one
        for m in chosen:
            if m['code'].startswith('TASK_DUE:'):
                k = m['code'][9:]
                self.acked_due.add(f'{k}@{self.tasks[k]["last_done_s"]}')
            else:
                self.msgs = [x for x in self.msgs if x['code'] != m['code']]
        self._event('alarm', 'ACKNOWLEDGED', '已确认：' + ', '.join(m['code'] for m in chosen))
        return dict(acknowledged=[m['code'] for m in chosen])

    def _task(self, task):
        if task not in self.tasks:
            raise ActionRejected('UNKNOWN_TASK')
        return self.tasks[task]

    def _cmd_recurring_task_define(self, name, warn, interval, reminder_days):
        self._need_power()
        if sum(1 for v in self.tasks.values() if v['custom']) >= MAX_CUSTOM_TASKS:
            raise ActionRejected('TASKS_FULL')
        self.seq['task'] += 1
        k = f'custom_{self.seq["task"]}'
        self.tasks[k] = dict(interval_s=None if interval is None else TASK_INTERVALS[interval], last_done_s=self.t, warn=bool(warn), reminder_s=86400.0 * reminder_days,
                             custom=True, name=name)
        self._event('maintenance', 'TASK_DEFINED', f'新建周期任务：{name}')
        return dict(task=k)

    def _cmd_recurring_task_edit(self, task, warn, interval, reminder_days):
        self._need_power()
        v = self._task(task)
        v.update(interval_s=None if interval is None else TASK_INTERVALS[interval], warn=bool(warn), reminder_s=86400.0 * reminder_days)
        return dict(task=task, interval_s=v['interval_s'])

    def _cmd_recurring_task_delete(self, task):
        self._need_power()
        if not self._task(task)['custom']:
            raise ActionRejected('NOT_CUSTOM_TASK')                             # only tasks defined by the user are deletable (p.96)
        del self.tasks[task]
        return dict(task=task)

    def _cmd_recurring_task_confirm(self, task):
        self._need_power()
        v = self._task(task)
        if TASKS.get(task, {}).get('procedure'):
            raise ActionRejected('TASK_REQUIRES_PROCEDURE')                     # HTD and the performance check are done by their procedures
        v['last_done_s'] = self.t
        self._event('maintenance', 'TASK_DONE', f'周期任务完成：{self.tasks[task]["name"]}')
        return dict(task=task, completed_s=self.t)

    def _cmd_operation_record_create(self, name, start_date, finish_date):
        self._need_power()
        import datetime
        try:
            d0, d1 = (datetime.date.fromisoformat(x) for x in (start_date, finish_date))
        except ValueError:
            raise ActionRejected('INVALID_DATE') from None
        if d1 < d0:
            raise ActionRejected('INVALID_DATE')
        self.seq['oprec'] += 1
        lid = self._logged_id()
        cfg = self.alarm_cfg
        initial = dict(temperature_c=round(self.setp['T'] - 273.15, 1), co2_percent=round(100 * self.setp['co2'], 1), o2_percent=round(100 * self.setp['o2'], 1),
                       temperature_alarm_k=[cfg['temperature']['low_k'], cfg['temperature']['high_k']], co2_alarm=[cfg['co2']['low'], cfg['co2']['high']],
                       o2_alarm=[cfg['o2']['low'], cfg['o2']['high']], rh_alarm_low=cfg['rh']['low'])
        self.op_records = (self.op_records + [dict(record_id=f'OR{self.seq["oprec"]:04d}', name=name, start_date=start_date, finish_date=finish_date, created_s=self.t,
                                                   user=self.users['accounts'][lid]['name'] if lid is not None else None, initial=initial,
                                                   company=self.dev['records.company'])])[-MAX_OP_RECORDS:]    # the oldest entries are overwritten (p.99)
        return dict(record_id=self.op_records[-1]['record_id'])

    def _cmd_operation_records_list(self):
        out = []
        for r in self.op_records:
            ev = [dict(time_s=e['time_s'], kind=e['kind'], code=e['code'], message_zh=e['message_zh']) for e in self.events
                  if e['kind'] in ('alarm', 'error') and not e['code'].endswith('_CLEARED') and e['code'] != 'ACKNOWLEDGED'
                  and r['start_date'] <= self._local_date(e['time_s']) <= r['finish_date']][:200]
            res = ('contains_error' if any(e['kind'] == 'error' for e in ev) else 'contains_alarm' if any(not e['code'].endswith('_WARNING') for e in ev)
                   else 'contains_warnings' if ev else 'successfully_created')
            out.append(dict(copy.deepcopy(r), result=res, events=ev))
        return dict(records=out)

    def _cmd_export_files(self, kinds):
        self._need_power()
        files = []
        for k in dict.fromkeys(kinds):
            if k in EXPORT_KINDS:
                r = self._cmd_data_export(k)
                files.append(dict(kind=k, **r))
                continue
            rows = dict(service_data=len(self.events) + len(self.chart), operation_records=len(self.op_records), user_data=len(self.users['accounts']))[k]
            ext, per = dict(service_data=('zip', 48), operation_records=('pdf', 2500), user_data=('txt', 60))[k]
            name = f'CellXpert_{k}_{int(self.t)}.{ext}'
            self._event('maintenance', 'EXPORTED', f'{name}导出到U盘')
            files.append(dict(kind=k, file=name, bytes=int(400 + per * rows), rows=int(rows)))
        return dict(files=files)

    # ---------------------------------------------------------------- doors, tray, ports, sensor cover (robot / host skills)
    def _cmd_door_outer(self, open):
        self._need_plant()
        if not open and (self._ang('inner') > 1e-9 or self.door['inner']['target'] > 0):
            raise ActionRejected('INNER_DOOR_OPEN')
        self._set_door('outer', DOOR_OPEN_RAD if open else 0.0)
        return dict(target_rad=self.door['outer']['target'])

    def _cmd_door_latch(self, open):
        self._need_plant()
        if self._ang('outer') < 1.0:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        if not open and (self._ang('inner') > 1e-9 or self.door['inner']['target'] > 0):
            raise ActionRejected('INNER_DOOR_OPEN')
        self._set_door('latch', LATCH_OPEN_RAD if open else 0.0)
        return dict(target_rad=self.door['latch']['target'])

    def _cmd_door_inner(self, open):
        self._need_plant()
        if open:
            if self._ang('outer') < DOOR_OPEN_RAD - 1e-9 or self.door['outer']['target'] < DOOR_OPEN_RAD:
                raise ActionRejected('OUTER_DOOR_CLOSED')
            if self._ang('latch') < LATCH_OPEN_RAD - 1e-9 or self.door['latch']['target'] < LATCH_OPEN_RAD:
                raise ActionRejected('LATCH_CLOSED')
        self._set_door('inner', DOOR_OPEN_RAD if open else 0.0)
        return dict(target_rad=self.door['inner']['target'])

    def _cmd_water_tray_fill(self, volume_m3, temperature_k):
        self._need_doors_open()
        self._need_cool_chamber()
        V = self.tray['V']
        if V + volume_m3 > TRAY_CAPACITY + 1e-12:
            raise ActionRejected('TRAY_OVERFLOW')
        T0 = self.tray['T'] if V > 0 else temperature_k
        Ctot = (V * RHO_WATER * C_WATER + volume_m3 * RHO_WATER * C_WATER)
        self.tray['T'] = (V * RHO_WATER * C_WATER * T0 + volume_m3 * RHO_WATER * C_WATER * temperature_k) / Ctot
        self.tray['V'] = V + volume_m3
        self.led['water_filled_m3'].add(volume_m3)
        if self.tray['cleaned'] or V == 0:
            self.tasks['water_refill']['last_done_s'] = self.t
        self.tray['cleaned'] = False
        warn = []
        if temperature_k > self.setp['T'] + 0.5:
            warn.append('water_warmer_than_setpoint')
        if self.tray['V'] < 1.5e-3:
            warn.append('below_recommended_1_5_l')
        self._event('maintenance', 'WATER_FILLED', f'水盘加水{volume_m3 * 1e3:.2f} L（{temperature_k - 273.15:.1f} °C）')
        return dict(volume_m3=self.tray['V'], warnings=warn)

    def _cmd_water_tray_empty(self, clean):
        self._need_doors_open()
        self._need_cool_chamber()
        v = self.tray['V']
        self.tray['V'] = 0.0
        self.tray['cleaned'] = bool(clean)
        self.led['water_removed_m3'].add(v)
        self._event('maintenance', 'WATER_EMPTIED', f'水盘倒空{v * 1e3:.2f} L' + ('并清洁' if clean else ''))
        return dict(removed_m3=v)

    def _cmd_rh_sensor_cover(self, on):
        self._need_doors_open()
        self._need_cool_chamber()
        self.rh_cover = bool(on)
        return dict(on=self.rh_cover)

    def _cmd_access_port_plug(self, port, plugged):
        self._need_plant()
        self._need_cool_chamber()
        self.ports[port] = bool(plugged)
        return dict(port=port, plugged=self.ports[port])

    # ---------------------------------------------------------------- vessels
    def _cmd_vessel_provide(self, vessel_id, vessel_type, medium_id, medium_volume_m3, seeded_cells, temperature_k, cell_line_id=None, closure=None,
                            initial_gas='as_supplied'):
        self._need_plant()
        if vessel_id in self.vessels:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.vessels) >= MAX_VESSELS:
            raise ActionRejected('TOO_MANY_ITEMS')
        vt = vessel_types()[vessel_type]
        md = media().get(medium_id)
        if md is None:
            raise ActionRejected('UNKNOWN_MEDIUM')
        cl = None
        if cell_line_id is not None:
            cl = cell_lines().get(cell_line_id)
            if cl is None:
                raise ActionRejected('UNKNOWN_CELL_LINE')
        elif seeded_cells > 0:
            raise ActionRejected('CELL_LINE_REQUIRED')
        closure = closure or vt['closure_default']
        if closure not in vt['closures']:
            raise ActionRejected('CLOSURE_MISMATCH')
        lo, hi = vt['medium_volume_per_well_m3']
        per_well = medium_volume_m3 / vt['wells']
        if per_well > 3.0 * hi + 1e-12 or per_well < 0.25 * lo - 1e-12:
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        if cl is not None and cl['growth_mode'] == 'adherent' and seeded_cells > vt['growth_area_m2'] * cl['max_density']['per_area_m2']:
            raise ActionRejected('SEEDING_ABOVE_CONFLUENCE')
        T = temperature_k
        xa = self._ambient_x()
        P = self._P()
        V = medium_volume_m3
        p_o = xa['o'] * P
        hep = md['hepes_mol_m3'] * V
        alk = (md['bicarbonate_mol_m3'] + hepes_base_fraction() * md['hepes_mol_m3'] + md['phosphate_dibasic_mol_m3']) * V
        qc = md['qc_pH'] or [7.2, 7.6]
        if md['bicarbonate_mol_m3'] < 1.0:                                     # no bicarbonate (L-15): the base of the unmodelled amino-acid buffers is set
            h_q = 10 ** -(0.5 * (qc[0] + qc[1]))                               # so that the medium is at its QC pH midpoint (25 C) without CO2
            alk = _bases(h_q, hep / V, md['phosphate_total_mol_m3'], 0.0, _constants(298.15)) * V
        if initial_gas == 'as_supplied':                                       # sealed bottle at the QC pH midpoint (25 C); its inorganic carbon is kept at T
            c25 = co2_for_ph(0.5 * (qc[0] + qc[1]), alk / V, hep / V, md['phosphate_total_mol_m3'], 0.0, 298.15)
            k25 = _constants(298.15)
            h25 = 10 ** -(0.5 * (qc[0] + qc[1]))
            dic = c25 * (1 + k25[0] / h25 + k25[0] * k25[4] / (h25 * h25))
            _, c_aq = sealed_state(alk / V, dic * V, V, 0.0, hep / V, md['phosphate_total_mol_m3'], 0.0, T)
        else:
            if initial_gas == 'air':
                x_c = xa['c']
            elif self.power and self.ctrl['co2'] and self.htd['phase'] == 'idle':
                x_c = self.setp['co2']                                         # pre-equilibrated at this incubator's CO2 set point
            else:
                x_c = self._xs()[0] if self.ch is not None else xa['c']       # CO2 control off: the chamber's actual CO2
            c_aq = henry_co2(T) * x_c * P
        osm = md['qc_osmolality_mol_kg']
        v = dict(vessel_id=vessel_id, type=vessel_type, medium=medium_id, cell_line=cell_line_id, closure=closure, location='bench', V=medium_volume_m3, V0=medium_volume_m3,
                 Tm=T, co2=0.0, o2=henry_o2(T) * p_o * medium_volume_m3, alk=alk, hep=hep,
                 phos=md['phosphate_total_mol_m3'] * medium_volume_m3, glc=(md['glucose_mol_m3'] + md['galactose_mol_m3']) * medium_volume_m3, lac=0.0,
                 osm0=0.5 * (osm[0] + osm[1]) if osm else 0.30, N=float(seeded_cells), D=0.0, B=0.0, t_seed=self.t, phenol=md['phenol_red'],
                 area=vt['growth_area_m2'], wells=vt['wells'], mass=vt['empty_mass_kg'], footprint=vt['footprint_m'][0] * vt['footprint_m'][1],
                 G=vt['thermal_conductance_w_k'], tau_hs=vt['tau_headspace_s'] if closure != 'plug_seal_closed' else None, g_vap=vt['vapour_conductance_m3_s'] * (0.05 if closure == 'plug_seal_closed' else 1.0),
                 pH=7.4, phase='empty' if not seeded_cells else 'lag', evap_mol_s=0.0, lid_fog=False)
        v['co2'] = c_aq * V
        if closure == 'plug_seal_closed':                                      # capped on the bench: headspace of room air (incubator gas for 'incubator')
            Vh = max(1e-7, vt['internal_volume_m3'] - V)
            k = _constants(T)
            h = 10 ** -medium_ph(alk / V, c_aq, hep / V, md['phosphate_total_mol_m3'], T)
            x_h = xa['c'] if initial_gas != 'incubator' else c_aq / (henry_co2(T) * P)
            v['n_c'] = c_aq * V * (1 + k[0] / h + k[0] * k[4] / (h * h)) + x_h * P * Vh / (R * T)
            v['n_o'] = henry_o2(T) * p_o * V + p_o * Vh / (R * T)
            v['V_int'] = vt['internal_volume_m3']
        v['pH'] = self._vessel_ph(v)
        self.vessels[vessel_id] = v
        if self._caller is not None:
            self._owners[vessel_id] = self._caller
        return dict(vessel_id=vessel_id, location='bench')

    def _vessel(self, vessel_id):
        v = self.vessels.get(vessel_id)
        if v is None:
            raise ActionRejected('UNKNOWN_ITEM')
        return v

    def _cmd_vessel_place(self, vessel_id, shelf):
        self._need_doors_open()
        v = self._vessel(vessel_id)
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        if self.htd['phase'] != 'idle':
            raise ActionRejected('HTD_RUNNING')
        here = [x for x in self.vessels.values() if x['location'] == f'shelf_{shelf}']
        if sum(x['footprint'] for x in here) + v['footprint'] > SHELF_AREA + 1e-12:
            raise ActionRejected('SHELF_FULL')
        if sum(x['mass'] + x['V'] * 1000.0 for x in here) + v['mass'] + v['V'] * 1000.0 > SHELF_MAX_KG:
            raise ActionRejected('SHELF_OVERLOAD')
        v['location'] = f'shelf_{shelf}'
        self._event('culture', 'VESSEL_PLACED', f'容器{vessel_id}放到第{shelf}层', ref=vessel_id)
        return dict(vessel_id=vessel_id, shelf=shelf)

    def _cmd_vessel_remove(self, vessel_id):
        self._need_doors_open()
        v = self._vessel(vessel_id)
        if v['location'] == 'bench':
            raise ActionRejected('VESSEL_NOT_IN_CHAMBER')
        v['location'] = 'bench'
        self._event('culture', 'VESSEL_REMOVED', f'容器{vessel_id}取回实验台', ref=vessel_id)
        return dict(vessel_id=vessel_id)

    def _cmd_vessel_observe(self, vessel_id):
        v = self._vessel(vessel_id)
        if v['location'] != 'bench' and self._door_state('outer') != 'open':
            raise ActionRejected('NOT_VISIBLE')                                  # the outer door is opaque; through the glass door when it is open
        name, rgb = phenol_red(v['pH'], v['phenol'])
        return dict(vessel_id=vessel_id, location=v['location'], medium_colour=name, medium_rgb=rgb, turbid=v['B'] / max(v['V'], 1e-12) > GROWTH['bacteria_turbid_m3'],
                    medium_volume_m3=round(v['V'] / 1e-7) * 1e-7, lid_condensation=bool(v['lid_fog']))

    def _cmd_vessel_discard(self, vessel_id):
        v = self._vessel(vessel_id)
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        del self.vessels[vessel_id]
        self.vessel_faults.discard(vessel_id)
        return dict(vessel_id=vessel_id)

    def _cmd_vessel_inspect(self, vessel_id):
        v = self._vessel(vessel_id)
        cl = cell_lines().get(v['cell_line']) if v['cell_line'] else None
        V = v['V']
        conf = None
        dens = None
        if cl is not None:
            if cl['growth_mode'] == 'adherent':
                conf = v['N'] / (v['area'] * cl['max_density']['per_area_m2'])
            else:
                dens = v['N'] / V
        truth = dict(location=v['location'], medium_id=v['medium'], cell_line_id=v['cell_line'], temperature_k=v['Tm'], pH=v['pH'], medium_volume_m3=V,
                     co2_aq_mol_m3=v['co2'] / V, o2_aq_mol_m3=v['o2'] / V, pericellular_o2_mol_m3=self._pericellular_o2(v, cl), glucose_mol_m3=v['glc'] / V, lactate_mol_m3=v['lac'] / V,
                     alkalinity_mol_m3=v['alk'] / V, osmolality_mol_kg=v['osm0'] * v['V0'] / V, cells_live=v['N'], cells_dead=v['D'],
                     viability=v['N'] / (v['N'] + v['D']) if v['N'] + v['D'] > 0 else None, confluence=conf, suspension_density_per_m3=dens, phase=v['phase'],
                     bacteria=v['B'], contaminated=vessel_id in self.vessel_faults, evaporation_mol_s=v['evap_mol_s'],
                     headspace_co2_fraction=(v['co2'] / V / henry_co2(v['Tm']) / self._P()) if 'n_c' in v else None)
        return dict(vessel_id=vessel_id, truth=truth)

    # ---------------------------------------------------------------- HTD
    def _cmd_htd_start(self):
        self._need_power()
        self._need_no_htd()
        if self.op is not None:
            raise ActionRejected('BUSY')
        if self._in_chamber():
            raise ActionRejected('VESSELS_IN_CHAMBER')
        if self.tray['V'] > 5e-5:
            raise ActionRejected('WATER_IN_TRAY')
        if not self.rh_cover:
            raise ActionRejected('RH_SENSOR_UNCOVERED')
        if not all(self.ports.values()):
            raise ActionRejected('ACCESS_PORT_OPEN')
        if self._doors_open() or not self._latched():
            raise ActionRejected('DOORS_NOT_CLOSED')
        if self.status['shutdown']:
            raise ActionRejected('HEATING_SHUTDOWN')
        self.seq['op'] += 1
        oid = f'op{self.seq["op"]:04d}'
        rid = self._new_record('htd', phases={}, max_wall_k=None, hold_at_180_s=0.0)
        self.op = self.operations[oid] = dict(op=oid, kind='htd', phase='running', start=self.t, record_id=rid)
        if self._caller is not None:
            self._owners[oid] = self._caller
        self.htd.update(phase='heating', t_phase=self.t, hold_s=0.0, max_k=self.ch['Tw'])
        self.records[rid]['details']['phases']['heating'] = self.t
        self._event('htd', 'HTD_START', '高温消毒开始：升温到180 °C', ref=rid)
        return dict(operation_id=oid)

    def _htd_finish(self, result, code=None, message=None):
        op = self.op
        rec = self.records[op['record_id']] if op else None
        self.htd['last'] = result
        if rec is not None:
            rec.update(status=result, finished_s=self.t)
            rec['details'].update(max_wall_k=self.htd['max_k'], hold_at_180_s=self.htd['hold_s'])
        if op is not None:
            op['phase'] = 'completed' if result == 'successful' else result
        self.op = None
        if result == 'successful':
            self.htd['phase'] = 'idle'
            self.tasks['high_temperature_disinfection']['last_done_s'] = self.t
            for v in self.vessels.values():                                    # nothing survives in the chamber; the tray is dry
                pass
            self.status['co2_init_until'] = self.t + INIT_S['co2']
            self.status['o2_init_until'] = self.t + INIT_S['o2']
            for k, v in PAUSE['htd'].items():
                self.pause[k] = max(self.pause[k], self.t + v)
            self._event('htd', 'HTD_DONE', '高温消毒成功（180 °C保持2 h）；传感器重新初始化', ref=op['record_id'] if op else None)
        else:
            self.htd['phase'] = 'post_htd_cooling' if self.ch['Tw'] > self.setp['T'] + 0.5 else 'idle'
            if code:
                self._raise(code, message)
            self._event('htd', 'HTD_' + result.upper(), message or f'高温消毒{result}', ref=op['record_id'] if op else None)

    def _htd_fail(self, code, message):
        self._htd_finish('failed', code, message)

    def _cancel_op(self, op):
        if self.op is None or self.op is not op:
            raise ActionRejected('NOT_RUNNING')
        self._htd_finish('cancelled', None, '高温消毒已取消（加热停止，冷却到培养温度）')

    # ---------------------------------------------------------------- faults and clock
    def _cmd_fault_inject(self, fault, active, vessel_id=None):
        self._need_plant()
        if fault == 'vessel_contamination':
            v = self._vessel(vessel_id) if vessel_id is not None else None
            if v is None:
                raise ActionRejected('UNKNOWN_ITEM')
            if active:
                self.vessel_faults.add(vessel_id)
                v['B'] = max(v['B'], 1e3)
            else:
                self.vessel_faults.discard(vessel_id)
        else:
            (self.faults.add if active else self.faults.discard)(fault)
            if fault == 'power_loss':
                if active and self.power:
                    self._power_off('mains')
                elif not active and self.switch_on and not self.power and self._mains_ok():
                    self._power_up()
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"}' + (f' {vessel_id}' if vessel_id else ''))
        return dict(faults=sorted(self.faults) + sorted(f'vessel_contamination:{v}' for v in self.vessel_faults))

    # ---------------------------------------------------------------- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the touch-screen firmware sees: the agent slots plus display data (English texts, chart rows, lists)."""
        out = self.read('agent')
        msgs = [dict(m, text=_text_en(m['code']) if m['code'] != 'DOOR_OPEN' else f'Door longer open than {self.alarm_cfg["door"]["signal_s"]:.0f} s',
                     time=self._clock_text(self._clock_s() - self.t + m['time_s'])[:16]) for m in self._messages()]
        out.update({'host.fatal': 'FATAL' in self.dev_errors, 'host.powered': self.power, 'host.power_epoch': self.power_epoch, 'host.now_h': self.t / 3600.0, 'host.chart': self._panel_chart(),
                    'host.events': self._panel_events(), 'host.messages': msgs, 'host.message_text': msgs[0]['text'] if msgs else '',
                    'host.contamination_notice': bool(self.contamination == 'not_contaminated' and self.contamination_changed_s is not None
                                                      and self.t - self.contamination_changed_s < 86400.0),
                    'host.recent': copy.deepcopy(self.recent), 'host.alarm_cfg': copy.deepcopy(self.alarm_cfg), 'host.tasks': self._panel_tasks(),
                    'host.op_records': self._panel_records(), 'host.new_credentials': copy.deepcopy(self.last_credentials), 'host.last_pc': self._panel_last_pc()})
        return out

    def _panel_chart(self):
        rows = [r for r in self.chart if r['time_s'] >= self.t - 7 * 86400.0]
        k = max(1, len(rows) // 1500)
        rows = rows[::k]
        out = {}
        for f, key, sc, off in (('temperature', 'temperature_k', 1.0, -273.15), ('co2', 'co2_fraction', 100.0, 0.0), ('o2', 'o2_fraction', 100.0, 0.0), ('rh', 'rh', 100.0, 0.0)):
            out[f] = [[r['time_s'] / 3600.0, r[key] * sc + off] for r in rows if r[key] is not None]
        return out

    @staticmethod
    def _event_cat(e):
        """Filter category of an event in the event list (None: not listed there)."""
        if e['kind'] not in _EVENT_CAT:
            return None
        return 'warning' if e['code'].endswith('_WARNING') else _EVENT_CAT[e['kind']]

    def _panel_events(self):
        evs = [e for e in reversed(self.events) if e['kind'] in _EVENT_CAT][:300]
        out = []
        for i, e in enumerate(evs):
            age = self.t - e['time_s']
            c = self._event_cat(e)
            out.append(dict(idx=i, code=e['code'], cat=c, kind_text=c.capitalize(), text=_text_en(e['code']),
                            time=self._clock_text(self._clock_s() - self.t + e['time_s'])[:16],
                            age=f'{age / 60:.0f} min' if age < 3600 else f'{age / 3600:.0f} h' if age < 86400 else f'{age / 86400:.0f} d'))
        return out

    def _panel_tasks(self):
        names = {v: k for k, v in TASK_INTERVALS.items()}
        out = []
        for k, v in self.tasks.items():
            iv = names.get(v['interval_s'])
            due = k in self._tasks_due()
            out.append(dict(id=k, name=v['name'], interval=iv, interval_text=iv.capitalize() if iv else ('-' if v['interval_s'] is None else f'{v["interval_s"] / 86400:.0f} d'),
                            reminder_days=int(round(v['reminder_s'] / 86400.0)), warn=v['warn'], custom=v['custom'], procedure=bool(TASKS.get(k, {}).get('procedure')),
                            service=False, due=due, due_text='due' if due else '',
                            last_text='-' if v['last_done_s'] is None else self._clock_text(self._clock_s() - self.t + v['last_done_s'])[:10]))
        out.append(dict(id='epservices', name='Routine maintenance by epServices', interval=None, interval_text='-', reminder_days=0, warn=False, custom=False,
                        procedure=False, service=True, due=False, due_text='', last_text='-'))
        return out

    def _panel_records(self):
        out = []
        for r in reversed(self._cmd_operation_records_list()['records']):
            i = r['initial']
            out.append(dict(record_id=r['record_id'], name=r['name'], start_date=r['start_date'], finish_date=r['finish_date'], result=r['result'], n_events=len(r['events']),
                            user=r['user'] or '',
                            initial_text=f'{i["temperature_c"]:.1f} C, {i["co2_percent"]:.1f} % CO2, {i["o2_percent"]:.1f} % O2'))
        return out

    def _panel_last_pc(self):
        recs = [r for r in self.records.values() if r['kind'] == 'performance_check']
        if not recs:
            return None
        d = recs[-1]['details']
        if d.get('quantity') == 'temperature':
            return dict(displayed_text=f'{d["displayed_temperature_k"] - 273.15:.2f} °C', reference_text=f'{d["reference_temperature_k"] - 273.15:.2f} °C',
                        deviation_text=f'{d["deviation_k"]:+.2f} K')
        return dict(displayed_text=f'{100 * d["displayed_fraction"]:.2f} %', reference_text=f'{100 * d["reference_fraction"]:.2f} %',
                    deviation_text=f'{100 * d["deviation_fraction"]:+.2f} %')

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (these commands check before they change anything).  1.2.0: the caller rule
        of the direct path applies to the command a touch invokes (another agent's HTD: ACCESS_DENIED, the screen shows a dialog); navigation is free."""
        c = self._caller
        if c is not None and self._principals.get(c) == 'agent' and capability_id in _CONTROL_GUARDED and self.op is not None:
            owner = self._owners.get(self.op['op'])
            if owner is not None and owner != c and self._principals.get(owner) == 'agent':
                raise _panel_ui.PanelRejected('ACCESS_DENIED')
        try:
            validate(CAPS[capability_id]['input_schema'], arguments)
            return self.invoke(capability_id, **arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def _panel(self, gesture, *args):
        self._need_plant()
        try:
            return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_panel_touch(self, display_id, x, y, duration_s=0.1, to_x=None, to_y=None):
        self._need_plant()
        if self.power:
            self._touch()                                                       # automatic lock / logout due first, then the inactivity timers restart
        to = None if to_x is None or to_y is None else (int(to_x), int(to_y))
        return self._panel(self.panel.touch, display_id, int(x), int(y), float(duration_s), to)

    def _cmd_panel_switch(self, input_id, position):
        return self._panel(self.panel.switch, input_id, position)

    def _cmd_panel_capture(self, display_id):
        """Close-up camera of the screen: a PNG file named by its content (large data is returned as a file reference)."""
        import tempfile
        self._need_plant()
        data, w, h = self.panel.png_bytes(display_id)
        sha = hashlib.sha256(data).hexdigest()
        d = Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'{display_id}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.t, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))

    def _cmd_panel_frame(self):
        return dict(frame=self.panel.frame())

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_chart_read(self, since_s, max_points):
        rows = [r for r in self.chart if r['time_s'] >= since_s][:max_points]
        return dict(rows=copy.deepcopy(rows))

    def _cmd_records_list(self):
        return dict(records=[copy.deepcopy(self.records[k]) for k in sorted(self.records)])

    def _cmd_tasks_list(self):
        due = set(self._tasks_due())
        return dict(tasks=[dict(task=k, interval_s=v['interval_s'], last_done_s=v['last_done_s'], due=k in due) for k, v in self.tasks.items()])

    def _cmd_alarms_list(self):
        return dict(alarms=[dict(code=k, since_s=v['since_s'], message_zh=v['message']) for k, v in sorted(self.alarms.items())], sound=self.sound, relays=self._relays())

    def _cmd_provenance_read(self):
        out = []
        for key in ('media', 'cells', 'vessels', 'equilibrium', 'model'):
            p = ASSET_DIR / LIBS[key]
            if p.exists():
                d = json.loads(p.read_text())
                n = len(d.get('substances', d.get('vessels', d.get('parameters', {}))))
                out.append(dict(path=str(p.relative_to(ROOT)), sha256=hashlib.sha256(p.read_bytes()).hexdigest(), count=n))
        return dict(libraries=out)

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    # ---------------------------------------------------------------- alarms
    def _raise(self, code, message):
        if code not in self.alarms:
            self.alarms[code] = dict(since_s=self.t, message=message)
            if not (code == 'DOOR_OPEN' and self.dev['acoustic.mode'] == 'alarms'):   # the door alarm is an interface signal (p.79: 'Alarms' only)
                self.sound = True
            self._event('error' if code.startswith('E_') else 'alarm', code, message)
            sev = 'error' if code.startswith('E_') else 'warning' if code.endswith('_WARNING') else 'alarm'
            self.msgs = ([m for m in self.msgs if m['code'] != code] + [dict(code=code, severity=sev, time_s=self.t, text=message)])[-200:]

    def _clear(self, code):
        if code in self.alarms:
            del self.alarms[code]
            self._event('alarm', code + '_CLEARED', f'{code}已恢复')

    def _cond(self, code, active, message):
        if active:
            self._raise(code, message)
        else:
            self._clear(code)

    def _alarms_tick(self):
        cfg, st, t = self.alarm_cfg, self.status, self.t
        d_open = (t - self.door['opened_at']) if self.door['open_flag'] else 0.0
        self._cond('DOOR_OPEN', cfg['door']['enabled'] and d_open > cfg['door']['signal_s'], f'门打开超过{cfg["door"]["signal_s"]:.0f} s')
        self._cond('DOOR_LONG', d_open > LONG_DOOR_S, '6731FW.50 长时间开门（>5 min）：关门')
        htd = self.htd['phase'] != 'idle'
        held = htd or self.door['open_flag']                                    # value alarms wait while the outer door is open (door alarm instead) and pause after closing
        Ts = self.sens['T']
        tc = cfg['temperature']
        arm = tc['enabled'] and not held and t >= self.pause['temperature'] and Ts is not None
        self._cond('TEMP_LOW', arm and Ts < self.setp['T'] - tc['low_k'], f'温度低于报警限（{Ts - 273.15 if Ts else 0:.1f} °C）')
        self._cond('TEMP_HIGH', arm and Ts > self.setp['T'] + tc['high_k'], f'温度高于报警限（{Ts - 273.15 if Ts else 0:.1f} °C）')
        for g, key in (('co2', 'c'), ('o2', 'o')):
            gc = cfg[g]
            s = self.sens[key]
            armg = gc['enabled'] and self.ctrl[g] and not held and t >= self.pause[g] and s is not None
            self._cond(f'{g.upper()}_LOW', armg and s < self.setp[g] - gc['low'], f'{g.upper()}低于报警限（{100 * (s or 0):.1f} %）')
            self._cond(f'{g.upper()}_HIGH', armg and s > self.setp[g] + gc['high'], f'{g.upper()}高于报警限（{100 * (s or 0):.1f} %）')
        rc = cfg['rh']
        self._cond('RH_LOW', rc['enabled'] and not held and t >= self.pause['rh'] and self.sens['rh'] is not None and self.sens['rh'] < rc['low'],
                   f'相对湿度低于报警限（{100 * (self.sens["rh"] or 0):.1f} %）')
        self._cond('WATER_LOW', cfg['water_level']['enabled'] and not htd and t >= self.pause['water'] and not st['sensors_hot'] and not self._dead('wl') and self._water_low(), '水盘水量低于0.5 L：加水')
        for g, p_key, pre in (('co2', 'co2_supply_pa', 'CO2'), ('o2', 'n2_supply_pa', 'N2')):
            p = self.plant[p_key]
            used = self.ctrl[g]
            mon = self.ctrl['monitor'] and used
            self._cond(f'{pre}_PRESSURE_WARNING', mon and 0.02e6 <= p < 0.04e6, f'{pre}压力低于警告值0.04 MPa：检查气瓶')
            self._cond(f'{pre}_PRESSURE_LOW', mon and p < 0.02e6, f'{pre}压力低于报警值0.02 MPa：控制停止')
            self._cond(f'{pre}_PRESSURE_HIGH', used and p > 0.18e6, f'{pre}压力高于0.18 MPa：进气阀关闭')

    # ---------------------------------------------------------------- stepping
    def step(self, dt_s):
        """Advance the host clock: the chamber is integrated on the 1 s grid (k * 1 s) and the cultures on the 60 s grid; state between grid points
        is held, so any split of the same interval gives identical results."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            return
        while (self.tick + 1) * H_TICK <= t_end + 1e-9:
            self.tick += 1
            self.t = self.tick * H_TICK
            self._physics_tick()
            if self.tick % int(CULTURE_DT / H_TICK) == 0:
                self._culture_step()
            if self.tick % int(CHART_DT / H_TICK) == 0:
                self._chart_sample()
        self.t = t_end

    def _move_doors(self):
        """Outer-door open / close transitions detected on the tick grid; their times are the exact crossings of the analytic ramp."""
        d = self.door
        was_open = d['open_flag']
        now_open = self._doors_open()
        if now_open and not was_open:
            d['open_flag'] = True
            d['opened_at'] = min(self.t, self._cross_time('outer', 0.02))
            self._event('door', 'OUTER_OPEN', '外门打开（屏幕提示）')
            if self.htd['phase'] in ('heating', 'holding', 'cooling'):
                self._htd_fail('E_6731FW753', '6731FW.753 高温消毒时开了门：腔体很烫（烫伤危险），高温消毒中止，需重新启动')
        elif was_open and not now_open:
            d['open_flag'] = False
            d['opened_at'] = None
            d['closed_at'] = min(self.t, self._cross_time('outer', 0.02))
            for k, v in PAUSE['door'].items():
                self.pause[k] = max(self.pause[k], self.t + v)
            self._event('door', 'OUTER_CLOSED', '外门关上')

    def _exchange_rate(self):
        m = self.m
        po = min(1.0, self._ang('outer') / (math.pi / 4))
        pi_ = min(1.0, self._ang('inner') / (math.pi / 4))
        k = m['k_leak_per_s'] + m['k_port_per_s'] * sum(1 for v in self.ports.values() if not v)
        if 'gasket_leak' in self.faults:
            k += m['k_gasket_fault_per_s']
        if self._ang('inner') <= 1e-9 and self._ang('latch') > 1e-9:
            k += m['k_unlatched_per_s']
        k += m['k_door_open_per_s'] * min(po, pi_) + m['k_outer_only_per_s'] * po * (1.0 - pi_)
        return k

    def _glass_temperature(self, Ta):
        m = self.m
        po = min(1.0, self._ang('outer') / (math.pi / 4))
        dt = m['dt_glass_latched_k'] if self._latched() else m['dt_glass_unlatched_k']
        return (Ta - dt) * (1 - po) + po * (0.5 * (Ta + self._room()))

    def _physics_tick(self):
        m, c, h = self.m, self.ch, H_TICK
        P, Tr = self._P(), self._room()
        self._move_doors()
        seed = self._seed()
        # ---- sensors (lagged, noise at the reading) from the state at the start of the tick
        xc, xo, xw = self._xs()
        s = getattr(self, '_sens_state', None)
        if self.power:
            drift = 0.003 if 'ndir_drift' in self.faults else 0.0
            s['T'] = _lag(s['T'], c['Ta'], h, m['tau_t_sensor_s'])
            s['c'] = _lag(s['c'], xc * P / P_REF + drift, h, m['tau_ndir_s'])
            s['o'] = _lag(s['o'], xo * P / P_REF, h, m['tau_o2_sensor_s'])
            s['rh'] = _lag(s['rh'], min(1.0, xw * P / p_sat(c['Ta'])), h, m['tau_rh_covered_s'] if self.rh_cover else m['tau_rh_sensor_s'])
            hot = self.status['sensors_hot']
            if hot and c['Tw'] < HOT_SENSOR_K:
                self.status['sensors_hot'] = False
                self.status['co2_init_until'] = self.t + INIT_S['co2']
                self.status['o2_init_until'] = self.t + INIT_S['o2']
                for code in ('E_6731FW202', 'E_6731FW302', 'E_6731FW402', 'E_6731FW452'):
                    self._clear(code)
            gT = _gauss(seed, self.tick, 1)
            self.sens['T'] = s['T'] + m['noise_t_k'] * gT
            self.sens['c'] = None if self._initializing('co2') else max(0.0, s['c'] + m['noise_co2'] * _gauss(seed, self.tick, 2))
            self.sens['o'] = None if self._initializing('o2') or not self._o2_on() else max(0.0, s['o'] + m['noise_o2'] * _gauss(seed, self.tick, 3))
            self.sens['rh'] = None if self.status['sensors_hot'] else min(1.0, max(0.0, s['rh'] + m['noise_rh'] * _gauss(seed, self.tick, 4)))
            self._device_faults_tick()
            for key in ('T', 'c', 'o', 'rh'):
                if self._dead(key):
                    self.sens[key] = None                                     # the failed / out-of-range sensor gives no valid reading
        # ---- control
        self._control()
        # ---- fluxes
        kappa = self._exchange_rate()
        xa = self._ambient_x()
        n = c['nc'] + c['no'] + c['nw'] + c['nr']
        f_c = max(0.0, self.plant['co2_supply_pa']) / 1.0e5 if self.plant['co2_supply_pa'] <= 0.18e6 else 0.0
        f_n = max(0.0, self.plant['n2_supply_pa']) / 1.0e5 if self.plant['n2_supply_pa'] <= 0.18e6 else 0.0
        q_c = self.u['c'] * m['q_co2_max_mol_s'] * f_c * (0.0 if 'co2_valve_stuck_closed' in self.faults else 1.0)
        q_n = self.u['n'] * m['q_n2_max_mol_s'] * f_n
        V, Tww = self.tray['V'], self.tray['T']
        E = m['h_m_tray_m_s'] * TRAY_AREA * (p_sat(Tww) - xw * P) / (R * Tww) if V > 0 else 0.0
        E = min(E, V * RHO_WATER / M_WATER / h)
        Tg = self._glass_temperature(c['Ta'])
        cond = m['h_m_glass_m_s'] * GLASS_AREA * (xw * P - p_sat(Tg)) / (R * Tg)
        if cond < 0:
            cond = max(cond, -c['cond'] / h)
        # coldest surfaces (door frame / gasket region, access-port plugs): condensate runs back into the tray; together with the tray evaporation it
        # sets the ~92.5 % steady humidity (manual 13.4.4)
        Tcs = c['Ta'] - m['dt_cold_spot_k']
        drain = max(0.0, m['g_cold_spot_m3_s'] * (xw * P - p_sat(Tcs)) / (R * Tcs))
        src_w = self.vapour_src
        # ---- vessel heat (chamber vessels)
        Tenv = 0.5 * (c['Ta'] + c['Tw'])
        Qv = 0.0
        for k in self._in_chamber():
            v = self.vessels[k]
            Cv = v['V'] * RHO_WATER * C_WATER + v['mass'] * 1300.0
            q = v['G'] * (Tenv - v['Tm'])
            v['Tm'] += h * q / Cv
            Qv += q
        # ---- energy
        Qh = self.u['h'] * m['heater_max_w']
        Qint = m['internal_heat_k'] * m['ua_wall_w_k'] if self.power else 0.0   # electronics / door-frame heat into the chamber shell (part of electronics_w)
        Qaw = m['g_air_wall_w_k'] * (c['Tw'] - c['Ta'])
        Qex = kappa * n * CP_GAS * (c['Ta'] - Tr)
        Qgas = (q_c + q_n) * CP_GAS * (c['Ta'] - Tr)
        Qdoor = m['g_door_w_k'] * (c['Ta'] - Tr)
        Qua = m['ua_wall_w_k'] * (c['Tw'] - Tr)
        Qtray = m['g_tray_w_k'] * (c['Tw'] - Tww) if V > 0 else 0.0
        Ca = n * CP_GAS
        Ta_new = c['Ta'] + h / Ca * (Qaw - Qex - Qgas - Qdoor - 0.5 * Qv)
        Tw_new = c['Tw'] + h / m['c_wall_j_k'] * (Qh + Qint - Qaw - Qua - Qtray - 0.5 * Qv + LV * drain)
        if V > 0:
            Ct = V * RHO_WATER * C_WATER + TRAY_STEEL_J_K
            self.tray['T'] = Tww + h / Ct * (Qtray - LV * E)
            dV = (E - drain) * h * M_WATER / RHO_WATER
            self.tray['V'] = max(0.0, V - dV)
        else:
            self.tray['T'] = Tw_new
            self.tray['V'] = drain * h * M_WATER / RHO_WATER
        # ---- gas moles: exchange, injection, vapour; then vent / draw to the room pressure at the new air temperature
        ex = kappa * h
        out = dict(c=ex * c['nc'], o=ex * c['no'], w=ex * c['nw'], r=ex * c['nr'])
        inn = dict(c=ex * n * xa['c'], o=ex * n * xa['o'], w=ex * n * xa['w'], r=ex * n * (1 - xa['c'] - xa['o'] - xa['w']))
        nc = c['nc'] - out['c'] + inn['c'] + q_c * h
        no = c['no'] - out['o'] + inn['o']
        nw = c['nw'] - out['w'] + inn['w'] + (E - cond - drain + src_w) * h
        nr = c['nr'] - out['r'] + inn['r'] + q_n * h
        c['cond'] = max(0.0, c['cond'] + cond * h)
        tot = nc + no + nw + nr
        target = self._n_total(Ta_new)
        vent_c = vent_w = 0.0
        if tot > target:
            f = (tot - target) / tot
            vent_c, vent_w = f * nc, f * nw
            nc, no, nw, nr = nc * (1 - f), no * (1 - f), nw * (1 - f), nr * (1 - f)
        else:
            d_ = target - tot
            nc, no, nw, nr = nc + d_ * xa['c'], no + d_ * xa['o'], nw + d_ * xa['w'], nr + d_ * (1 - xa['c'] - xa['o'] - xa['w'])
            inn['c'] += d_ * xa['c']
            inn['w'] += d_ * xa['w']
        s_ = p_sat(Ta_new) / P
        fog = max(0.0, nw - s_ * (nc + no + nr) / (1.0 - s_)) if s_ < 0.99 else 0.0   # supersaturated air condenses on the walls, runs into the tray
                                                                                 # (near / above 100 C water vapour cannot condense at room pressure)
        if fog > 0:
            nw -= fog
            Tw_new += LV * fog / m['c_wall_j_k']
            self.tray['V'] += fog * M_WATER / RHO_WATER
        c.update(Ta=Ta_new, Tw=Tw_new, nc=nc, no=no, nw=nw, nr=nr)
        # ---- ledger
        L = self.led
        L['heater_energy_j'].add(Qh * h)
        L['electrical_energy_j'].add((Qh + (m['electronics_w'] if self.power else 0.0)) * h)
        L['co2_injected_mol'].add(q_c * h)
        L['n2_injected_mol'].add(q_n * h)
        L['co2_exchanged_out_mol'].add(out['c'] + vent_c)
        L['co2_exchanged_in_mol'].add(inn['c'])
        L['water_evaporated_tray_mol'].add(E * h)
        L['water_evaporated_vessels_mol'].add(src_w * h)
        L['water_condensed_mol'].add(cond * h)
        L['water_drained_to_tray_mol'].add(drain * h + fog)
        L['water_vented_mol'].add(out['w'] + vent_w - inn['w'])
        L['heat_loss_wall_j'].add(Qua * h)
        L['heat_loss_exchange_j'].add(Qex * h)
        L['heat_loss_door_j'].add(Qdoor * h)
        L['heat_to_vessels_j'].add(Qv * h)
        L['latent_tray_j'].add(LV * E * h)
        L['gas_heating_j'].add(Qgas * h)
        # ---- HTD phases, limiter, errors, alarms
        self._supervise()
        if self.power:
            self._alarms_tick()

    def _control(self):
        m, c, st, u = self.m, self.ch, self.status, self.u
        if not self.power or 'FATAL' in self.dev_errors:                     # 1.2.0: a fatal software error stops all control outputs (assumption)
            u.update(h=0.0, c=0.0, n=0.0)
            return
        h = H_TICK
        Ts = self.sens['T'] if self.sens['T'] is not None else c['Ta']
        Tws = c['Tw']
        ph = self.htd['phase']
        closed_for = self.t - self.door['closed_at'] if not self.door['open_flag'] else -1.0
        hold = self.door['open_flag'] or closed_for < m['door_hold_s']         # door opening: air loop frozen (no wall over-heating, no overshoot)
        if st['shutdown'] or 'heater_failure' in self.faults or self._dead('T'):   # 1.2.0: no valid temperature reading: no heating (assumption)
            uh = 0.0
        elif 'heater_stuck_on' in self.faults:
            uh = 1.0
        elif ph in ('heating', 'holding'):
            uh = min(1.0, max(0.0, m['kp_wall'] * (HTD_WALL_K + 0.5 - Tws)))
        elif ph in ('cooling', 'post_htd_cooling'):
            uh = 0.0
        elif hold:
            off = min(m['wall_band_hi_k'], max(m['wall_band_lo_k'], self.pid['I_T']))
            uh = min(1.0, max(0.0, m['kp_wall'] * (self.setp['T'] + off - Tws)))
        else:
            e = self.setp['T'] - Ts
            I = self.pid['I_T'] + m['ki_air'] * e * h
            I = min(m['wall_band_hi_k'], max(m['wall_band_lo_k'], I))
            off = min(m['wall_band_hi_k'], max(m['wall_band_lo_k'], m['kp_air'] * e + I))
            if off == m['kp_air'] * e + I or (off >= m['wall_band_hi_k'] and e < 0) or (off <= m['wall_band_lo_k'] and e > 0):
                self.pid['I_T'] = I
            uh = min(1.0, max(0.0, m['kp_wall'] * (self.setp['T'] + off - Tws)))
        u['h'] = uh
        # gas control: paused while the outer door is open and for a short delay after closing; CO2 waits for the temperature after a power cut
        base_ok = ph == 'idle' and not self._doors_open() and closed_for >= m['gas_restart_delay_s']
        if st['co2_gate'] and Ts >= self.setp['T'] - 0.1:
            st['co2_gate'] = False
        for g, key, sup in (('co2', 'c', 'co2_supply_pa'), ('o2', 'o', 'n2_supply_pa')):
            p = self.plant[sup]
            ok = base_ok and self.ctrl[g] and not self._initializing(g) and not st[f'{g}_failed'] and p <= 0.18e6 and not (self.ctrl['monitor'] and p < 0.02e6) \
                and not self._dead(key + '_ctl')
            if g == 'co2':
                ok = ok and not st['co2_gate']
            s = self.sens[key]
            if not ok or s is None:
                u['c' if g == 'co2' else 'n'] = 0.0
                continue
            key_i, kp, ki = ('I_c', m['kp_co2'], m['ki_co2']) if g == 'co2' else ('I_o', m['kp_o2'], m['ki_o2'])
            e = (self.setp['co2'] - s) if g == 'co2' else (s - self.setp['o2'])
            I = min(1.0, max(0.0, self.pid[key_i] + ki * e * h))
            raw = kp * e + I
            if 0.0 < raw < 1.0 or (raw >= 1.0 and e < 0) or (raw <= 0.0 and e > 0):   # integrate only while the valve is not saturated (no wind-up)
                self.pid[key_i] = I
            u['c' if g == 'co2' else 'n'] = min(1.0, max(0.0, kp * e + self.pid[key_i]))

    def _supervise(self):
        c, st, h = self.ch, self.status, H_TICK
        ph = self.htd['phase']
        if ph != 'idle':
            self.htd['max_k'] = max(self.htd['max_k'], c['Tw'])
        if ph == 'heating':
            if c['Tw'] >= HTD_WALL_K - 1.0:
                self.htd.update(phase='holding', t_phase=self.t)
                self.records[self.op['record_id']]['details']['phases']['holding'] = self.t
                self._event('htd', 'HTD_HOLD', '达到180 °C：保持2 h', ref=self.op['record_id'])
            elif self.t - self.htd['t_phase'] > HTD_HEAT_TIMEOUT_S:
                self._htd_fail('E_6731FW110', '6731FW.110 180 °C未按时达到：检查环境条件（水盘须为空）')
        elif ph == 'holding':
            if c['Tw'] >= HTD_WALL_K - 1.0:
                self.htd['hold_s'] += h
            if self.htd['hold_s'] >= HTD_HOLD_S:
                self.htd.update(phase='cooling', t_phase=self.t)
                self.records[self.op['record_id']]['details']['phases']['cooling'] = self.t
                self._event('htd', 'HTD_COOL', '消毒保持结束：冷却到培养温度', ref=self.op['record_id'])
        elif ph == 'cooling':
            if c['Tw'] <= self._htd_end_k():
                self._htd_finish('successful')
        elif ph == 'post_htd_cooling':
            if c['Tw'] <= self._htd_end_k():
                self.htd['phase'] = 'idle'
        if not self.power:
            return
        Ts = self.sens['T']
        if ph == 'idle':
            if Ts is not None and abs(Ts - self.setp['T']) < 0.3:
                st['reached_setpoint'] = True
            if c['Tw'] > self.setp['T'] + 10.0 and not st['shutdown']:
                st['shutdown'] = True
                self._raise('E_6731FW111', '6731FW.111 温度限制器检测到超温（>设定值+10 K）：加热停止，请重启')
            if st['reached_setpoint'] and c['Tw'] < self.setp['T'] - 5.0 and self.u['h'] >= 1.0 - 1e-9 or 'heater_failure' in self.faults and st['reached_setpoint'] and c['Tw'] < self.setp['T'] - 5.0:
                self.timers['subnormal'] += h
            else:
                self.timers['subnormal'] = 0.0
            if self.timers['subnormal'] > SUBNORMAL_S and not st['shutdown']:
                st['shutdown'] = True
                self._raise('E_6731W112', '6731W.112 温度限制器检测到欠温（<设定值−5 K）：请重启，设定值至少高于室温4 K')
            if Ts is not None and Ts > self.setp['T'] + 0.5 and self.u['h'] <= 1e-9:
                self.timers['t_high'] += h
            else:
                self.timers['t_high'] = 0.0
            self._cond('E_6731FW108', self.timers['t_high'] > SETPOINT_NOT_REACHED_S, '6731FW.108 设定值达不到：降温太慢（室温与设定值相差不足4 K）')
            for g, key in (('co2', 'c'), ('o2', 'o')):
                s = self.sens[key]
                p_sup = self.plant['co2_supply_pa' if g == 'co2' else 'n2_supply_pa']
                stopped = self.ctrl['monitor'] and p_sup < 0.02e6                       # stopped by the pressure monitor: resumes by itself
                active = self.ctrl[g] and not st[f'{g}_failed'] and s is not None and not self._doors_open() and not stopped and abs(s - self.setp[g]) > 0.005
                if active and (g == 'co2' and not st['co2_gate'] or g == 'o2'):
                    self.timers[f'{g}_off'] += h
                else:
                    self.timers[f'{g}_off'] = 0.0
                if self.timers[f'{g}_off'] > CONTROL_TIMEOUT_S:
                    st[f'{g}_failed'] = True
                    self.timers[f'{g}_off'] = 0.0
                    self.pid['I_c' if g == 'co2' else 'I_o'] = 0.0
                    self._raise('E_6731FW214' if g == 'co2' else 'E_6731FW314', f'{"6731FW214 CO2" if g == "co2" else "6731FW314 O2"}控制超时：设定值未按时达到（检查气源、泄漏与接入孔）')

    def _chart_sample(self):
        if len(self.chart) >= MAX_CHART:
            self.chart.pop(0)
        on = self.power
        self.chart.append(dict(time_s=self.t, temperature_k=self.sens['T'] if on else None, co2_fraction=self.sens['c'] if on else None,
                               o2_fraction=self.sens['o'] if on else None, rh=self.sens['rh'] if on else None, door_open=self._doors_open()))

    # ---------------------------------------------------------------- cultures
    def _vessel_env(self, v):
        if v['location'] == 'bench':
            xa = self._ambient_x()
            P = self._P()
            return dict(T=self._room(), p_c=xa['c'] * P, p_o=xa['o'] * P, p_w=xa['w'] * P)
        c = self.ch
        xc, xo, xw = self._xs()
        P = self._P()
        return dict(T=0.5 * (c['Ta'] + c['Tw']), p_c=xc * P, p_o=xo * P, p_w=xw * P)

    def _vessel_ph(self, v):
        V, T = v['V'], v['Tm']
        if 'n_c' not in v:
            return medium_ph(v['alk'] / V, v['co2'] / V, v['hep'] / V, v['phos'] / V, T, v['lac'] / V)
        Vh = max(1e-7, v['V_int'] - V)                                         # sealed: partition with the headspace (equilibrium within the 60 s step)
        ph, c_aq = sealed_state(v['alk'] / V, v['n_c'], V, Vh, v['hep'] / V, v['phos'] / V, v['lac'] / V, T)
        v['co2'] = c_aq * V
        p_o = v['n_o'] / (henry_o2(T) * V + Vh / (R * T))
        v['o2'] = henry_o2(T) * p_o * V
        return ph

    def _pericellular_o2(self, v, cl):
        if cl is None or v['N'] <= 0:
            return v['o2'] / v['V']
        depth = v['V'] / v['area']
        flux = GROWTH['our_mol_s'] * v['N'] / v['area']
        return max(0.0, v['o2'] / v['V'] - flux * depth / diffusion('o2', v['Tm']) * (1.0 if cl['growth_mode'] == 'adherent' else 0.5))

    def _culture_step(self):
        dt = CULTURE_DT
        g = GROWTH
        src = 0.0
        for k in sorted(self.vessels):
            v = self.vessels[k]
            env = self._vessel_env(v)
            if v['location'] == 'bench':                                       # bench: medium temperature relaxes to the room (chamber vessels on the 1 s grid)
                Cv = v['V'] * RHO_WATER * C_WATER + v['mass'] * 1300.0
                v['Tm'] = _lag(v['Tm'], env['T'], dt, Cv / v['G'])
            T = v['Tm']
            V = v['V']
            depth = V / v['area']
            for gas, key, pk in (('co2', 'co2', 'p_c'), ('o2', 'o2', 'p_o')):
                if v['tau_hs'] is None:                                        # sealed flask: no exchange (headspace partition in _vessel_ph)
                    continue
                tau = v['tau_hs'] + 4.0 * depth ** 2 / (math.pi ** 2 * diffusion(gas, T))
                eq_ = (henry_co2(T) if gas == 'co2' else henry_o2(T)) * env[pk] * V
                v[key] = _lag(v[key], eq_, dt, tau)
            ev = v['g_vap'] * max(0.0, p_sat(T) - env['p_w']) / (R * T)       # mol/s
            dV = min(ev * dt * M_WATER / RHO_WATER, 0.5 * V)
            v['V'] = V - dV
            v['evap_mol_s'] = ev
            T_lid = self.ch['Ta'] if v['location'] != 'bench' else env['T']
            humid_headspace = v['closure'] != 'vented_cap' or env['p_w'] / p_sat(T_lid) > 0.85   # a vented cap exchanges its headspace with the chamber
            v['lid_fog'] = humid_headspace and T > T_lid + (0.3 if v['location'] != 'bench' else 1.0)
            if v['location'] != 'bench':
                src += ev
            self.led['glucose_consumed_mol'].add(self._grow(v, T, dt))
            v['pH'] = self._vessel_ph(v)
        self.vapour_src = src

    def _grow(self, v, T, dt):
        """Cells and bacteria over one culture step; returns the glucose consumed (mol)."""
        g = GROWTH
        V = v['V']
        cl = cell_lines().get(v['cell_line']) if v['cell_line'] else None
        glc0 = v['glc']
        used = 0.0
        if v['B'] > 0 or v['vessel_id'] in self.vessel_faults:                 # bacterial contamination (fault)
            fb = (v['glc'] / V) / (v['glc'] / V + 0.1)
            fb *= min(1.0, max(0.0, (v['pH'] - g['bacteria_stop_ph']) / (g['bacteria_full_ph'] - g['bacteria_stop_ph'])))   # acid stops the bacteria
            B1 = v['B'] * math.exp(math.log(2) / g['bacteria_doubling_s'] * fb * dt) if T > 283.15 else v['B']
            q = min(v['glc'], g['bacteria_q_glc'] * 0.5 * (v['B'] + B1) * dt * (1.0 if fb > 0 else 0.0))
            v['glc'] -= q
            v['lac'] += 1.8 * q                                                 # organic acids, lumped with lactic acid (weak acid in the charge balance)
            v['B'] = B1
            used += q
        if cl is None or v['N'] <= 0:
            return used
        Topt = cl['culture_temperature_k']
        dTk = T - Topt
        if dTk < -g['arrest_below_k'] or T < g['freeze_k']:
            fT = 0.0
        elif dTk <= 0:
            fT = g['q10'] ** (dTk / 10.0)
        elif dTk <= g['decline_above_k']:
            fT = 1.0
        elif dTk < g['arrest_above_k']:
            fT = (g['arrest_above_k'] - dTk) / (g['arrest_above_k'] - g['decline_above_k'])
        else:
            fT = 0.0
        pH = v['pH']
        dph = max(0.0, g['ph_band'][0] - pH, pH - g['ph_band'][1])                # flat optimum 7.1-7.5, Gaussian decline outside
        fpH = math.exp(-(dph / g['ph_sigma']) ** 2)
        G = v['glc'] / V
        fG = G / (G + g['glc_km'])
        osm = v['osm0'] * v['V0'] / V
        fO = 1.0 if osm <= g['osm_ok'] else max(0.0, (g['osm_stop'] - osm) / (g['osm_stop'] - g['osm_ok']))
        C = self._pericellular_o2(v, cl)
        fO2 = C / (C + g['o2_km'])
        fL = 1.0 / (1.0 + (v['lac'] / V) / g['lac_ki'])
        if cl['growth_mode'] == 'adherent':
            Nmax = v['area'] * cl['max_density']['per_area_m2']
        else:
            Nmax = V * cl['max_density']['per_volume_m3']
        fC = max(0.0, 1.0 - (v['N'] / Nmax) ** g['contact_exponent'])           # growth continues until near confluence (Richards-type)
        lag = 0.0 if self.t - v['t_seed'] < g['lag_s'] else 1.0
        mu = (math.log(2.0) / cl['doubling_time_s'] + g['base_death_per_s']) * fT * fpH * fG * fO * fO2 * fL * fC * lag
        kd = g['base_death_per_s']
        if dTk > g['heat_death_above_k']:
            kd += g['heat_death_per_s_k'] * (dTk - g['heat_death_above_k'] + 1.0)
        if T < g['freeze_k']:
            kd += 1e-3
        if pH < g['acid_death_ph']:
            kd += g['acid_death_per_s'] * (1.0 + 10.0 * (g['acid_death_ph'] - pH))
        if G < g['starve_below']:
            kd += g['starve_death_per_s']
        if osm > g['osm_death']:
            kd += g['osm_death_per_s']
        if v['vessel_id'] in self.vessel_faults and v['B'] / V > 1e12:
            kd += g['bacteria_death_per_s']
        N0 = v['N']
        N1 = N0 * math.exp((mu - kd) * dt)
        births = N0 * (math.exp(mu * dt) - 1.0) if mu > 0 else 0.0
        need = g['q_glc_mol_s'] * 0.5 * (N0 + N1) * dt * (1.0 if fT > 0 else 0.2)
        q = min(v['glc'], need)
        if need > 0 and q < need:
            N1 = min(N1, N0)
        v['glc'] -= q
        v['lac'] += g['y_lac'] * q                                             # lactic acid: weak acid (pKa 3.86) in the charge balance
        q_o = min(g['our_mol_s'] * 0.5 * (N0 + N1) * dt * fO2 * (1.0 if fT > 0 else 0.2), v['n_o'] if 'n_o' in v else v['o2'])
        if 'n_c' in v:                                                         # respiration: O2 from the sealed headspace, CO2 (RQ 0.9) into it
            v['n_o'] -= q_o
            v['n_c'] += g['rq'] * q_o
        else:
            v['o2'] -= q_o
            v['co2'] += g['rq'] * q_o
        self.led['lactate_produced_mol'].add(g['y_lac'] * q)
        self.led['cells_divided'].add(max(0.0, births))
        v['D'] += max(0.0, N0 * kd * dt)
        v['N'] = max(0.0, N1)
        v['phase'] = 'lag' if lag == 0 else ('death' if mu < kd else 'stationary' if fC < 0.05 or fG < 0.05 else 'growth')
        return used + (glc0 - used - v['glc'])


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
ASYNC = {'htd.start'}
VESSEL_SKILLS = ('vessel.place', 'vessel.remove', 'vessel.observe', 'vessel.discard')   # hand / robot handling of the culture vessels
_OWNED_VESSEL = set()      # 1.2.0 (lead rule 10-19): handling vessels is a physical action - not owner-guarded; only the device's own preconditions refuse it
_AGENT_COMMANDS = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] == 'device_command' and k not in UNSUPPORTED
                   and not k.startswith(('panel.', 'power.'))}
REMOTE_CAPS = set(_AGENT_COMMANDS)                                 # 1.2.0: commands of the external-software interface (Enable remote access, p.77)
# 1.2.0 (lead rule 10-19): commands of another agent that would stop or change a running high-temperature disinfection.  There are none: the device
# itself locks the set points, gas control and a second start during HTD (HTD_RUNNING), the HTD ends only by its owner's cancel (session.cancel,
# owner-only), the door or the mains switch (both physical, never refused), and the incubator has no soft power-off.  Every other command follows
# the device's own rules, on the touch screen and the direct path alike (the session and panel_invoke guards below stay for the rule's structure).
_CONTROL_GUARDED = set()


class EppendorfCellxpertC170iSession:
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
        meta = copy.deepcopy(export_eppendorf_cellxpertc170i_contract_metadata())
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
        return h.op is not None and self._foreign(h.op['op'])

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['operation.started_by_other_agent'] = self._role == 'agent' and self._foreign_op()
        if self._role == 'agent':
            values['vessels.in_chamber'] = [x for x in values['vessels.in_chamber'] if not self._foreign(x.split('@')[0])]
            values['vessels.on_bench'] = [x for x in values['vessels.on_bench'] if not self._foreign(x)]
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
        if capability_id in REMOTE_CAPS and not h.dev['network.remote_access']:
            raise ActionRejected('REMOTE_DISABLED')                            # 1.2.0: 'Enable remote access' off: an agent's commands are refused, reads stay
        if capability_id in _CONTROL_GUARDED and self._foreign_op():
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
            if not isinstance(capability_id, str) or capability_id not in CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id]['input_schema'], arguments)
            self._guard(capability_id, arguments)
            h._caller = self._principal
            try:
                result = h.invoke(capability_id, **arguments)
            finally:
                h._caller = None
            if self._role == 'agent':
                if capability_id == 'records.list':
                    result = dict(records=[r for r in result['records'] if not self._foreign(r['record_id'])])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_ref')) and e['kind'] != 'fault'], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
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
        rec = copy.deepcopy(h.records[op['record_id']])
        return dict(operation_id=operation_id, status='succeeded' if op['phase'] == 'completed' else op['phase'], result=rec)

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


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（评测者能力或真值槽；另一agent的高温消毒操作只能由其发起者poll/cancel）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'ACCESS_PORT_OPEN': '接入孔未插堵头（高温消毒前须插好，否则180 °C热气外泄、孔道不被消毒）',
    'MISSING_REFERENCE': '性能检查缺少该量的外部读数（温度用reference_temperature_k，CO2/O2用reference_fraction）', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '市电不在100–127 V或220–240 V ±10 %（或断电）',
    'REMOTE_DISABLED': '设置中Enable remote access已关闭：外部软件（agent）的命令被拒，读取不受影响；在触摸屏上重新开启（1.2.0）',
    'POWER_OFF': '培养箱未通电', 'HTD_RUNNING': '高温消毒进行中（或其后冷却中）', 'INVALID_INCREMENT': '设定值不是0.1 K / 0.1 %的整数倍',
    'SENSOR_INITIALIZING': '传感器初始化中（开机或高温消毒后CO2 30 min、O2 10 min），不能改设定值', 'NO_CONTROL_ERROR': '该气体控制没有出错',
    'INVALID_DOOR_SIGNAL': '门报警时间只能是15、30、45、60或90 s', 'INVALID_ALARM_LIMIT': '报警限超出允许范围（温度0.1–5 K，CO2/O2 0.1–5 %，RH下限50–95 %）',
    'TASK_REQUIRES_PROCEDURE': '高温消毒与性能检查任务由各自的程序完成', 'NOT_AT_DEFAULT_SETPOINT': '性能检查需先把该量设为默认值（37 °C、5 % CO2、10 % O2且O2控制启用）',
    'NOT_STABLE': '没有显示值或门开着，不能记录性能检查', 'RECORDS_FULL': '记录数已达上限', 'BUSY': '已有操作进行中',
    'VESSELS_IN_CHAMBER': '腔内还有培养容器（高温消毒前须取出）', 'WATER_IN_TRAY': '水位传感器检测到水盘有水（高温消毒前须倒空）',
    'RH_SENSOR_UNCOVERED': '湿度传感器未盖保护罩', 'DOORS_NOT_CLOSED': '门没关好（外门关闭、内门关闭且门闩水平）', 'HEATING_SHUTDOWN': '温度限制器已动作，需先重启',
    'INNER_DOOR_OPEN': '内门开着（先关内门）', 'OUTER_DOOR_CLOSED': '外门没有打开到位', 'LATCH_CLOSED': '内门门闩未打开', 'DOORS_NOT_OPEN': '外门与内门需都已打开',
    'TRAY_OVERFLOW': '水盘最多2.5 L', 'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '容器数量超过上限', 'UNKNOWN_MEDIUM': '培养基库中没有该培养基',
    'UNKNOWN_CELL_LINE': '细胞系库中没有该细胞系', 'CELL_LINE_REQUIRED': '接种细胞需指定细胞系', 'CLOSURE_MISMATCH': '该容器不能用这种盖子',
    'VOLUME_OUT_OF_RANGE': '培养基体积不在推荐量的0.25–3倍之内', 'SEEDING_ABOVE_CONFLUENCE': '贴壁细胞接种数超过汇合数', 'UNKNOWN_ITEM': '容器不存在',
    'VESSEL_NOT_ON_BENCH': '容器不在实验台上', 'VESSEL_NOT_IN_CHAMBER': '容器不在培养箱内', 'SHELF_FULL': '该层搁板放不下（面积）', 'SHELF_OVERLOAD': '该层搁板超过10 kg',
    'NOT_VISIBLE': '外门关着，看不到腔内容器', 'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作已结束',
    'UNKNOWN_SETTING': '没有该设备设置键', 'INVALID_SETTING': '设置值不符合该键的类型或选项', 'AUTOMATIC_TIME': '自动日期时间开着：先关掉再手动设定',
    'INVALID_DATE': '日期或时间无效（结束日期不能早于开始日期）', 'USER_MANAGEMENT_ON': '用户管理已启用', 'NO_USER_MANAGEMENT': '用户管理未启用',
    'NOT_ADMIN': '需要已登录的管理员（或本人）', 'WRONG_SECRET': 'PIN/密码错误', 'INVALID_SECRET': 'PIN须为4–8位数字，密码6–16个字符',
    'UNKNOWN_USER': '没有该用户ID', 'USER_EXISTS': '用户名或用户ID已存在', 'USERS_FULL': '最多999个用户账户', 'SELF_DELETE': '已登录的管理员不能删除自己的账户',
    'NOT_LOGGED_IN': '没有已登录的用户', 'NO_MESSAGE': '信息栏没有未确认的消息', 'UNKNOWN_TASK': '没有该周期任务', 'NOT_CUSTOM_TASK': '只能删除自定义的周期任务',
    'TASKS_FULL': '最多4个自定义周期任务',
}
_OPS = ['operation.kind', 'operation.progress']
_INIT = ['status.co2_initializing', 'status.o2_initializing']
_ALARM = ['alarms.active', 'alarm.sound', 'bms.relays', 'messages.unacknowledged']
_THERMAL = ['sensor.temperature_k', 'display.temperature_c', 'truth.air_temperature_k', 'truth.wall_temperature_k', 'truth.water_temperature_k', 'truth.heater_power_w',
            'status.heating_shutdown']
_GAS = ['sensor.co2_fraction', 'display.co2_percent', 'truth.co2_fraction', 'sensor.o2_fraction', 'truth.o2_fraction', 'status.co2_control_failed', 'status.o2_control_failed']
_HUMID = ['truth.rh', 'sensor.rh', 'truth.condensate_kg']
_WATER = ['water_tray.volume_m3', 'water_tray.level_m', 'sensor.water_low', 'sensor.water_present', 'truth.water_temperature_k']
_DOOR = ['door.outer_angle_rad', 'door.inner_angle_rad', 'door.latch_angle_rad', 'door.outer_state', 'door.inner_state', 'door.latched', 'door.outer_open_s']
_HTD = ['htd.phase', 'htd.remaining_s', 'htd.last_result', 'tasks.due'] + _OPS
_PHYS = _THERMAL + _GAS + _HUMID + _WATER + _INIT + _ALARM
_PWR = ['power.enabled', 'sensor.temperature_k', 'sensor.co2_fraction', 'sensor.o2_fraction', 'sensor.rh', 'sensor.water_low', 'sensor.water_present', 'sensor.co2_supply_pa', 'sensor.n2_supply_pa',
        'display.temperature_c', 'display.co2_percent', 'status.heating_shutdown', 'status.fatal_error', 'truth.heater_power_w', 'users.state', 'screen.locked'] + _INIT + _ALARM + _HTD
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.air_temperature_k', 'truth.wall_temperature_k', 'truth.water_temperature_k', 'truth.co2_fraction', 'truth.o2_fraction', 'truth.rh'] + _PWR,
    'power.set': _PWR, 'power.press_switch': _PWR,
    'temperature.set': ['setpoint.temperature_k'], 'co2.set': ['setpoint.co2_fraction'], 'o2.set': ['setpoint.o2_fraction'],
    'co2.control': ['control.co2_enabled'], 'o2.control': ['control.o2_enabled'], 'gas.pressure_monitor': ['gas.pressure_monitor_enabled'],
    'gas.reset': ['status.co2_control_failed', 'status.o2_control_failed'] + _ALARM, 'alarm.mute': ['alarm.sound'],
    'htd.start': ['htd.phase', 'htd.remaining_s'] + _OPS, 'contamination.set_status': ['contamination.status'], 'task.configure': ['tasks.due'], 'task.complete': ['tasks.due'],
    'performance_check.record': ['tasks.due'], 'door.outer': ['door.outer_state'], 'door.latch': [], 'door.inner': ['door.inner_state'],
    'water_tray.fill': ['water_tray.volume_m3', 'water_tray.level_m', 'sensor.water_low', 'sensor.water_present', 'truth.water_temperature_k', 'tasks.due'],
    'water_tray.empty': ['water_tray.volume_m3', 'water_tray.level_m', 'sensor.water_low', 'sensor.water_present'], 'rh_sensor.cover': ['rh_sensor.cover_on'],
    'access_port.plug': ['access_ports.unplugged'], 'vessel.provide': ['vessels.on_bench'], 'vessel.place': ['vessels.in_chamber', 'vessels.on_bench'],
    'vessel.remove': ['vessels.in_chamber', 'vessels.on_bench'], 'vessel.discard': ['vessels.on_bench'],
    'fault.inject': ['truth.faults'] + _PWR, 'sim.step': ['clock.time_s'],
    'settings.configure': ['device.settings', 'device.clock', 'bms.relays', 'screen.locked', 'users.state', 'sensor.o2_fraction', 'status.o2_initializing'],
    'clock.set': ['device.clock'], 'network.create_certificate': ['device.settings'], 'relay.test': ['bms.relays'], 'screen.lock': ['screen.locked', 'users.state'],
    'users.enable': ['users.state', 'device.settings'], 'message.acknowledge': ['messages.unacknowledged'],
    **{k: ['users.state'] for k in ('users.disable', 'user.login', 'user.logout', 'user.create', 'user.update', 'user.delete', 'user.reset_secret', 'user.change_secret')},
    **{k: ['tasks.due', 'messages.unacknowledged'] for k in ('recurring_task.define', 'recurring_task.edit', 'recurring_task.delete', 'recurring_task.confirm')},
    'operation_record.create': [], 'export.files': [],
}
_PHYS_OPS = _PHYS + ['htd.remaining_s'] + _OPS                               # a physical change also moves the HTD time estimate
ON_STEP = {'plant.configure': _PHYS + _HTD, 'power.set': _PHYS + _HTD, 'power.press_switch': _PHYS + _HTD, 'temperature.set': _PHYS_OPS, 'co2.set': _PHYS_OPS, 'o2.set': _PHYS_OPS,
           'co2.control': _PHYS_OPS, 'o2.control': _PHYS_OPS, 'gas.pressure_monitor': _PHYS_OPS, 'gas.reset': _PHYS_OPS, 'alarm.configure': _ALARM, 'alarm.mute': _ALARM,
           'htd.start': _PHYS + _HTD, 'door.outer': _DOOR + _PHYS + _HTD, 'door.latch': _DOOR + _PHYS + _HTD, 'door.inner': _DOOR + _PHYS + _HTD,
           'water_tray.fill': _PHYS_OPS, 'water_tray.empty': _PHYS_OPS, 'rh_sensor.cover': _HUMID + _ALARM, 'access_port.plug': _PHYS_OPS, 'vessel.place': _PHYS_OPS,
           'vessel.remove': _PHYS_OPS, 'fault.inject': _PHYS + _HTD, 'sim.step': _PHYS + _HTD + _DOOR}
ON_STEP.update({'settings.configure': _PHYS_OPS + ['device.clock', 'screen.locked', 'users.state'], 'clock.set': ['device.clock'], 'relay.test': ['bms.relays'],
                'screen.lock': ['screen.locked', 'users.state'], 'message.acknowledge': ['messages.unacknowledged'],
                **{k: ['users.state'] for k in ('users.enable', 'user.login', 'user.logout', 'users.disable')},
                **{k: ['tasks.due', 'messages.unacknowledged'] for k in ('recurring_task.define', 'recurring_task.edit', 'recurring_task.delete', 'recurring_task.confirm')}})
ON_STEP['sim.step'] = ON_STEP['sim.step'] + ['device.clock', 'screen.locked', 'users.state']
for _fx in (EFFECTS, ON_STEP):                                               # the information bar follows every alarm and task warning
    for _k, _v in _fx.items():
        if ('alarms.active' in _v or 'tasks.due' in _v) and 'messages.unacknowledged' not in _v:
            _v.append('messages.unacknowledged')
for _k, _v in EFFECTS.items():                                               # a changed message list keeps evolving with the next alarms
    if 'messages.unacknowledged' in _v and _k != 'sim.step' and 'messages.unacknowledged' not in ON_STEP.setdefault(_k, []):
        ON_STEP[_k] = ON_STEP[_k] + ['messages.unacknowledged']
ON_STEP_ZH = {
    'settings.configure': '自动屏幕锁定与自动注销按时间生效；O2传感器开关后N2控制停止或重新初始化', 'clock.set': '设备时钟继续走', 'relay.test': '15 s后继电器回到按事件的状态',
    'screen.lock': '自动屏幕锁定与自动注销按时间生效', 'message.acknowledge': '新的报警、错误或到期任务再次出现在信息栏',
    'users.enable': '无触摸达到自动注销时间后注销', 'user.login': '无触摸达到自动注销时间后注销', 'user.logout': '同上', 'users.disable': '同上',
    'recurring_task.define': '任务按间隔到期（提前提醒天数）', 'recurring_task.edit': '任务按新间隔到期', 'recurring_task.delete': '到期提醒随任务删除',
    'recurring_task.confirm': '从完成时刻重新计算到期',
    'plant.configure': '室温、湿度、气压与气源压力改变腔体热损失、交换空气成分与注气流量', 'power.set': '开机：加热控制与传感器初始化（CO2 30 min、O2 10 min），断电：无加热无注气，腔体冷却',
    'power.press_switch': '同power.set', 'temperature.set': '加热器按新设定值调节，温度、湿度与报警随之变化', 'co2.set': 'CO2按新设定值注气（降低设定值时只能开门放气）',
    'o2.set': 'O2按新设定值注N2（升高只能开门）', 'co2.control': '启停CO2注气', 'o2.control': '启停N2注气', 'gas.pressure_monitor': '低压时是否停控与报警',
    'gas.reset': '重启出错的气体控制', 'alarm.configure': '报警按新限值判定', 'alarm.mute': '新的报警会重新响',
    'htd.start': '升温到180 °C、保持2 h、冷却到培养温度；结束后传感器重新初始化', 'door.outer': '门按约1.5 s转动；开门时注气暂停、腔内与室内空气交换（内门也开时交换很快）',
    'door.latch': '门闩约0.5 s转到位；门闩未扣时内门漏气、玻璃更冷', 'door.inner': '内门约1.5 s转动；双门打开时腔内气体快速与室内交换',
    'water_tray.fill': '水盘蒸发使湿度回升，温水与冷水改变腔温与凝露', 'water_tray.empty': '湿度随泄漏逐渐下降', 'rh_sensor.cover': '盖着保护罩时湿度读数响应很慢',
    'access_port.plug': '拔掉堵头增加泄漏', 'vessel.place': '容器与腔体交换热量与水汽', 'vessel.remove': '容器回到室温与空气',
    'fault.inject': '断电、加热器、阀、门封、传感器故障改变腔体演化；染菌改变容器', 'sim.step': '1 s网格积分腔体物理，60 s网格更新培养'}
PRECONDITIONS = {
    'plant.configure': ('噪声种子只能在首次声明时设定', ['plant.configured']),
    'power.set': ('已声明实验室条件；开机时市电在100–127 V或220–240 V ±10 %且未断电', ['plant.configured', 'power.enabled']),
    'power.press_switch': ('同power.set；按一下切换开关位置', ['plant.configured', 'power.enabled']),
    'temperature.set': ('通电、不在高温消毒中；22–50 °C、0.1 K步进', ['power.enabled', 'htd.phase']),
    'co2.set': ('通电、不在高温消毒中、CO2传感器不在初始化；0.1–20 %、0.1 %步进', ['power.enabled', 'htd.phase', 'status.co2_initializing']),
    'o2.set': ('通电、不在高温消毒中、O2传感器不在初始化；1–20 %、0.1 %步进', ['power.enabled', 'htd.phase', 'status.o2_initializing']),
    'co2.control': ('通电、不在高温消毒中', ['power.enabled', 'htd.phase']), 'o2.control': ('通电、不在高温消毒中', ['power.enabled', 'htd.phase']),
    'gas.pressure_monitor': ('通电', ['power.enabled']), 'gas.reset': ('通电；该气体控制处于出错状态', ['power.enabled', 'status.co2_control_failed', 'status.o2_control_failed']),
    'alarm.configure': ('通电；门报警时间15/30/45/60/90 s；限值在允许范围', ['power.enabled']), 'alarm.mute': ('通电', ['power.enabled', 'alarm.sound']),
    'htd.start': ('通电、无进行中的操作、不在高温消毒中；腔内无容器、水盘无水（真机的水位传感器联锁）、湿度传感器盖上保护罩、接入孔插好堵头、外门与内门关好且门闩水平、'
                  '温度限制器未动作（容器、保护罩与堵头在真机上是触屏引导的检查清单，仿真中作为拒绝条件）',
                  ['power.enabled', 'operation.kind', 'htd.phase', 'vessels.in_chamber', 'sensor.water_present', 'rh_sensor.cover_on', 'door.outer_state', 'door.latched',
                   'status.heating_shutdown', 'access_ports.unplugged']),
    'contamination.set_status': ('通电', ['power.enabled']), 'task.configure': ('通电', ['power.enabled']),
    'task.complete': ('通电；只用于手动任务（腔体清洁、加水）', ['power.enabled']),
    'performance_check.record': ('通电、不在高温消毒中；给出该量的外部读数（温度reference_temperature_k，CO2/O2 reference_fraction）；该量的设定值为默认值'
                                 '（37 °C / 5 % CO2 / 10 % O2且O2控制启用）、有显示值、外门关着',
                                 ['power.enabled', 'setpoint.temperature_k', 'setpoint.co2_fraction', 'setpoint.o2_fraction', 'door.outer_state']),
    'data.export': ('通电', ['power.enabled']),
    'door.outer': ('已声明实验室条件；关外门前内门须已关闭', ['door.inner_state']),
    'door.latch': ('外门已打开（≥1 rad）；关门闩时内门须已关闭', ['door.outer_state', 'door.inner_state']),
    'door.inner': ('开内门需外门全开且门闩已打开', ['door.outer_state', 'door.latch_angle_rad']),
    'water_tray.fill': ('外门与内门都已打开；不在高温消毒或其中断后的高温冷却中；加水后总水量不超过2.5 L（按真实水量判断，agent只能看到水位传感器sensor.water_low / '
                        'sensor.water_present，溢出时TRAY_OVERFLOW）', ['door.outer_state', 'door.inner_state', 'htd.phase', 'sensor.water_low', 'sensor.water_present']),
    'water_tray.empty': ('外门与内门都已打开；不在高温消毒或其中断后的高温冷却中', ['door.outer_state', 'door.inner_state', 'htd.phase']),
    'rh_sensor.cover': ('外门与内门都已打开；不在高温消毒或其中断后的高温冷却中', ['door.outer_state', 'door.inner_state', 'htd.phase']),
    'access_port.plug': ('已声明实验室条件；不在高温消毒或其中断后的高温冷却中', ['access_ports.unplugged', 'htd.phase']),
    'vessel.provide': ('已声明实验室条件；ID唯一；培养基与细胞系在库中；盖子适用于容器；体积在推荐量0.25–3倍内；贴壁接种数不超过汇合数', ['vessels.on_bench']),
    'vessel.place': ('外门与内门都已打开；容器在实验台上；不在高温消毒（或其后冷却）中；该层面积与10 kg载重够用', ['door.outer_state', 'door.inner_state', 'vessels.on_bench', 'htd.phase']),
    'vessel.remove': ('外门与内门都已打开；容器在培养箱内', ['door.outer_state', 'door.inner_state', 'vessels.in_chamber']),
    'vessel.observe': ('容器存在；腔内容器需外门打开（透过玻璃门看）', ['door.outer_state']), 'vessel.discard': ('容器在实验台上', ['vessels.on_bench']),
}
PRECONDITIONS.update({
    'settings.configure': ('通电；键在设置表中、值符合其类型与选项（触摸屏上按用户权限限制，见users.state.rights）', ['power.enabled', 'device.settings']),
    'clock.set': ('通电；自动日期时间关闭', ['power.enabled', 'device.settings']), 'network.create_certificate': ('通电', ['power.enabled']),
    'relay.test': ('通电', ['power.enabled']), 'screen.lock': ('通电', ['power.enabled']),
    'users.enable': ('通电；用户管理未启用；PIN为4–8位数字或密码6–16个字符', ['power.enabled', 'users.state']),
    'users.disable': ('通电；已登录的管理员输入自己的PIN/密码', ['power.enabled', 'users.state']),
    'user.login': ('通电；用户管理已启用；用户ID存在且PIN/密码正确', ['power.enabled', 'users.state']), 'user.logout': ('通电', ['power.enabled', 'users.state']),
    'user.create': ('通电；已登录的管理员；用户名唯一；不超过999个账户', ['power.enabled', 'users.state']),
    'user.update': ('通电；本人只能改全名与电子邮件，用户ID与角色须管理员；新用户ID未被占用', ['power.enabled', 'users.state']),
    'user.delete': ('通电；已登录的管理员；不能删除自己', ['power.enabled', 'users.state']), 'user.reset_secret': ('通电；已登录的管理员', ['power.enabled', 'users.state']),
    'user.change_secret': ('通电；已登录；当前PIN/密码正确；新PIN/密码格式正确', ['power.enabled', 'users.state']),
    'message.acknowledge': ('通电；信息栏有未确认的消息（触摸屏上按权限：受限用户只能确认警告，用户可确认报警，错误须管理员）', ['power.enabled', 'messages.unacknowledged']),
    'recurring_task.define': ('通电；自定义任务不超过4个', ['power.enabled', 'tasks.due']), 'recurring_task.edit': ('通电；任务存在', ['power.enabled', 'tasks.due']),
    'recurring_task.delete': ('通电；只能删除自定义任务', ['power.enabled', 'tasks.due']),
    'recurring_task.confirm': ('通电；手动任务（腔体清洁、加水或自定义；高温消毒与性能检查由各自程序完成）', ['power.enabled', 'tasks.due']),
    'operation_record.create': ('通电；日期有效且结束不早于开始（超过1000条时覆盖最早的）', ['power.enabled']), 'export.files': ('通电（模拟U盘已插入）', ['power.enabled'])})
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_PW = ['PLANT_REQUIRED', 'POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'temperature.set': _PW + ['HTD_RUNNING', 'INVALID_INCREMENT'], 'co2.set': _PW + ['HTD_RUNNING', 'SENSOR_INITIALIZING', 'INVALID_INCREMENT'],
    'o2.set': _PW + ['HTD_RUNNING', 'SENSOR_INITIALIZING', 'INVALID_INCREMENT'], 'co2.control': _PW + ['HTD_RUNNING'], 'o2.control': _PW + ['HTD_RUNNING'],
    'gas.pressure_monitor': _PW, 'gas.reset': _PW + ['NO_CONTROL_ERROR'], 'alarm.configure': _PW + ['INVALID_DOOR_SIGNAL', 'INVALID_ALARM_LIMIT'], 'alarm.mute': _PW,
    'htd.start': _PW + ['HTD_RUNNING', 'BUSY', 'VESSELS_IN_CHAMBER', 'WATER_IN_TRAY', 'RH_SENSOR_UNCOVERED', 'ACCESS_PORT_OPEN', 'DOORS_NOT_CLOSED', 'HEATING_SHUTDOWN',
                  'RECORDS_FULL'],
    'contamination.set_status': _PW, 'task.configure': _PW, 'task.complete': _PW + ['TASK_REQUIRES_PROCEDURE'],
    'performance_check.record': _PW + ['HTD_RUNNING', 'MISSING_REFERENCE', 'NOT_AT_DEFAULT_SETPOINT', 'NOT_STABLE', 'RECORDS_FULL'], 'data.export': _PW,
    'door.outer': ['PLANT_REQUIRED', 'INNER_DOOR_OPEN'], 'door.latch': ['PLANT_REQUIRED', 'OUTER_DOOR_CLOSED', 'INNER_DOOR_OPEN'],
    'door.inner': ['PLANT_REQUIRED', 'OUTER_DOOR_CLOSED', 'LATCH_CLOSED'], 'water_tray.fill': ['PLANT_REQUIRED', 'DOORS_NOT_OPEN', 'HTD_RUNNING', 'TRAY_OVERFLOW'],
    'water_tray.empty': ['PLANT_REQUIRED', 'DOORS_NOT_OPEN', 'HTD_RUNNING'], 'rh_sensor.cover': ['PLANT_REQUIRED', 'DOORS_NOT_OPEN', 'HTD_RUNNING'],
    'access_port.plug': ['PLANT_REQUIRED', 'HTD_RUNNING'],
    'vessel.provide': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_MEDIUM', 'UNKNOWN_CELL_LINE', 'CELL_LINE_REQUIRED', 'CLOSURE_MISMATCH', 'VOLUME_OUT_OF_RANGE',
                       'SEEDING_ABOVE_CONFLUENCE'],
    'vessel.place': ['PLANT_REQUIRED', 'DOORS_NOT_OPEN', 'UNKNOWN_ITEM', 'VESSEL_NOT_ON_BENCH', 'HTD_RUNNING', 'SHELF_FULL', 'SHELF_OVERLOAD'],
    'vessel.remove': ['PLANT_REQUIRED', 'DOORS_NOT_OPEN', 'UNKNOWN_ITEM', 'VESSEL_NOT_IN_CHAMBER'], 'vessel.observe': ['UNKNOWN_ITEM', 'NOT_VISIBLE'],
    'vessel.discard': ['UNKNOWN_ITEM', 'VESSEL_NOT_ON_BENCH'], 'vessel.inspect': ['UNKNOWN_ITEM'], 'fault.inject': ['PLANT_REQUIRED', 'UNKNOWN_ITEM'],
    'events.read': [], 'chart.read': [], 'records.list': [], 'tasks.list': [], 'alarms.list': [], 'provenance.read': [], 'ledger.read': [], 'sim.step': [],
    'settings.configure': _PW + ['UNKNOWN_SETTING', 'INVALID_SETTING'], 'clock.set': _PW + ['AUTOMATIC_TIME', 'INVALID_DATE'], 'network.create_certificate': _PW,
    'relay.test': _PW, 'screen.lock': _PW, 'users.enable': _PW + ['USER_MANAGEMENT_ON', 'INVALID_SECRET'],
    'users.disable': _PW + ['NO_USER_MANAGEMENT', 'NOT_ADMIN', 'WRONG_SECRET'], 'user.login': _PW + ['NO_USER_MANAGEMENT', 'UNKNOWN_USER', 'WRONG_SECRET'],
    'user.logout': _PW, 'user.create': _PW + ['NO_USER_MANAGEMENT', 'NOT_ADMIN', 'USERS_FULL', 'USER_EXISTS'],
    'user.update': _PW + ['NO_USER_MANAGEMENT', 'UNKNOWN_USER', 'NOT_ADMIN', 'USER_EXISTS'], 'user.delete': _PW + ['NO_USER_MANAGEMENT', 'NOT_ADMIN', 'UNKNOWN_USER', 'SELF_DELETE'],
    'user.reset_secret': _PW + ['NO_USER_MANAGEMENT', 'NOT_ADMIN', 'UNKNOWN_USER'], 'user.change_secret': _PW + ['NOT_LOGGED_IN', 'WRONG_SECRET', 'INVALID_SECRET'],
    'message.acknowledge': _PW + ['NO_MESSAGE'], 'recurring_task.define': _PW + ['TASKS_FULL'], 'recurring_task.edit': _PW + ['UNKNOWN_TASK'],
    'recurring_task.delete': _PW + ['UNKNOWN_TASK', 'NOT_CUSTOM_TASK'], 'recurring_task.confirm': _PW + ['UNKNOWN_TASK', 'TASK_REQUIRES_PROCEDURE'],
    'operation_record.create': _PW + ['INVALID_DATE'], 'operation_records.list': [], 'export.files': _PW,
}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件', 'temperature.set': '设定值只在过程层（屏上温度数字经宿主镜像）', 'co2.set': '设定值只在过程层（屏上CO2数字经宿主镜像）',
    'o2.set': '只在过程层', 'co2.control': '只在过程层', 'o2.control': '只在过程层', 'gas.pressure_monitor': '只在过程层', 'gas.reset': '只在过程层',
    'alarm.configure': '只在过程层', 'alarm.mute': '只在过程层', 'htd.start': '腔体温度只在过程层（没有温度场可视化）', 'contamination.set_status': '信息栏为贴图',
    'task.configure': '只在过程层', 'task.complete': '只在过程层', 'performance_check.record': '外部仪器不在场景中', 'data.export': '数据操作',
    'rh_sensor.cover': '保护罩没有单独几何', 'access_port.plug': '堵头为固定几何（插拔状态只在过程层）', 'vessel.provide': '容器没有场景几何（搁板为接口面）',
    'vessel.place': '同左', 'vessel.remove': '同左', 'vessel.observe': '只读', 'vessel.discard': '同左', 'events.read': '只读', 'chart.read': '只读', 'records.list': '只读',
    'tasks.list': '只读', 'alarms.list': '只读', 'provenance.read': '只读', 'vessel.inspect': '只读真值（evaluator）', 'ledger.read': '只读账目（evaluator）',
    'fault.inject': '评估者故障注入', 'sim.step': '推进时钟'}
SCENE_MIRRORED = {'door.outer_angle_rad', 'door.inner_angle_rad', 'door.latch_angle_rad', 'water_tray.level_m', 'display.temperature_c', 'display.co2_percent', 'power.enabled'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、水面或屏上仿真数字（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {'htd.start': ('冷却到培养温度后结果为successful（completed）；开门（6731FW.753）、断电（6731FW.751）或180 °C未按时达到（6731FW.110）时failed',
                           '取消：停止加热、冷却到培养温度（post_htd_cooling），记录cancelled；消毒不算完成')}
RESOURCES = {'power.set': ['mains'], 'power.press_switch': ['mains'], 'htd.start': ['heaters', 'chamber', 'host_clock'], 'door.outer': ['outer_door', 'host_clock'],
             'door.latch': ['inner_door'], 'door.inner': ['inner_door', 'host_clock'], 'water_tray.fill': ['water_tray'], 'water_tray.empty': ['water_tray'],
             'rh_sensor.cover': ['rh_sensor'], 'access_port.plug': ['access_ports'], 'vessel.provide': ['bench'], 'vessel.place': ['shelves'], 'vessel.remove': ['shelves'],
             'vessel.observe': ['bench'], 'vessel.discard': ['bench'], 'data.export': ['usb'], 'plant.configure': ['room', 'mains', 'gas_supply'], 'fault.inject': ['evaluator'],
             'sim.step': ['host_clock']}
OBSERVES = {'power.set': ['power.enabled', 'display.temperature_c'], 'power.press_switch': ['power.enabled'], 'temperature.set': ['setpoint.temperature_k', 'sensor.temperature_k', 'display.temperature_c'],
            'co2.set': ['setpoint.co2_fraction', 'sensor.co2_fraction', 'display.co2_percent'], 'o2.set': ['setpoint.o2_fraction', 'sensor.o2_fraction'],
            'co2.control': ['control.co2_enabled'], 'o2.control': ['control.o2_enabled'], 'gas.reset': ['status.co2_control_failed', 'status.o2_control_failed'],
            'alarm.mute': ['alarm.sound'], 'htd.start': ['htd.phase', 'htd.remaining_s', 'operation.kind'], 'door.outer': ['door.outer_state', 'door.outer_angle_rad'],
            'door.latch': ['door.latch_angle_rad', 'door.latched'], 'door.inner': ['door.inner_state', 'door.inner_angle_rad'], 'water_tray.fill': ['sensor.water_low', 'sensor.water_present'],
            'water_tray.empty': ['sensor.water_low', 'sensor.water_present'], 'rh_sensor.cover': ['rh_sensor.cover_on'], 'access_port.plug': ['access_ports.unplugged'],
            'vessel.provide': ['vessels.on_bench'], 'vessel.place': ['vessels.in_chamber'], 'vessel.remove': ['vessels.on_bench'], 'contamination.set_status': ['contamination.status']}
_WB, _BR, _OM, _HM, _CE, _TF, _SA, _SIM = ('user_workbook', 'manufacturer_primary', 'manufacturer_operating_manual', 'himedia_technical_data_sheets', 'cellosaurus',
                                           'thermo_useful_numbers', 'sander_henry_compilation', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({k: [_OM] for k in ('power.set', 'power.press_switch', 'co2.control', 'o2.control', 'gas.pressure_monitor', 'gas.reset', 'alarm.configure', 'alarm.mute',
                                        'contamination.set_status', 'task.configure', 'task.complete', 'performance_check.record', 'data.export', 'door.outer', 'door.latch',
                                        'door.inner', 'water_tray.fill', 'water_tray.empty', 'rh_sensor.cover', 'access_port.plug', 'events.read', 'chart.read', 'records.list',
                                        'tasks.list', 'alarms.list')})
CAP_SOURCES.update({'temperature.set': [_WB, _BR, _OM], 'co2.set': [_WB, _BR, _OM], 'o2.set': [_BR, _OM], 'htd.start': [_WB, _BR, _OM],
                    'vessel.provide': [_TF, _HM, _CE, _SIM], 'vessel.place': [_OM, _TF], 'vessel.remove': [_OM], 'vessel.observe': [_SIM, _HM], 'vessel.inspect': [_SIM, _CE, _HM, _SA],
                    'provenance.read': [_HM, _CE, _TF, _SA]})
CAP_SOURCES.update({k: [_OM] for k in UNSUPPORTED})
_DEVICE_FUNCTIONS = ('settings.configure', 'clock.set', 'network.create_certificate', 'relay.test', 'screen.lock', 'users.enable', 'users.disable', 'user.login',
                     'user.logout', 'user.create', 'user.update', 'user.delete', 'user.reset_secret', 'user.change_secret', 'message.acknowledge',
                     'recurring_task.define', 'recurring_task.edit', 'recurring_task.delete', 'recurring_task.confirm', 'operation_record.create',
                     'operation_records.list', 'export.files')
CAP_SOURCES.update({k: [_OM, _SIM] for k in _DEVICE_FUNCTIONS})
MODEL_NONE_ZH.update({k: '只在过程层（触摸屏画面由panel.capture渲染）' for k in _DEVICE_FUNCTIONS})
RESOURCES.update({'export.files': ['usb'], 'relay.test': ['rear_connectors']})
_PANEL_FX = list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in EFFECTS.get(c, []))) + ['screen.locked', 'users.state']
_PANEL_STEP = list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in EFFECTS.get(c, []) + ON_STEP.get(c, [])))
for _k in _GESTURES:
    EFFECTS[_k] = list(dict.fromkeys(_PANEL_FX))                             # a gesture changes what the commands it calls change
    ON_STEP[_k] = list(dict.fromkeys(_PANEL_STEP + ['screen.locked', 'users.state']))
    ON_STEP_ZH[_k] = '手势调用的能力随step演化；自动屏幕锁定与自动注销从这次触摸重新计时'
    PRECONDITIONS[_k] = ('面板按当前画面与手册规则处理手势：关机时只有电源开关起作用；屏幕锁定时只有按住Unlock Screen起作用；用户权限不足的控件灰显不反应；'
                         '被调用能力的前提同样适用（被拒时显示仪器自己的提示，手势不被接受）', ['power.enabled', 'screen.locked', 'users.state'])
    CAP_ERRORS[_k] = ['PLANT_REQUIRED']
PRECONDITIONS['panel.capture'] = ('已声明实验室条件（关机时为暗屏）', ['power.enabled'])
PRECONDITIONS['panel.frame'] = ('无', ['plant.configured'])
CAP_ERRORS.update({'panel.capture': ['PLANT_REQUIRED'], 'panel.frame': []})
CAP_SOURCES.update({k: [_OM, _SIM] for k in PANEL_CAPS})
MODEL_NONE_ZH.update({k: '没有3D模型绑定：屏幕纹理与开关关节联动属于后续Matterix步骤' for k in PANEL_CAPS})
EXEC_TEXT.update({k: ('手势瞬时完成（不推进仿真时钟）：界面状态与被调用能力的效果立即生效；结果只说明是否被仪器接受，屏幕须用panel.capture查看',
                      '一个手势可依次调用多个能力，前面已提交的不回滚（与真实触摸屏相同）；被拒的能力不改变状态') for k in _GESTURES})
PARAM_ZH = {'display_id': '显示件（触摸屏）', 'x': '触点x（像素）', 'y': '触点y（像素）', 'duration_s': '按住时间', 'to_x': '拖动终点x', 'to_y': '拖动终点y',
            'input_id': '输入件（电源开关）', 'position': '开关位置', 'settings': '设置键值（见设置表）', 'date': '日期YYYY-MM-DD', 'time': '时间HH:MM', 'locked': '锁定或解锁', 'login_mode': '登录方式（PIN或密码）',
            'admin_name': '第一个管理员的用户名', 'secret': 'PIN或密码', 'user_id': '用户ID', 'name': '名称', 'full_name': '全名', 'email': '电子邮件', 'role': '角色',
            'new_user_id': '新的用户ID', 'current': '当前PIN/密码', 'new': '新PIN/密码', 'all': '确认全部', 'task': '周期任务ID', 'warn': '逾期提醒',
            'interval': '间隔', 'reminder_days': '提前提醒天数', 'start_date': '开始日期', 'finish_date': '结束日期', 'kinds': '导出的数据',
            'room_k': '实验室室温', 'room_rh': '室内相对湿度', 'ambient_pressure_pa': '大气压（绝压）', 'mains_voltage_v': '供电电压', 'co2_supply_pa': 'CO2进气压力（表压）',
            'n2_supply_pa': 'N2进气压力（表压）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.incubator_process.limitations。'
CAP_LIMITS = {
    'settings.configure': ['设备设置为键值模型：网络设置只保存并显示（仿真中没有网络、时间服务器与VisioNize连接）；声音只记录模式与音量（没有音频，“Alarms”模式下门报警不响）；'
                           '主页关掉O2时O2传感器关闭（N2控制停止），再打开时重新初始化10 min；自动注销与自动屏幕锁定以前面板最后一次触摸为起点。'],
    'clock.set': ['设备时钟=宿主时钟（2026-01-01 00:00 UTC起，假设）+手动偏移+时区（固定UTC偏移，不含夏令时）；自动日期时间取宿主时钟作为时间服务器。'],
    'users.enable': ['PIN为4–8位数字、密码6–16个字符（手册未给格式，假设）；权限表按手册p.104，在触摸屏层执行，直接API调用代表远程/宿主，不按登录用户限制（用户管理命令本身要求管理员）。'],
    'user.create': ['初始PIN/密码由设备按噪声种子确定性生成（4位PIN或8个字符）；用户数据导出为export.files的user_data。'],
    'message.acknowledge': ['信息栏消息为报警、错误与警告（_WARNING）与到期的周期任务；“当前消息”为最新的一条；确认后从信息栏删除，报警本身持续到恢复。'],
    'recurring_task.define': ['间隔按每天1 d、每周7 d、每月30 d、每年365 d；提前提醒天数为手册“reminder”的解释（假设）；自定义任务最多4个（与tasks.list的上限一致，假设）。'],
    'operation_record.create': ['运行记录的结果按期间（设备本地日期）的报警、错误与警告事件判定；初始参数为创建时的设定值与报警限；导出PDF为文件摘要（不生成真实PDF）。'],
    'export.files': ['导出到模拟U盘：只生成文件名、大小与行数（与data.export相同）；服务数据为事件与曲线的打包摘要。'],
    'relay.test': ['测试期间4个继电器都处于报警位15 s（手册未说是否只测所选继电器，假设全部）。'],
    **{k: ['面板层按panel_ui.json（手册页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟；手册未写之处的选择见panel_ui.json的assumptions。']
       for k in PANEL_CAPS},
    'temperature.set': ['控温为空气温度PI外环+壁温内环的工程控制器（厂家未公开算法）；热容、热导按高温消毒约14 h与恢复曲线标定，未用实测温度场；均匀性（±0.3 K）与27点分布未建模（单节点空气）。'],
    'co2.set': ['NDIR读CO2分压并按101.325 kPa换算为%（未做气压补偿，工程假设）；注气阀最大流量、关门泄漏率与开门交换率按产品册恢复曲线标定。'],
    'o2.set': ['ZrO2读O2分压；N2最大流量为工程值；O2上限受CO2与水汽稀释（高CO2时达不到高O2）。'],
    'htd.start': ['升温/保持/冷却按热模型计算，总时长约14 h（标定目标）；腔内微生物的灭活不单独建模：成功的高温消毒只记录结果与任务完成。',
                  '真机开门时会询问继续还是取消；仿真中消毒期间开外门总是立即中止（6731FW.753），不建模该询问。',
                  '真机只有水位传感器联锁（WATER_IN_TRAY）；容器取出（VESSELS_IN_CHAMBER）、湿度传感器保护罩（RH_SENSOR_UNCOVERED）与接入孔堵头（ACCESS_PORT_OPEN）'
                  '是触屏引导的检查清单，仿真中作为拒绝条件。'],
    'vessel.provide': ['容器面积、推荐培养基量与汇合细胞数取Thermo Fisher表（HeLa；其它贴壁细胞用同一汇合密度，工程假设）；培养基配方取HiMedia技术数据表（血清等添加物未计入）；'
                       '细胞倍增时间取Cellosaurus中位数（不同来源差别大，如HeLa为30–48 h等）；容器的传热、顶空交换时间、蒸发常数与密封培养瓶的内容积为工程值。',
                       '初始溶解CO2：as_supplied按HiMedia表QC pH中值（25 °C）的密封瓶状态，air为与室内空气平衡，incubator为与本培养箱当前CO2平衡；'
                       '密封培养瓶（plug_seal_closed）的顶空按盖上时的室内空气计，培养基与顶空在60 s步内平衡，细胞呼吸（RQ 0.9）的CO2进入顶空。',
                       '容器只放单层，不叠放（搁板面积按容器底面积累计）。'],
    'vessel.observe': ['只给目视可见的量（酚红颜色、浑浊、液量、盖上凝水）；细胞计数与形态需其它仪器。'],
    'vessel.inspect': ['pH按CO2/碳酸氢盐（表观pK\' 6.10于37 °C）、HEPES（25 °C滴定到pH 7.4的工程假设）、磷酸盐（pK2\' 6.8）与乳酸的电荷平衡计算；'
                       '生长为对数期×温度、pH、葡萄糖、渗透压、氧、乳酸与接触抑制因子（工程值），死亡率为工程值；谷氨酰胺、血清与氨未建模。'],
    'water_tray.fill': ['水盘蒸发传质系数为工程值（自然对流量级）；稳态湿度约92.5 %由最冷表面（门框/密封条一带）的凝水决定：该表面比空气低的温差按手册92.5 %标定，'
                        '凝水流回水盘；水盘尺寸为工程值（容量2.5 L为厂家值）。'],
}
LIMITATIONS = [
    '选定配置：CellXpert C170i，装O2（1–20 %）、湿度与水位三个选件，4层搁板，左铰链，不分段内门；铜内胆、分段门、门锁与VisioNize云不在配置内。',
    '腔体为集总模型（空气、壁、水盘水三个热节点，均匀气体）；温度与气体的空间分布、风道与27点均匀性未建模。',
    '控制器为工程实现（厂家算法未公开），参数按产品册恢复曲线与手册14 h高温消毒标定；报警暂停时长中只有湿度3.5/5 h与水位2/1 h为手册值，其余为工程值。',
    '培养容器与细胞没有场景几何；放取容器、开关门与加水是宿主/机器人抽象，没有接触验证；触摸屏为贴图，屏上温度与CO2数字为仿真UI提示。',
    '培养部分是工程模型：培养基pH按碳酸氢盐/CO2、HEPES（25 °C滴定到pH 7.4的假设）、磷酸盐与乳酸的电荷平衡计算；细胞按Cellosaurus倍增时间中位数（净群体倍增）'
    '乘以温度、pH、葡萄糖、渗透压、氧、乳酸与接触抑制因子生长，死亡率为工程值；所有贴壁细胞用HeLa的汇合密度；谷氨酰胺、血清、氨与细胞周期未建模。'
    '环境合格不保证培养成功。',
    '没有真实CellXpert的硬件接口。',
]


def _sampling_text(k):
    if k.startswith(('sensor.', 'truth.', 'display.', 'door.', 'water_tray.', 'htd.', 'alarm', 'bms.', 'status.')) or k in ('clock.time_s', 'operation.progress', 'tasks.due'):
        return 'Integrated on the fixed 1 s host grid (cultures every 60 s); between grid points the last grid value is held.'
    return 'Changes when a command is accepted; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _OWNED_VESSEL or CAPS[k]['visibility'] != 'agent') else []
    extra += ['REMOTE_DISABLED'] if k in REMOTE_CAPS else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    if k in _CONTROL_GUARDED:
        return '；另一个agent的高温消毒进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）'
    if k in _OWNED_VESSEL:
        return '；agent只能操作自己准备的容器（ACCESS_DENIED）'
    return ''


def export_eppendorf_cellxpertc170i_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'EppendorfCellxpertC170iSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.eppendorf_cellxpertc170i.EppendorfCellxpertC170iSession.invoke',
                                              invocation=f'session.invoke(instance_id="incubator_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(dict.fromkeys(list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []))),
                                                                              enforced_by='runtime', check='EppendorfCellxpertC170iRuntime.invoke / EppendorfCellxpertC170iSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（门与门闩的转动在之后的step中进行）',
                                completion_state_ids=['htd.phase', 'operation.kind'] if async_ else [], timeout_s=None,
                                cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'chamber_volume_m3': (V_CHAMBER, 'm3', '腔体总容积167 L（手册；产品册名义170 L）', [_OM]), 'temperature_setpoint_k': (list(T_SET_RANGE), 'K', '温度设定22–50 °C（至少高于室温4 K）', [_OM, _WB]),
            'co2_range': (list(CO2_RANGE), '1', 'CO2 0.1–20 %，0.1 %步进', [_OM, _WB, _BR]), 'o2_range': (list(O2_RANGE), '1', 'O2 1–20 %（选件）', [_OM, _BR]),
            'htd_temperature_k': (HTD_WALL_K, 'K', '高温消毒温度180 °C', [_OM, _WB]), 'htd_hold_s': (HTD_HOLD_S, 's', '高温消毒在180 °C保持2 h', [_OM]),
            'htd_duration_s': (14 * 3600.0, 's', '高温消毒总时长约14 h', [_OM]),
            'tray_capacity_m3': (TRAY_CAPACITY, 'm3', '水盘容量2.5 L', [_OM]), 'sensor_init_s': (dict(INIT_S), 's', '传感器初始化CO2 30 min、O2 10 min', [_OM]),
            'alarm_temperature_band_k': ([ALARM_DEFAULTS['temperature']['low_k'], ALARM_DEFAULTS['temperature']['high_k']], 'K', '出厂温度报警限：设定值−0.5/+0.5 K', [_OM]),
            'alarm_co2_band': ([ALARM_DEFAULTS['co2']['low'], ALARM_DEFAULTS['co2']['high']], '1', '出厂CO2报警限：设定值±0.5 %', [_OM]),
            'alarm_o2_band': ([ALARM_DEFAULTS['o2']['low'], ALARM_DEFAULTS['o2']['high']], '1', '出厂O2报警限：设定值±0.5 %', [_OM]),
            'alarm_rh_low': (ALARM_DEFAULTS['rh']['low'], '1', '出厂湿度报警下限80 %', [_OM]), 'alarm_door_signal_s': (ALARM_DEFAULTS['door']['signal_s'], 's', '出厂门报警时间30 s', [_OM]),
            'shelf_area_m2': (SHELF_AREA, 'm2', '搁板52.2×42.8 cm', [_OM]), 'shelf_max_kg': (SHELF_MAX_KG, 'kg', '每层搁板≤10 kg', [_OM]),
            'shelf_count': (SHELVES, '1', '标准配置4层搁板', [_OM]),
            'heater_rated_w': (1150.0, 'W', '额定功耗1150 W', [_OM])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    units = dict(c_wall_j_k='J/K', ua_wall_w_k='W/K', g_air_wall_w_k='W/K', g_door_w_k='W/K', heater_max_w='W', electronics_w='W', k_leak_per_s='1/s', k_port_per_s='1/s',
                 k_gasket_fault_per_s='1/s', k_unlatched_per_s='1/s', k_outer_only_per_s='1/s', k_door_open_per_s='1/s', q_co2_max_mol_s='mol/s', q_n2_max_mol_s='mol/s',
                 gas_restart_delay_s='s', tau_t_sensor_s='s', tau_ndir_s='s', tau_o2_sensor_s='s', tau_rh_sensor_s='s', tau_rh_covered_s='s', noise_t_k='K', noise_co2='1',
                 noise_o2='1', noise_rh='1', h_m_tray_m_s='m/s', g_tray_w_k='W/K', h_m_glass_m_s='m/s', dt_glass_latched_k='K', dt_glass_unlatched_k='K', kp_air='1',
                 ki_air='1/s', wall_band_lo_k='K', wall_band_hi_k='K', kp_wall='1/K', kp_co2='1', ki_co2='1/s', kp_o2='1', ki_o2='1/s', g_cold_spot_m3_s='m3/s',
                 dt_cold_spot_k='K', internal_heat_k='K', door_hold_s='s')
    fit = {}
    p = ASSET_DIR / LIBS['model']
    if p.exists():
        fit = json.loads(p.read_text()).get('fit_report', {})
    door_rms = fit.get('door', {})
    rms_text = (f'；产品册曲线拟合残差RMS {door_rms.get("rms_T_K", float("nan")):.2f} K / {door_rms.get("rms_CO2_percent", float("nan")):.2f} % CO2'
                if door_rms else '')
    fit_text = dict(ua_wall_w_k='拟合值：使手册的高温消毒总时长约14 h（calibrate_incubator.py；14 h为约数，UA随之约±10 %），不是厂家给出的参数',
                    dt_cold_spot_k=f'拟合值：使37 °C稳态相对湿度为92.5 %（手册§13.4.4的范围中值；calibrate_incubator.py，'
                                   f'稳态{fit.get("rh", {}).get("rh_steady", float("nan")):.4f}），不是厂家给出的参数')
    for name, val in MODEL.items():
        ev = MODEL_EVIDENCE.get(name, 'assumption')
        params[name] = dict(value=val, unit=units.get(name, '1'), meaning_zh=MODEL_PARAM_ZH.get(name, name), evidence='assumption',
                            source_ids=[_BR, _OM, _SIM] if ev == 'derived' else [_SIM],
                            uncertainty=(fit_text.get(name, '拟合值：按产品册p.6的30 s开门恢复曲线拟合（calibrate_incubator.py），不是厂家给出的参数' + rms_text)
                                         if ev == 'derived' else '工程值（厂家未公开）'))
    for name, val in GROWTH.items():
        params['growth_' + name] = dict(value=list(val) if isinstance(val, tuple) else val, unit=GROWTH_UNITS[name], meaning_zh=GROWTH_ZH[name], evidence='assumption',
                                        source_ids=[_SIM], uncertainty='细胞培养常识的典型值（工程值）')
    mechanism = dict(
        component_ids=['base_plinth', 'body_shell', 'chamber', 'outer_door', 'inner_door', 'inner_door_latch', 'shelf_1', 'shelf_2', 'shelf_3', 'shelf_4', 'water_tray', 'water_surface',
                       'chamber_sensors', 'rh_sensor', 'access_ports', 'touch_screen', 'display_temperature_digits', 'display_co2_digits', 'switch_mains', 'rear_connectors'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='CO2培养箱：空气/壁/水盘三节点热平衡与6面加热控制，CO2/O2/水汽摩尔平衡（注气、泄漏、开门交换、蒸发与结露），传感器滞后与噪声，报警与错误，高温消毒；培养容器的温度、溶解气、pH、蒸发与细胞生长',
        equations=['C_air dT_a/dt = G_aw (T_w - T_a) - kappa n c_p (T_a - T_room) - q_gas c_p (T_a - T_room) - G_door (T_a - T_room) - Q_vessels/2',
                   'C_wall dT_w/dt = u_h P_heater - G_aw (T_w - T_a) - UA (T_w - T_room) - G_tray (T_w - T_tray) - Q_vessels/2',
                   'dn_i/dt = kappa (n x_i,room - n_i) + q_i (+ E_tray - C_glass for water); vent / draw to n = P V / (R T_a)',
                   'kappa = k_leak + k_port N_unplugged + k_open min(phi_outer, phi_inner) + k_outer phi_outer (1 - phi_inner) (+ k_unlatched, gasket fault)',
                   'E_tray = h_m A (p_sat(T_tray) - x_w P) / (R T_tray); C_glass = h_m A_glass (x_w P - p_sat(T_glass)) / (R T_glass) when positive',
                   'NDIR reading = p_CO2 / 101.325 kPa (first-order lag); ZrO2 reading = p_O2 / 101.325 kPa',
                   'medium: alk = [HCO3-](1 + 2K2/h) + HEPES_T Kh/(Kh + h) + P_T K2p/(K2p + h) + Lac_T Kl/(Kl + h) + [OH-] - [H+]; [HCO3-] = K1\' [CO2aq] / h; '
                   'open vessels [CO2aq] -> H_CO2(T) p_CO2 with tau = tau_headspace + 4 d^2 / (pi^2 D); sealed flasks: n_C = V DIC + V_hs p_CO2 / (R T) at equilibrium',
                   'cells: dN/dt = ((ln2/Td + k_base) f_T f_pH f_glc f_osm f_O2 f_lac f_contact - k_death) N; glucose use q_glc N, lactate = 1.6 x glucose; O2 use q_O2 N, CO2 = 0.9 x O2',
                   'humidity: cold-spot condensate D = g_cs (x_w P - p_sat(T_a - dT_cs)) / (R T) when positive, back into the tray; excess above saturation condenses on the walls',
                   'control: air PI sets the wall target within the band; frozen while the outer door is open and for door_hold_s after closing; wall P loop to the heater'],
        parameters=params, source_ids=[_SIM, _WB, _BR, _OM, _HM, _CE, _TF, _SA], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_cellxpert_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('eppendorf_cellxpertc170i', caps, slots, {'incubator_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'incubator_process': mechanism})
