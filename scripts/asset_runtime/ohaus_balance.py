"""OHAUS Pioneer PX224/E and Scout SPX2201 balances: firmware over the legacy weighing abstraction (one module, two model profiles).

Process: scripts/balance_runtime.py (unchanged; the XPR204E asset pins its hash) - the pan force, stability gating by doors / tilt / vibration
and settling time, pan and transport-lock interlocks, the one-point force calibration.  FirmwareBalance below subclasses its Balance only to make
the stability band follow the Stable Range setting; everything the user menus do is firmware here:
  * weighing (zero, tare, units incl. a custom unit, graduations, auto zero tracking, auto tare, filter = settling time, stable range), power-on
    zero range, overload / underload, capacity bar;
  * calibration as the manuals describe it: span (zero then one of two span points) and linearity (zero, mid, span) with the balance capturing
    each stable point itself; the stored correction (gain and a piecewise linear term) is applied to every reading; sensor errors (offset,
    gain, non-linearity) are evaluator settings and 0 by default;
  * applications - PX: weighing (+ batch), parts counting (APW, optimisation), percent, dynamic (averaging), density (solid / liquid / porous,
    water and ethanol tables), formulation; SPX: weigh, count, percent, check (under / accept / over), totalize, display / peak hold;
  * settings menus with the manuals' items, lockout, password (PX), approved mode with the security switch (PX), auto off, backlight /
    brightness / auto dim, date and time (PX), RS232 and print settings with a printout composer (the interface output log);
  * the SPX RS232 kit is optional (PRINT and RS232 menus appear only when it is installed: plant.configure rs232_kit);
  * PX system errors (Error 2.0 / 9.5 / 5.3, IDNR error, Service; p.46) can be injected by the evaluator (fault.inject / fault.clear).
Manuals: references/manuals/ohaus_px224e/operating_manual__pioneer_px_series_instruction_manual.pdf (PDF pages; EN-n = PDF n+4) and
references/manuals/ohaus_spx2201/operating_manual__scout_spx_instruction_manual_rev_f_2023.pdf (EN-n = PDF n+2), RS232 kit manual 30269073A."""
import copy
import datetime
import hashlib
import json
import math
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))
import balance_runtime as legacy  # noqa: E402

PROTOCOL_VERSION = '1.0.0'
ASSET_VERSIONS = {'PX224E': '1.2.0', 'SPX2201': '1.1.1'}                # sync audit 2026-10-19: PX fault.inject (minor), SPX session describe() (fix)
G0 = 9.80665
EPOCH = datetime.datetime(2026, 1, 5, 8, 0, 0)                           # assumption: balance clock at simulation time 0
GRAMS = {'g': 1.0, 'kg': 1000.0, 'mg': 0.001, 'ct': 0.2, 'N': 1000.0 / G0, 'lb': 453.59237, 'oz': 28.349523125, 'ozt': 31.1034768, 'GN': 0.06479891,
         'grn': 0.06479891, 'dwt': 1.55517384, 'mo': 3.75, 'msg': 4.6083, 'tl H': 37.79936, 'hkt': 37.79936, 'tl S': 37.7994, 'sgt': 37.7994, 'tl T': 37.5,
         'twt': 37.5, 'tcl': 16.32932, 'tical': 16.32932, 'tola': 11.6638038, 'baht': 15.244, 'lb:oz': 453.59237}
PROFILES = {
    'PX224E': dict(
        asset_type_id='instrument.ohaus_px224e', asset_dir='assets/PX224E', legacy='PX224E', capacity_g=220.0, d_g=0.0001, settling_s=3.0,
        span_points_g=[100.0, 200.0], lin_points_g=[100.0, 200.0], display_digits=8, analytical=True, interface_builtin=True,
        units=['g', 'kg', 'mg', 'ct', 'N', 'lb', 'oz', 'ozt', 'GN', 'dwt', 'mo', 'msg', 'tl H', 'tl S', 'tl T', 'tcl', 'tola', 'baht', 'C1'],
        apps=['weighing', 'counting', 'percent', 'dynamic', 'density', 'formulation'],
        manual='references/manuals/ohaus_px224e/operating_manual__pioneer_px_series_instruction_manual.pdf'),
    'SPX2201': dict(
        asset_type_id='instrument.ohaus_spx2201', asset_dir='assets/SPX2201', legacy='SPX2201', capacity_g=2200.0, d_g=0.1, settling_s=1.0,
        span_points_g=[2000.0, 1000.0], lin_points_g=[1000.0, 2000.0], display_digits=6, analytical=False, interface_builtin=False,
        units=['g', 'kg', 'ct', 'N', 'oz', 'ozt', 'dwt', 'lb', 'lb:oz', 'grn', 'hkt', 'sgt', 'twt', 't', 'C'],
        apps=['weigh', 'count', 'percent', 'check', 'total', 'hold'],
        manual='references/manuals/ohaus_spx2201/operating_manual__scout_spx_instruction_manual_rev_f_2023.pdf'),
}
FILTER_FACTOR = {'low': 0.5, 'medium': 1.0, 'high': 2.0}                # assumption: settling time x factor (manual: faster / normal / slower)
INIT_ZERO_RANGE = 0.10                                                   # assumption: power-on zero range +-10 % of capacity
ZERO_RANGE = 0.02                                                        # legacy runtime assumption: Zero key range +-2 % of capacity
CAL_TOLERANCE = 0.10                                                     # assumption: a calibration point more than 10 % off the nominal mass fails
CAL_TIMEOUT_S = 60.0                                                     # assumption: no stable reading for 60 s -> Timeout / CAL E
OUT_MAX = 200                                                            # interface output lines kept
FEET = ['foot_front_left', 'foot_front_right', 'foot_rear_left', 'foot_rear_right']
FOOT_SPAN_M = (0.17, 0.25)                                               # assumption: foot spacing left-right, front-rear
LEVEL_OK_RAD = math.radians(0.2)                                         # legacy runtime: tilt within 0.2 deg (bubble centred)
FAULT_CODES = ['2.0', '9.5', '5.3', 'IDNR', 'Service']                   # PX p.46: system errors
RESTART_CLEARS = ('2.0', '9.5', '5.3')                                   # p.46 remedy: disconnect the power and restart (if it persists: service)


class ActionRejected(ValueError):
    """The firmware refused a command; the session restores the state from before the call."""


def nice_step(x):
    """Smallest 1-2-5 x 10^k step >= x (display resolution of a unit; matches the SPX capacity x readability table)."""
    e = math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        if m * 10 ** e >= x * (1 - 1e-9):
            return round(m * 10 ** e, 12)
    return 10 ** (e + 1)


def water_density(t_c):
    """Air-free water density (g/cm3), Tanaka et al. 2001 (the balance has a 10-30.9 C table)."""
    a1, a2, a3, a4, a5 = -3.983035, 301.797, 522528.9, 69.34881, 0.999974950
    return a5 * (1 - (t_c + a1) ** 2 * (t_c + a2) / (a3 * (t_c + a4)))


def ethanol_density(t_c):
    """Ethanol density (g/cm3), linear fit 0.80625 - 0.000847 t (OHAUS density tables use the same linear dependence)."""
    return 0.80625 - 0.000847 * t_c


def round_to(v, step):
    return round(math.floor(v / step + 0.5 + 1e-9) * step, 10) if v >= 0 else -round(math.floor(-v / step + 0.5 + 1e-9) * step, 10)


# ---------------------------------------------------------------- schema helpers
def number(unit='1', lo=None, hi=None):
    s = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


def obj(properties, optional=()):
    return dict(type='object', properties=properties, required=[k for k in properties if k not in optional], additionalProperties=False)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


BOOL = {'type': 'boolean'}
FREE = {'type': 'object'}


def text(n):
    return {'type': 'string', 'maxLength': n}


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
            if not set(s.get('required', [])) <= set(v) or not set(v) <= set(s['properties']):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                validate(s['properties'][key], value)
    elif k == 'boolean' and type(v) is not bool:
        raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'string':
        if not isinstance(v, str) or len(v) > s.get('maxLength', 10 ** 6):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'pattern' in s and not re.fullmatch(s['pattern'], v):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'enum' in s and v not in s['enum']:
            raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- settings (defaults: the manuals print them in bold, which text extraction lost)
def default_settings(model):
    p = PROFILES[model]
    if model == 'PX224E':
        return dict(
            password='', language='English', filter='medium', azt='0.5', auto_tare=False, graduations=1, date_format='MM/DD/YYYY', time_format='24HR',
            brightness='high', auto_dim='off', auto_off='off', capacity_bar=False, approved=False,
            units={u: True for u in p['units']}, custom_factor=1.0, custom_exponent=0, custom_lsd=1.0,
            baud=9600, transmission='8-NO-1', handshake='none', stable_only=False, numeric_only=False, single_header=False, print_to='printer',   # p.36 bold
            output_format='OHAUS-PX', batch_printing=False, label_printing=False, auto_print='off', auto_print_condition='load', print_interval_s=0,
            header=True, date_time=True, balance_id=True, balance_name=True, user_name=True, project_name=True, application_name=True, result=True,
            gross=True, net=True, tare=True, signature=True, line_feed=1,
            glp_header=['Header 1', 'Header 2', 'Header 3', 'Header 4', 'Header 5'], balance_name_text='PX224', user_name_text='', project_name_text='',
            lockout=dict(calibration=False, setup=False, units=False, rs232=False, print=False, glp=False))
    return dict(
        filter='medium', azt='0.5', stable_range='1', backlight='auto', auto_tare='off', auto_off='off',
        modes=dict(weigh=True, count=True, percent=True, check=True, total=True), hold='disp',
        units={u: (u in ('g', 'kg')) for u in p['units'] if u not in ('t',)}, t_unit='off', custom_factor=1.0, custom_exponent=0, custom_lsd=1.0,
        stable_only=False, a_print='off', print_interval_s=10, content=dict(result=True, gross=False, net=False, tare=False, header=False, footer=False, mode=False,
                                                                          unit=False, info=False),
        layout_format='single', feed='line', baud=9600, parity='8 none', stop_bit=1, handshake='none', alt_print='P', alt_tare='T', alt_zero='Z')


SETTING_ENUMS = {
    'PX224E': dict(language=['English', 'Deutsch', 'Francais', 'Italiano', 'Polski', 'Espanol', 'Turkce', 'Portugues', 'Korean', 'Chinese', 'Japanese'],
                   filter=['low', 'medium', 'high'], azt=['off', '0.5', '1', '3'], date_format=['YYYY/MM/DD', 'MM/DD/YYYY', 'DD/MM/YYYY'],
                   time_format=['24HR', '12HR'], brightness=['low', 'medium', 'high'], auto_dim=['off', '10', '20', '30'], auto_off=['off', '30', '60', '120'],
                   baud=[1200, 2400, 4800, 9600, 19200, 38400], transmission=['8-NO-1', '8-NO-2', '7-EVEN-1', '7-EVEN-2', '7-NO-1', '7-NO-2', '7-ODD-1', '7-ODD-2'],
                   handshake=['none', 'xon-xoff', 'hardware'], print_to=['pc', 'printer'], output_format=['OHAUS-PX', 'SICS', 'ST', 'OHAUS-AR', 'OHAUS-PA'],
                   auto_print=['off', 'on_stability', 'interval', 'continuous'], auto_print_condition=['load', 'load_zero'], line_feed=[1, 4], graduations=[1, 10]),
    'SPX2201': dict(filter=['low', 'medium', 'high'], azt=['off', '0.5', '1', '3'], stable_range=['0.5', '1', '2', '5'], backlight=['off', 'on', 'auto'],
                    auto_tare=['off', 'on', 'on_acc'], auto_off=['off', '1', '5', '10'], hold=['disp', 'peak', 'off'], t_unit=['off', 'tola', 'tical'],
                    a_print=['off', 'on_stable_load', 'on_stable_load_zero', 'interval', 'accept', 'continuous'], layout_format=['single', 'multi'],
                    feed=['line', '4lines', 'form'], baud=[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200], parity=['7 even', '7 odd', '7 none', '8 none'],
                    stop_bit=[1, 2], handshake=['none', 'xon-xoff']),
}


def settings_schema(model):
    """settings.configure: every user-menu setting of the model as an optional argument (each menu line writes one)."""
    d = default_settings(model)
    en = SETTING_ENUMS[model]
    props = {}
    for k, v in d.items():
        if k in en:
            props[k] = {'type': 'integer', 'x-unit': '1', 'enum': list(en[k])} if isinstance(en[k][0], int) else enum(en[k])
        elif isinstance(v, bool):
            props[k] = BOOL
        elif k == 'custom_factor':
            props[k] = number('1', 0.1, 1.999999)
        elif k == 'custom_exponent':
            props[k] = integer(-3, 3)
        elif k == 'custom_lsd':
            props[k] = {'type': 'number', 'x-unit': '1', 'enum': [0.5, 1, 2, 5, 10, 100]}
        elif k == 'print_interval_s':
            props[k] = integer(0, 3600)
        elif k == 'password':
            props[k] = {'type': 'string', 'pattern': '^[123]{0,6}$', 'maxLength': 6}
        elif k == 'glp_header':
            props[k] = {'type': 'object'}                               # {index: text}; validated by the runtime
        elif k in ('balance_name_text', 'user_name_text', 'project_name_text'):
            props[k] = text(16)
        elif k in ('alt_print', 'alt_tare', 'alt_zero'):
            props[k] = {'type': 'string', 'pattern': '^[A-Z]$', 'maxLength': 1}
        elif isinstance(v, dict):
            props[k] = dict(type='object', properties={kk: BOOL for kk in v}, additionalProperties=False)
        else:
            raise AssertionError(k)
    if 'password' in props:
        props['password_digits'] = integer(0, 333333)                    # the keypad path: digits 1 / 2 / 3 as a number (132 = '132')
    return obj(props, optional=tuple(props))


# ---------------------------------------------------------------- capabilities
def build_caps(model):
    p = PROFILES[model]
    px = model == 'PX224E'
    caps = {}

    def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
        caps[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)

    ST = obj(dict(state=FREE))
    cap_kg = p['capacity_g'] / 1000.0
    cap('plant.configure', obj(dict(g_local=number('m/s2', 9.76, 9.84), rs232_kit=BOOL), optional=('g_local', 'rs232_kit')), ST, dict(g_local=G0),
        ['housing'], '评测者：当地重力加速度；SPX是否装了RS232接口套件（装了才有PRINT与RS232菜单）' if not px else '评测者：当地重力加速度', 'evaluator', 'configuration')
    cap('power.connect_adapter', obj(dict(connected=BOOL)), ST, dict(connected=True), ['rear_ports'], '插拔电源适配器（拔掉即断电）', 'agent', 'robot_skill')
    cap('power.set', obj(dict(on=BOOL)), ST, dict(on=True), ['button_zero'], 'On/Zero短按开机；长按Off关机（开机时检查开机零点范围）')
    cap('pan.set_load', obj(dict(mass_kg=number('kg', -1.0, 3 * cap_kg))), ST, dict(mass_kg=0.05), ['pan'],
        '在秤盘上放或取物体后的秤盘净载荷（质量，含容器；宿主由接触力汇总）', 'agent', 'robot_skill')
    cap('pan.remove', obj(dict(unlock_angle_rad=number('rad', -0.4, 0.0)), optional=('unlock_angle_rad',)), ST, dict(unlock_angle_rad=-0.35), ['pan'],
        '取下秤盘（须关机且空载；SPX先顺时针转20°解开卡口）', 'agent', 'robot_skill')
    cap('pan.install', obj({}), ST, {}, ['pan'], '装上秤盘', 'agent', 'robot_skill')
    if not px:
        cap('transport_lock.set', obj(dict(angle_rad=number('rad', 0.0, math.pi / 2))), ST, dict(angle_rad=math.pi / 2), ['transport_lock'],
            '转动秤盘下的运输锁（逆时针90°为解锁；须先取下秤盘）', 'agent', 'robot_skill')
    else:
        cap('doors.set', obj(dict(open_fraction=number('1', 0.0, 1.0))), ST, dict(open_fraction=0.0), ['door_left', 'door_right', 'door_top'],
            '开关防风罩门（任一门开时读数不稳定）', 'agent', 'robot_skill')
    cap('security.set_switch', obj(dict(locked=BOOL)), ST, dict(locked=False), ['security_switch'], '后部安全开关（锁定：' +
        ('锁定菜单只读，批准模式需要' if px else '隐藏校准、锁定单位') + '）', 'agent', 'robot_skill')
    cap('environment.set', obj(dict(tilt_rad=number('rad', -0.1, 0.1), vibration=BOOL), optional=('tilt_rad', 'vibration')), ST, dict(tilt_rad=0.0),
        ['housing'], '评测者：台面倾斜（调平脚之前）与振动（读数不稳定）', 'evaluator', 'simulation')
    cap('level.turn_foot', obj(dict(foot=enum(FEET), angle_rad=number('rad', -4 * math.pi, 4 * math.pi))), ST, dict(foot='foot_front_left', angle_rad=math.pi / 2),
        FEET, '转动调平脚（1 mm/圈，旧模型螺距）使水平泡居中', 'agent', 'robot_skill')
    cap('weighing.zero', obj({}), ST, {}, ['button_zero'], 'Zero：稳定时把当前读数置零（范围为容量的±2 %）')
    cap('weighing.tare', obj({}), ST, {}, ['button_tare'], 'Tare：稳定时去皮；空盘时清除皮重')
    cap('weighing.set_unit', obj(dict(unit=enum(p['units'] + (['tola', 'tical'] if not px else [])))), ST, dict(unit='g'), ['button_print'],
        '选择称量单位（只有在单位菜单中打开的单位）')
    cap('print.send', obj({}), ST, {}, ['button_print'], 'Print：把当前读数按打印设定发送到接口（未连接则没有输出）')
    if not px:
        cap('weighing.next_unit', obj({}), ST, {}, ['button_print'], '长按Units：换到下一个已打开的单位')
        cap('app.next', obj({}), ST, {}, ['button_function'], '长按Mode：换到下一个已打开的应用模式')
        cap('power.set_batteries', obj(dict(installed=BOOL)), ST, dict(installed=True), ['battery_cover'], '装入或取出4节AA电池（无适配器时由电池供电）', 'agent',
            'robot_skill')
    cap('interface.connect', obj(dict(device=enum(['none', 'pc', 'printer']))), ST, dict(device='pc'), ['rear_ports'],
        '连接电脑或打印机的串口线（SPX须装RS232套件）', 'agent', 'robot_skill')
    cap('settings.configure', settings_schema(model), ST, dict(filter='high'), ['button_tare'],
        '用户菜单的设定（每个菜单项写一个参数）；受锁定菜单、密码、批准模式与安全开关限制', 'agent', 'configuration')
    cap('settings.reset', obj(dict(menu=enum(['all'] if px else ['setup', 'mode', 'unit', 'print', 'rs232']))), ST, dict(menu='all' if px else 'setup'),
        ['button_tare'], '恢复出厂设定' + ('（Factory Reset > Reset All）' if px else '（各子菜单的Reset）'), 'agent', 'configuration')
    if px:
        cap('settings.enter_password', obj(dict(password=text(6), password_digits=integer(0, 333333)), optional=('password', 'password_digits')),
            obj(dict(state=FREE, accepted=BOOL)), dict(password='132'), ['button_tare'],
            '进入菜单时输入密码（1=On/Zero、2=Function、3=Print）；正确后本次菜单解锁', 'agent', 'configuration')
        cap('clock.set', obj(dict(year=integer(2000, 2099), month=integer(1, 12), day=integer(1, 31), hour=integer(0, 23), minute=integer(0, 59), second=integer(0, 59)),
                             optional=('year', 'month', 'day', 'hour', 'minute', 'second')),
            ST, dict(year=2026, month=1, day=5), ['button_tare'], 'Setup：设定日期与时间（按当前格式逐项输入）', 'agent', 'configuration')
        cap('glp.set_header', obj(dict(index=integer(1, 5), text=text(25))), ST, dict(index=1, text='LAB 2'), ['button_tare'], 'GLP：页眉1–5（25字符）',
            'agent', 'configuration')
    cap('calibration.start', obj(dict(kind=enum(['span', 'linearity']))), ST, dict(kind='span'), ['button_tare'],
        '开始跨度校准（零点+跨度点）或线性校准（零点、中点、满点）；之后天平在读数稳定时自己采点（须安全开关解锁' + ('、未锁定且有密码时已输入' if px else '') + '）',
        'agent', 'configuration')
    cap('calibration.select_point', obj({}), ST, {}, ['button_function' if px else 'button_print'], '跨度校准：切换两个跨度点（' +
        ('Function键' if px else 'No键') + '）', 'agent', 'configuration')
    cap('calibration.abort', obj({}), ST, {}, ['button_tare'], 'Exit：中止校准（原校准保持）', 'agent', 'configuration')
    cap('sensor.configure_error', obj(dict(offset_kg=number('kg', -0.01 * cap_kg, 0.01 * cap_kg), gain=number('1', -0.01, 0.01), nonlinearity=number('1', -0.001, 0.001)),
                                      optional=('offset_kg', 'gain', 'nonlinearity')),
        ST, dict(gain=0.0005), ['pan'], '评测者：称重传感器的零点、增益与非线性误差（非线性为半量程处相对误差，抛物线；默认0即理想；校准后消除）', 'evaluator', 'simulation')
    cap('app.select', obj(dict(app=enum(p['apps'])), ), ST, dict(app=p['apps'][1]), ['button_function'], '长按Function/Mode：选择应用（' +
        '、'.join(p['apps']) + '）')
    cap('app.function', obj({}), ST, {}, ['button_function'], 'Function短按：当前应用的动作（存APW/参考/成分/批量重量、切换显示、保持开始/复位、密度采点）')
    cap('counting.clear_apw', obj({}), ST, {}, ['button_function'], '计数：清除平均单件重量（Clear APW? Yes）')
    cap('counting.set_sample_size', obj(dict(size=integer(1, 1000) if px else {'type': 'integer', 'x-unit': '1', 'enum': [5, 10, 20, 50, 100]})), ST, dict(size=10),
        ['button_function'], '计数：取样数量')
    cap('counting.establish', obj({}), ST, {}, ['button_function'], '计数：用盘上取样数量的重量建立APW（稳定时）')
    cap('percent.clear_reference', obj({}), ST, {}, ['button_function'], '百分比：清除参考重量')
    cap('percent.establish', obj({}), ST, {}, ['button_function'], '百分比：把盘上重量存为100 %参考（稳定时）')
    if px:
        cap('batch.store', obj({}), ST, {}, ['button_function'], '批量称量（Batch Printing开）：存当前重量（最多10个）')
        cap('batch.finish', obj({}), ST, {}, ['button_print'], '批量称量：Print结束并打印')
        cap('dynamic.set_time', obj(dict(time_s=integer(1, 15))), ST, dict(time_s=10), ['button_function'], '动态称量：平均时间1–15 s')
        cap('density.configure', obj(dict(sample_type=enum(['solid', 'liquid']), liquid=enum(['water', 'alcohol', 'other']), porous=BOOL,
                                          water_temp_c=number('degC', 10.0, 30.9), alcohol_temp_c=number('degC', 10.0, 30.9), liquid_density=number('g/cm3', 0.5, 2.0),
                                          sinker_volume_ml=number('mL', 0.1, 100.0), porous_weight_g=number('g', 0.0, 220.0), oil_density=number('g/cm3', 0.5, 1.5),
                                          result_digits=integer(1, 4)),
                                     optional=('sample_type', 'liquid', 'porous', 'water_temp_c', 'alcohol_temp_c', 'liquid_density', 'sinker_volume_ml', 'porous_weight_g',
                                               'oil_density', 'result_digits')),
            ST, dict(sample_type='solid', liquid='water'), ['button_function'], '密度测定参数（样品类型、辅助液体、多孔材料、水/乙醇温度、液体密度、沉锤体积、干重、油密度、结果位数）')
        cap('formulation.finish', obj({}), ST, {}, ['button_print'], '配方：不再加成分（No），进入加填料或显示总量')
        cap('formulation.skip_filler', obj({}), ST, {}, ['button_print'], '配方：不加填料（No），显示总量')
    else:
        cap('check.set_limits', obj(dict(low_kg=number('kg', -cap_kg, cap_kg), high_kg=number('kg', -cap_kg, cap_kg)), optional=('low_kg', 'high_kg')), ST,
            dict(low_kg=0.1, high_kg=0.12), ['button_function'], '检重：下限与上限（kg；正检、负检或零检）')
        cap('check.clear', obj({}), ST, {}, ['button_function'], '检重：清除限值（Clr.ref）')
        cap('totalize.clear', obj({}), ST, {}, ['button_function'], '累计：清除累计（Clr.tot）')
    if px:
        cap('fault.inject', obj(dict(code=enum(FAULT_CODES), persistent=BOOL), optional=('persistent',)), ST, dict(code='2.0'), ['display'],
            '评测者：注入手册p.46的系统错误（Error 2.0/9.5/5.3、IDNR error、Service）：显示对应画面，按键无反应、称量与自动功能停止；2.0/9.5/5.3断电重启即清除'
            '（persistent=True时重启后仍在，须维修），IDNR与Service须维修（fault.clear）', 'evaluator', 'simulation')
        cap('fault.clear', obj({}), ST, {}, ['display'], '评测者：排除注入的系统错误（代表OHAUS维修）', 'evaluator', 'simulation')
    cap('events.read', obj({}), obj(dict(output=FREE)), {}, ['rear_ports'], '接口输出记录（打印行）', 'agent', 'query')
    cap('truth.inspect', obj({}), obj(dict(process=FREE)), {}, ['housing'], '评测者：秤盘真实载荷、原始读数、传感器误差、校准修正', 'evaluator', 'query')
    cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['housing'], '推进宿主时钟', 'evaluator', 'simulation')
    return caps


def _panel_engine():
    try:
        from . import _panel_ui as engine
        return engine
    except ImportError:
        import importlib.util
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui = _panel_engine()
CAPS = {m: build_caps(m) for m in PROFILES}
PANEL_SPEC, PANEL_CAPS, ALL_CAPS = {}, {}, {}
for _m, _p in PROFILES.items():
    _f = ROOT / _p['asset_dir'] / 'panel_ui.json'
    if _f.is_file():
        PANEL_SPEC[_m] = _panel_ui.load_spec(_f, capabilities=set(CAPS[_m]))
        PANEL_CAPS[_m] = _panel_ui.capability_definitions(PANEL_SPEC[_m])
    else:                                                                 # spec not built yet (build_panel_spec.py imports this module)
        PANEL_SPEC[_m], PANEL_CAPS[_m] = None, {}
    ALL_CAPS[_m] = dict(CAPS[_m], **PANEL_CAPS[_m])


# ---------------------------------------------------------------- process: the legacy balance with the Stable Range band
class FirmwareBalance(legacy.Balance):
    """Legacy Balance; update() is the legacy update with the change band set by the firmware (Stable Range: 0.5-5 d; legacy 0.5 d)."""
    band_d = 0.5

    def update(self, pan_load_force_N, dt_s, *, door_fraction=0.0, tilt_rad=0.0, vibration=False):
        if not math.isfinite(pan_load_force_N) or not math.isfinite(dt_s) or dt_s < 0:
            raise ValueError('Finite force and nonnegative finite dt required')
        m = pan_load_force_N / self.g_cal
        changed = abs(m - self.gross_kg) > self.readability_kg * self.band_d
        self.gross_kg = m
        self.door_fraction = door_fraction
        self.tilt_rad = tilt_rad
        environmental_gate = (not self.analytical or door_fraction <= .01) and abs(tilt_rad) <= math.radians(.2) and not vibration
        hardware_gate = self.powered and self.pan_installed and self.pan_latched and self.transport_unlocked
        valid = environmental_gate and hardware_gate
        if changed or not valid or not self.last_environment_valid:
            self.elapsed_s = 0
        else:
            self.elapsed_s += dt_s
        self.last_environment_valid = valid
        self.stable = bool(hardware_gate and environmental_gate and self.elapsed_s >= self.settling_s and 0 <= m <= self.capacity_kg)
        return self.read()


class OhausBalanceRuntime:
    def __init__(self, model, instance_id=None):
        if model not in PROFILES:
            raise ValueError('Unknown model')
        self.model = model
        self.p = PROFILES[model]
        self.px = model == 'PX224E'
        self.instance_id = instance_id or self.p['asset_type_id'].split('.')[-1] + '_01'
        b = legacy.Balance.for_model(self.p['legacy'])
        self.b = FirmwareBalance(**{k: getattr(b, k) for k in b.__dataclass_fields__})
        self.base_settling_s = self.b.settling_s
        self.b.transport_unlocked = True
        self.t = 0.0
        self.g_local = G0
        self.load_kg = 0.0
        self.doors = 0.0
        self.tilt, self.vibration = 0.0, False
        self.feet = {f: 0.0 for f in FEET}                                 # foot screw angles (rad)
        self.adapter = False
        self.batteries = not self.px                                       # SPX: 4 AA batteries fitted (URDF battery_1..4)
        self.rs232_kit = False
        self.err = dict(offset_kg=0.0, gain=0.0, nonlinearity=0.0)
        self.cal = dict(lin=None, last=None)                               # lin: [(reading_kg, true_kg)] piecewise correction from a linearity calibration
        self.s = default_settings(model)
        self.zero_kg = self.tare_kg = 0.0
        self.unit = 'g'
        self.app = self.p['apps'][0]
        self.appstate = self._fresh_apps()
        self.calproc = None                                                # running calibration procedure
        self.init_error = None                                             # power-on zero range error ('over' / 'under')
        self.fault = None                                                  # PX system error: dict(code, persistent) (fault.inject, p.46)
        self.header_date = None                                            # date of the last printout with the header lines (Single Header)
        self.password_ok = False
        self.last_motion_t = 0.0
        self.last_key_t = 0.0
        self.auto_tare_armed = True
        self.azt_ref = None
        self.interface = 'none'
        self.output = []
        self.last_print_t = None
        self.print_armed = True
        self.message = None
        self.clock_offset_s = 0.0
        self.power_epoch = 0
        self.revision = 0
        self._principals, self._requests, self._caller = {}, {}, None
        self.panel_capture_dir = None
        self._apply_settings()
        spec = PANEL_SPEC[model]
        self.panel = _panel_ui.PanelUI(spec, self) if spec is not None else None

    def _fresh_apps(self):
        return dict(counting=dict(apw_kg=None, sample=10, view='count'), percent=dict(ref_kg=None, view='percent'),
                    batch=dict(items=[], active=False), dynamic=dict(time_s=10, phase='ready', start=None, acc=0.0, n=0, result_kg=None),
                    density=dict(sample_type='solid', liquid='water', porous=False, water_temp_c=20.0, alcohol_temp_c=20.0, liquid_density=1.0, sinker_volume_ml=10.0,
                                 porous_weight_g=5.0, oil_density=0.8, result_digits=4, air_kg=None, liquid_kg=None, result=None),
                    formulation=dict(items=[], phase='component', filler_kg=None, view=0),
                    check=dict(low_kg=None, high_kg=None), total=dict(sum_kg=0.0, n=0, flash_until=None, armed=True), hold=dict(phase='idle', value_kg=None))

    # ---- sessions
    @property
    def time_s(self):
        return self.t

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator', 'debug'):
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return OhausBalanceSession(self, name)

    # ---- helpers
    def _on(self):
        return self.b.powered

    def _supply(self):
        return self.adapter or self.batteries

    def _need_on(self):
        if not self._supply():
            raise ActionRejected('NO_POWER')
        if not self.b.powered:
            raise ActionRejected('POWER_OFF')
        if self.fault:
            raise ActionRejected('SYSTEM_ERROR')                          # p.46: the balance shows the error and does not weigh

    def _d_kg(self):
        return self.p['d_g'] / 1000.0 * (self.s.get('graduations', 1) if self.px else 1)

    def _apply_settings(self):
        self.b.settling_s = self.base_settling_s * FILTER_FACTOR[self.s['filter']]
        self.b.band_d = float(self.s.get('stable_range', '0.5'))
        self.b.security_locked = self.b.security_locked

    def _corrected(self, gross_kg):
        """Stored linearity correction (piecewise linear through the captured points) applied to the span-calibrated reading."""
        pts = self.cal['lin']
        if not pts:
            return gross_kg
        (r0, m0), (r1, m1), (r2, m2) = pts
        if gross_kg <= r1:
            return m0 + (gross_kg - r0) * (m1 - m0) / (r1 - r0)
        return m1 + (gross_kg - r1) * (m2 - m1) / (r2 - r1)

    def _gross(self):
        return self._corrected(self.b.gross_kg)

    def _status(self):
        b = self.b
        if not self._supply() or not b.powered:
            return 'off'
        if self.fault:
            return 'fault'
        if self.init_error:
            return 'init_' + self.init_error
        if not b.pan_installed or not b.pan_latched:
            return 'underload'
        if not b.transport_unlocked:
            return 'locked'
        g = self._gross()
        if g > b.capacity_kg + 9 * self._d_kg():
            return 'overload'
        if g < -0.05 * b.capacity_kg:
            return 'underload'
        return 'stable' if b.stable else 'unstable'

    def _net(self):
        return self._gross() - self.zero_kg - self.tare_kg

    def _unit_step_g(self, unit):
        if unit in ('C', 'C1'):
            return None
        return nice_step(self.p['d_g'] * (self.s.get('graduations', 1) if self.px else 1) / GRAMS[unit])

    def _custom_factor(self):
        return self.s['custom_factor'] * 10 ** self.s['custom_exponent']

    def display_value(self, kg, unit=None):
        """(text, value) of a mass in the display unit, rounded to the unit's displayed resolution."""
        unit = unit or self.unit
        g = kg * 1000.0
        if unit in ('C', 'C1'):
            v = g * self._custom_factor()
            step = self.s['custom_lsd'] * 10 ** math.floor(math.log10(self._custom_factor() * self.p['d_g']) + 1e-9)   # LSD x the decade of 1 d
            v = round_to(v, step)
            dec = max(0, -int(math.floor(math.log10(step) + 1e-9)))
            return f'{v:.{dec}f}', v
        if unit == 'lb:oz':                                               # pounds in the first cell, the colon, ounces (SPX p.7 display colons)
            lb = int(abs(g) // GRAMS['lb'])
            step = self._unit_step_g('oz')
            oz = round_to(abs(g) / GRAMS['oz'] - lb * 16, step)
            dec = max(0, -int(math.floor(math.log10(step) + 1e-9)))
            return ('-' if g < 0 else '') + f'{lb}:{oz:0{dec + 3}.{dec}f}', g / GRAMS['lb']
        step = self._unit_step_g(unit)
        v = round_to(g / GRAMS[unit], step)
        dec = max(0, -int(math.floor(math.log10(step) + 1e-9)))
        return f'{v:.{dec}f}', v

    def _units_enabled(self):
        out = [u for u in self.p['units'] if u != 't' and self.s['units'].get(u, False)]
        if not self.px and self.s['t_unit'] != 'off':
            out.append(self.s['t_unit'])
        return out

    def _clock(self):
        return EPOCH + datetime.timedelta(seconds=self.clock_offset_s + self.t)

    def _locked(self, menu):
        """PX: a sub-menu is locked by the Lockout menu, by a password that was not entered (p.30-31, p.39), or - approved mode with the security
        switch locked - calibration, setup, application mode, unit and lockout (p.40)."""
        if not self.px:
            return False
        if self.s['approved'] and self.b.security_locked and menu in ('calibration', 'setup', 'units', 'mode', 'lockout'):
            return True
        if self.s['lockout'].get(menu):
            return True
        return bool(self.s['password']) and not self.password_ok and menu in ('calibration', 'setup', 'units', 'print', 'glp', 'rs232', 'factory')

    # ---- capability dispatch
    def invoke(self, capability_id, **args):
        if capability_id not in ('events.read', 'truth.inspect', 'panel.capture', 'panel.frame', 'sim.step') and not capability_id.startswith('panel.'):
            self.message = None
            if capability_id not in ('power.connect_adapter', 'pan.set_load', 'environment.set', 'doors.set', 'plant.configure', 'sensor.configure_error'):
                self.last_key_t = self.t
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'panel.capture', 'panel.frame'):
            self.revision += 1
        return out if out is not None else dict(state=self.summary())

    def _cmd_plant_configure(self, g_local=None, rs232_kit=None):
        if g_local is not None:
            self.g_local = g_local
        if rs232_kit is not None:
            if self.px:
                raise ActionRejected('INVALID_ARGUMENT')
            self.rs232_kit = rs232_kit
            if not rs232_kit:
                self.interface = 'none'

    def _cmd_power_connect_adapter(self, connected):
        self.adapter = connected
        if not self._supply():
            self.b.powered = False
            self.calproc = None

    def _cmd_power_set_batteries(self, installed):
        self.batteries = installed
        if not self._supply():
            self.b.powered = False
            self.calproc = None

    def _cmd_power_set(self, on):
        if not self._supply():
            raise ActionRejected('NO_POWER')
        if self.fault and self.b.powered:
            raise ActionRejected('SYSTEM_ERROR')                          # keys do nothing in a system error; the remedy is to disconnect the power
        if on and not self.b.powered:
            if self.fault and self.fault['code'] in RESTART_CLEARS and not self.fault['persistent']:
                self.fault = None                                         # p.46: disconnect the power and restart
            self.b.powered = True
            self.b.stable = False
            self.b.elapsed_s = 0
            self.power_epoch += 1
            self.password_ok = False
            self.last_motion_t = self.t
            self._step_legacy(0.0)
            g = self._gross()
            lim = INIT_ZERO_RANGE * self.b.capacity_kg
            if not self.b.pan_installed or g < -lim:
                self.init_error = 'under'                                 # p.46: under init. range / SPX Err 8.2
            elif g > lim:
                self.init_error = 'over'                                  # p.46: over init. range / SPX Err 8.1
            else:
                self.init_error = None
                self.zero_kg, self.tare_kg = g, 0.0                       # power-on zero
            self.message = 'power_on'
        elif not on and self.b.powered:
            self.b.powered = False
            self.calproc = None

    def _cmd_pan_set_load(self, mass_kg):
        if abs(mass_kg - self.load_kg) > 0.5 * self.p['d_g'] / 1000.0:
            self.last_motion_t = self.t
        self.load_kg = mass_kg

    def _cmd_pan_remove(self, unlock_angle_rad=0.0):
        try:
            self.b.remove_pan(unlock_angle_rad)
        except ValueError:
            raise ActionRejected('PAN_LOCKED') from None

    def _cmd_pan_install(self):
        self.b.install_pan()

    def _cmd_transport_lock_set(self, angle_rad):
        try:
            self.b.set_transport_lock(angle_rad)
        except ValueError:
            raise ActionRejected('PAN_INSTALLED') from None

    def _cmd_doors_set(self, open_fraction):
        self.doors = open_fraction

    def _cmd_security_set_switch(self, locked):
        self.b.security_locked = locked

    def _cmd_level_turn_foot(self, foot, angle_rad):
        new = self.feet[foot] + angle_rad
        if abs(new) > 4 * math.pi:
            raise ActionRejected('FOOT_LIMIT')                            # legacy runtime: +-2 turns
        self.feet[foot] = new

    def _tilt_total(self):
        """Bench tilt plus the tilt the feet make (legacy foot_extension_m, 1 mm per turn): the larger of the two axis slopes."""
        e = {f: legacy.foot_extension_m(a) for f, a in self.feet.items()}
        lr = ((e['foot_front_left'] + e['foot_rear_left']) - (e['foot_front_right'] + e['foot_rear_right'])) / 2 / FOOT_SPAN_M[0]
        fr = ((e['foot_front_left'] + e['foot_front_right']) - (e['foot_rear_left'] + e['foot_rear_right'])) / 2 / FOOT_SPAN_M[1]
        return math.hypot(self.tilt + lr, fr)

    def _cmd_environment_set(self, tilt_rad=None, vibration=None):
        if tilt_rad is not None:
            self.tilt = tilt_rad
        if vibration is not None:
            self.vibration = vibration

    def _need_weighing(self):
        self._need_on()
        st = self._status()
        if st.startswith('init_') or st in ('underload', 'overload', 'locked'):
            raise ActionRejected('NOT_WEIGHING')

    def _cmd_weighing_zero(self):
        self._need_weighing()
        if not self.b.stable:
            raise ActionRejected('UNSTABLE')
        g = self._gross()
        if abs(g) > ZERO_RANGE * self.b.capacity_kg:
            raise ActionRejected('ZERO_RANGE')
        self.zero_kg, self.tare_kg = g, 0.0
        self.azt_ref = None

    def _cmd_weighing_tare(self):
        self._need_weighing()
        if not self.b.stable:
            raise ActionRejected('UNSTABLE')
        t = self._gross() - self.zero_kg
        if t < -0.5 * self._d_kg():
            raise ActionRejected('TARE_RANGE')                            # SPX Err 8.5
        self.tare_kg = max(0.0, t)

    def _cmd_weighing_set_unit(self, unit):
        self._need_on()
        if self.b.security_locked and not self.px:
            raise ActionRejected('SECURITY_LOCKED')                       # SPX: units locked with the security switch
        if unit not in self._units_enabled():
            raise ActionRejected('UNIT_DISABLED')
        self.unit = unit

    def _cmd_weighing_next_unit(self):
        self._need_on()
        if self.b.security_locked:
            raise ActionRejected('SECURITY_LOCKED')
        us = self._units_enabled()
        self.unit = us[(us.index(self.unit) + 1) % len(us)] if self.unit in us else us[0]

    def _cmd_app_next(self):
        self._need_on()
        apps = [a for a in self.p['apps'] if self._app_enabled(a)]
        nxt = apps[(apps.index(self.app) + 1) % len(apps)] if self.app in apps else apps[0]
        self._cmd_app_select(nxt)

    def _cmd_interface_connect(self, device):
        if device != 'none' and not self.px and not self.rs232_kit:
            raise ActionRejected('NO_INTERFACE')
        self.interface = device

    def _settings_menu(self, key):
        px_menu = {'password': 'setup', 'language': 'setup', 'filter': 'setup', 'azt': 'setup', 'auto_tare': 'setup', 'graduations': 'setup', 'date_format': 'setup',
                   'time_format': 'setup', 'brightness': 'setup', 'auto_dim': 'setup', 'auto_off': 'setup', 'capacity_bar': 'setup', 'approved': 'setup',
                   'units': 'units', 'custom_factor': 'units', 'custom_exponent': 'units', 'custom_lsd': 'units', 'baud': 'rs232', 'transmission': 'rs232',
                   'handshake': 'rs232', 'glp_header': 'glp', 'balance_name_text': 'glp', 'user_name_text': 'glp', 'project_name_text': 'glp', 'lockout': 'lockout'}
        return px_menu.get(key, 'print')

    @staticmethod
    def _pw_text(digits):
        t = '' if not digits else str(int(digits))
        if not re.fullmatch('[123]{0,6}', t):
            raise ActionRejected('INVALID_ARGUMENT')
        return t

    def _cmd_settings_configure(self, **kw):
        self._need_on()
        if 'password_digits' in kw:
            kw['password'] = self._pw_text(kw.pop('password_digits'))
        s = self.s
        for k, v in kw.items():
            if self.px:
                menu = self._settings_menu(k)
                if k == 'approved' and v != s['approved'] and v != self.b.security_locked:
                    raise ActionRejected('SECURITY_SWITCH')               # p.33: switch locked to set On, unlocked to set Off
                if menu == 'lockout':
                    if self.b.security_locked:
                        raise ActionRejected('SECURITY_LOCKED')           # p.40: the Lock menu can be viewed, not changed
                    if s['approved']:
                        raise ActionRejected('MENU_HIDDEN')
                elif self._locked(menu) and k != 'approved':
                    raise ActionRejected('MENU_LOCKED')
                if s['approved'] and k in ('filter', 'auto_tare', 'graduations', 'stable_only', 'numeric_only'):
                    raise ActionRejected('APPROVED_MODE')
                if s['approved'] and k == 'azt' and v not in ('off', '0.5'):
                    raise ActionRejected('APPROVED_MODE')
                if s['approved'] and k == 'auto_print' and v == 'continuous':
                    raise ActionRejected('APPROVED_MODE')
            elif self.b.security_locked and k in ('units', 't_unit', 'custom_factor', 'custom_exponent', 'custom_lsd'):
                raise ActionRejected('SECURITY_LOCKED')                   # SPX p.11, p.15: units locked with the security switch
            if not self.px and k in ('stable_only', 'a_print', 'print_interval_s', 'content', 'layout_format', 'feed', 'baud', 'parity', 'stop_bit', 'handshake',
                                     'alt_print', 'alt_tare', 'alt_zero') and not self.rs232_kit:
                raise ActionRejected('NO_INTERFACE')
            if isinstance(s.get(k), dict) and k != 'glp_header':
                unknown = set(v) - set(s[k])
                if unknown:
                    raise ActionRejected('INVALID_ARGUMENT')
                s[k] = dict(s[k], **v)
                if k == 'modes' and not any(s['modes'].values()) and s['hold'] == 'off':
                    raise ActionRejected('NO_MODE')
            elif k == 'glp_header':
                hdr = list(s['glp_header'])
                for i, txt in v.items():
                    if not str(i).isdigit() or not 1 <= int(i) <= 5 or not isinstance(txt, str) or len(txt) > 25:
                        raise ActionRejected('INVALID_ARGUMENT')
                    hdr[int(i) - 1] = txt
                s['glp_header'] = hdr
            else:
                s[k] = v
        if self.px and s['approved']:
            s['graduations'] = 1                                          # p.33: forced to 1 division in approved mode
            s['stable_only'], s['numeric_only'] = True, False
        if 'units' in kw or 't_unit' in kw:
            if self.unit not in self._units_enabled():
                self.unit = (self._units_enabled() or ['g'])[0]
        if 'modes' in kw or 'hold' in kw:
            if not self._app_enabled(self.app):
                self.app = next(a for a in self.p['apps'] if self._app_enabled(a))
        self._apply_settings()

    def _cmd_settings_reset(self, menu):
        self._need_on()
        d = default_settings(self.model)
        if self.px:
            if self._locked('factory'):
                raise ActionRejected('MENU_LOCKED')
            keep = dict(approved=self.s['approved'])
            self.s = d
            self.s.update(keep)
            self.cal['lin'] = None                                        # assumption: Reset All clears the stored linearity correction too
        else:
            groups = dict(setup=['filter', 'azt', 'stable_range', 'backlight', 'auto_tare', 'auto_off'], mode=['modes', 'hold'],
                          unit=['units', 't_unit', 'custom_factor', 'custom_exponent', 'custom_lsd'],
                          print=['stable_only', 'a_print', 'print_interval_s', 'content', 'layout_format', 'feed'],
                          rs232=['baud', 'parity', 'stop_bit', 'handshake', 'alt_print', 'alt_tare', 'alt_zero'])
            if menu in ('print', 'rs232') and not self.rs232_kit:
                raise ActionRejected('NO_INTERFACE')
            if menu == 'unit' and self.b.security_locked:
                raise ActionRejected('SECURITY_LOCKED')
            for k in groups[menu]:
                self.s[k] = copy.deepcopy(d[k])
        if self.unit not in self._units_enabled():
            self.unit = self._units_enabled()[0]
        if not self._app_enabled(self.app):
            self.app = self.p['apps'][0]
        self._apply_settings()

    def _cmd_settings_enter_password(self, password=None, password_digits=None):
        self._need_on()
        if (password is None) == (password_digits is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if password is None:
            password = self._pw_text(password_digits)
        ok = not self.s['password'] or password == self.s['password']
        self.password_ok = ok
        return dict(state=self.summary(), accepted=ok)

    def _cmd_clock_set(self, **kw):
        self._need_on()
        if self._locked('setup'):
            raise ActionRejected('MENU_LOCKED')
        try:
            now = self._clock().replace(microsecond=0, **kw)
        except ValueError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        self.clock_offset_s = (now - EPOCH).total_seconds() - self.t

    def _cmd_glp_set_header(self, index, text):
        self._cmd_settings_configure(glp_header={str(index): text})

    # ---- calibration (PX p.27-30; SPX p.7, p.11)
    def _cmd_calibration_start(self, kind):
        self._need_on()
        if self.b.security_locked:
            raise ActionRejected('SECURITY_LOCKED')                       # SPX: calibration hidden; PX: approved / sealed
        if self._locked('calibration'):
            raise ActionRejected('MENU_LOCKED')
        if self.px and self.s['approved']:
            raise ActionRejected('APPROVED_MODE')                          # ExCal model in approved mode: no user calibration (p.33)
        pts = [0.0] + ([self.p['span_points_g'][0]] if kind == 'span' else list(self.p['lin_points_g']))
        self.calproc = dict(kind=kind, points_g=pts, step=0, readings=[], since=self.t, result=None)

    def _cmd_calibration_select_point(self):
        self._need_on()
        cp = self.calproc
        if cp is None or cp['kind'] != 'span' or cp['result'] is not None:
            raise ActionRejected('NO_CALIBRATION')
        sp = self.p['span_points_g']
        cp['points_g'][1] = sp[1] if cp['points_g'][1] == sp[0] else sp[0]

    def _cmd_calibration_abort(self):
        self._need_on()
        if self.calproc is None:
            raise ActionRejected('NO_CALIBRATION')
        self.calproc = None

    def _cal_tick(self):
        """The balance captures each calibration point itself once the reading is stable (PX p.28-30; SPX p.7 [--C--])."""
        cp = self.calproc
        if cp is None or cp['result'] is not None or not self.b.powered:
            return
        if not self.b.stable:
            if self.t - cp['since'] >= CAL_TIMEOUT_S - 1e-9:
                cp['result'] = 'timeout'
            return
        expected = cp['points_g'][cp['step']] / 1000.0
        g = self.b.gross_kg
        if cp['step'] == 0:
            if abs(g) > 0.05 * self.b.capacity_kg:
                return                                                    # waiting for an empty pan
        elif cp['step'] > 1 and abs(g - cp['readings'][-1]) < CAL_TOLERANCE * (expected - cp['points_g'][cp['step'] - 1] / 1000.0):
            return                                                        # the previous point's mass is still on the pan
        elif abs(g - cp['readings'][0] - expected) > CAL_TOLERANCE * expected:
            if abs(g - cp['readings'][0]) > 0.2 * expected:
                cp['result'] = 'fail'                                     # wrong calibration masses (p.45)
            return                                                        # (a nearly empty pan: still waiting)
        cp['readings'].append(g)
        cp['since'] = self.t
        cp['step'] += 1
        self.b.elapsed_s = 0
        self.b.stable = False
        if cp['step'] < len(cp['points_g']):
            return
        r = cp['readings']
        if cp['kind'] == 'span':
            gain = (r[1] - r[0]) / (cp['points_g'][1] / 1000.0)
            self.b.g_cal *= gain                                          # the legacy force calibration (g_cal = force / mass)
            self.cal['lin'] = None
        else:
            gain = (r[2] - r[0]) / (cp['points_g'][2] / 1000.0)
            self.b.g_cal *= gain
            mid = (r[1] - r[0]) / gain
            self.cal['lin'] = [(0.0, 0.0), (mid, cp['points_g'][1] / 1000.0), (cp['points_g'][2] / 1000.0, cp['points_g'][2] / 1000.0)]
        self.b.calibrated = True
        self.zero_kg = self.tare_kg = 0.0
        self.cal['last'] = dict(kind=cp['kind'], time=self._clock().isoformat(timespec='seconds'), points_g=cp['points_g'][1:])
        cp['result'] = 'done'

    def _cmd_sensor_configure_error(self, **kw):
        self.err.update(kw)

    # ---- applications (PX p.12-25; SPX p.8-10)
    def _app_enabled(self, app):
        if self.px:
            return True
        if app == 'hold':
            return self.s['hold'] != 'off'
        return self.s['modes'].get(app, False)

    def _cmd_app_select(self, app):
        self._need_on()
        if self._locked('mode'):
            raise ActionRejected('MENU_LOCKED')
        if not self._app_enabled(app):
            raise ActionRejected('MODE_DISABLED')
        self.app = app
        a = self.appstate
        a['counting']['view'], a['percent']['view'] = 'count', 'percent'
        a['check']['view'] = 'weight'
        if app in ('dynamic',):
            a['dynamic'].update(phase='ready', start=None, acc=0.0, n=0, result_kg=None)
        if app == 'density':
            a['density'].update(air_kg=None, liquid_kg=None, result=None)
        if app == 'formulation':
            a['formulation'].update(items=[], phase='component', filler_kg=None, view=0)
        if app in ('weighing',):
            a['batch'].update(items=[], active=self.s.get('batch_printing', False))
        if app == 'hold':
            a['hold'].update(phase='idle', value_kg=None)

    def _need_stable(self):
        self._need_weighing()
        if not self.b.stable:
            raise ActionRejected('UNSTABLE')

    def _cmd_app_function(self):
        """Function key: the action of the active application."""
        self._need_on()
        a, app = self.appstate, self.app
        if app in ('counting', 'count'):
            c = a['counting']
            if c['apw_kg'] is None:
                return self._cmd_counting_establish()
            c['view'] = {'count': 'apw', 'apw': 'weight', 'weight': 'count'}[c['view']] if self.px else ('apw' if c['view'] == 'count' else 'count')
        elif app == 'percent':
            pc = a['percent']
            if pc['ref_kg'] is None:
                return self._cmd_percent_establish()
            pc['view'] = {'percent': 'reference', 'reference': 'weight', 'weight': 'percent'}[pc['view']] if self.px else (
                'reference' if pc['view'] == 'percent' else 'percent')
        elif app == 'weighing':
            if self.s.get('batch_printing'):
                return self._cmd_batch_store()
            raise ActionRejected('NO_FUNCTION')
        elif app == 'density':
            self._need_stable()
            dn = a['density']
            if dn['result'] is not None:
                dn.update(air_kg=None, liquid_kg=None, result=None)       # next sample
            elif dn['air_kg'] is None:
                dn['air_kg'] = self._net()
            else:
                dn['liquid_kg'] = self._net()
                dn['result'] = self._density_result()
        elif app == 'formulation':
            f = a['formulation']
            if f['phase'] == 'component':
                self._need_stable()
                if len(f['items']) >= 50:
                    raise ActionRejected('OUT_OF_RANGE')
                f['items'].append(self._net())
            elif f['phase'] == 'filler':
                self._need_stable()
                f['filler_kg'] = self._net()
                f['phase'] = 'total'
            else:
                f['view'] = (f['view'] + 1) % (len(f['items']) + 1 + (1 if f['filler_kg'] is not None else 0))
        elif app == 'dynamic':
            raise ActionRejected('NO_FUNCTION')
        elif app == 'check':
            a['check']['view'] = 'limits' if a['check'].get('view') != 'limits' else 'weight'
        elif app == 'total':
            self._need_stable()
            n = self._net()
            if n <= 0:
                raise ActionRejected('NOT_POSITIVE')                       # only positive numbers are totalized (p.10)
            t = a['total']
            if t['sum_kg'] + n > 999999 * self._d_kg() * 10:
                raise ActionRejected('DISPLAY_OVERFLOW')                  # Err 8.6
            t['sum_kg'] += n
            t['n'] += 1
            t['flash_until'] = self.t + 2.0
        elif app == 'hold':
            h = a['hold']
            if h['phase'] == 'idle':
                h['phase'] = 'ready'                                      # [rEAdY] until a weight is added
            elif h['phase'] == 'held' and abs(self._net()) < 5 * self._d_kg():
                h.update(phase='idle', value_kg=None)                     # reset with an empty pan
            elif h['phase'] == 'held':
                raise ActionRejected('PAN_NOT_EMPTY')
        else:
            raise ActionRejected('NO_FUNCTION')

    def _cmd_counting_clear_apw(self):
        self._need_on()
        self.appstate['counting'].update(apw_kg=None, view='count')

    def _cmd_counting_set_sample_size(self, size):
        self._need_on()
        self.appstate['counting']['sample'] = int(size)

    def _cmd_counting_establish(self):
        self._need_stable()
        c = self.appstate['counting']
        n = self._net()
        apw = n / c['sample']
        too_small = apw < 0.1 * self._d_kg() if self.px else n < self._d_kg()
        if too_small:
            raise ActionRejected('LOW_REFERENCE')                         # PX: APW < 0.1 d ('Low ref. weight'); SPX: sample < 1 d (ref.err)
        c.update(apw_kg=apw, view='count')

    def _cmd_percent_clear_reference(self):
        self._need_on()
        self.appstate['percent'].update(ref_kg=None, view='percent')

    def _cmd_percent_establish(self):
        self._need_stable()
        n = self._net()
        if n < (100 if self.px else 1) * self._d_kg():
            raise ActionRejected('LOW_REFERENCE')                         # PX: reference < 100 d ('Invalid'); SPX: < 1 d (ref.err)
        self.appstate['percent'].update(ref_kg=n, view='percent')

    def _cmd_batch_store(self):
        self._need_stable()
        bt = self.appstate['batch']
        if len(bt['items']) >= 10:
            self.message = 'End' if len(bt['items']) == 10 and not bt.get('ended') else 'Out of range'
            bt['ended'] = True
            raise ActionRejected('BATCH_FULL')
        bt['items'].append(self._net())

    def _cmd_batch_finish(self):
        self._need_on()
        bt = self.appstate['batch']
        self._print(batch=list(bt['items']))
        bt.update(items=[], ended=False)

    def _cmd_dynamic_set_time(self, time_s):
        self._need_on()
        self.appstate['dynamic'].update(time_s=int(time_s), phase='ready', start=None, acc=0.0, n=0, result_kg=None)

    def _cmd_density_configure(self, **kw):
        self._need_on()
        self.appstate['density'].update(kw)
        self.appstate['density'].update(air_kg=None, liquid_kg=None, result=None)

    def _density_result(self):
        """Archimedes (PX p.17-23): solid rho = A / (A - B) (rho_L - rho_air) + rho_air; liquid rho = (A - B) / V + rho_air; porous: oiled weights."""
        dn = self.appstate['density']
        rho_air = 0.0012
        A, B = dn['air_kg'] * 1000.0, dn['liquid_kg'] * 1000.0
        if dn['sample_type'] == 'liquid':
            return (A - B) / dn['sinker_volume_ml'] + rho_air
        rho_l = {'water': water_density(dn['water_temp_c']), 'alcohol': ethanol_density(dn['alcohol_temp_c']), 'other': dn['liquid_density']}[dn['liquid']]
        if A - B == 0:
            raise ActionRejected('INVALID_ARGUMENT')
        if dn['porous']:
            dry = dn['porous_weight_g']
            vol = (A - B) / (rho_l - rho_air) - (A - dry) / dn['oil_density']
            return dry / vol if vol > 0 else None
        return A / (A - B) * (rho_l - rho_air) + rho_air

    def _cmd_formulation_finish(self):
        self._need_on()
        f = self.appstate['formulation']
        if f['phase'] != 'component' or not f['items']:
            raise ActionRejected('NO_FUNCTION')
        f['phase'] = 'filler'

    def _cmd_formulation_skip_filler(self):
        self._need_on()
        f = self.appstate['formulation']
        if f['phase'] != 'filler':
            raise ActionRejected('NO_FUNCTION')
        f['phase'] = 'total'

    def _cmd_check_set_limits(self, low_kg=None, high_kg=None):
        self._need_on()
        c = self.appstate['check']
        if low_kg is not None:
            c['low_kg'] = low_kg
        if high_kg is not None:
            c['high_kg'] = high_kg

    def _cmd_check_clear(self):
        self._need_on()
        self.appstate['check'].update(low_kg=None, high_kg=None)

    def _cmd_totalize_clear(self):
        self._need_on()
        self.appstate['total'].update(sum_kg=0.0, n=0, flash_until=None)

    # ---- printing (PX p.36-38, p.42-44; SPX RS232 kit p.4-5)
    def _cmd_print_send(self):
        self._need_weighing()
        if self.px and self.app == 'weighing' and self.s['batch_printing'] and self.appstate['batch']['items']:
            return self._cmd_batch_finish()
        if not self.px and self.s['a_print'] != 'off' and self.rs232_kit:
            raise ActionRejected('AUTO_PRINT')                           # SPX: Print sends only when AUTOPRINT is off (p.6)
        if self.s['stable_only'] and not self.b.stable:
            raise ActionRejected('UNSTABLE')
        self._print()

    def _print_line(self, label, kg, flag=None, unit=None):
        txt, _ = self.display_value(kg, unit)
        stab = '' if self.b.stable else '?'
        return f'{label}{txt:>11} {(unit or self.unit):>5} {stab:1} {flag or "":>2}'.rstrip()

    def _sics_line(self, kg, ident='S'):
        """PX Output Format SICS ('the Mettler Toledo format', p.37): an MT-SICS weight response (MT-SICS reference manual, commands S / TA):
        identification, status S (stable) or D (dynamic; A for the tare memory), the value right aligned in 10 characters, the unit."""
        txt, _ = self.display_value(kg)
        return f'{ident} {"A" if ident == "TA" else "S" if self.b.stable else "D"} {txt:>10} {self.unit}'

    def _data_line(self, label, kg, flag):
        """A G/N/T data line in the selected Output Format (p.42: the format applies to the result and G/N/T data); MT-SICS has no gross
        response, so the Gross line is left out in SICS (assumption)."""
        if self.px and self.s['output_format'] == 'SICS':
            return None if flag == 'G' else self._sics_line(kg, 'TA' if flag == 'T' else 'S')
        return self._print_line(label, kg, flag)

    def _result_lines(self):
        a = self.appstate
        app = self.app
        if app in ('counting', 'count') and a['counting']['apw_kg']:
            return [f'Quantity: {self._count()} PCS']
        if app == 'percent' and a['percent']['ref_kg']:
            return [f'Percentage: {self._percent():.3f} %']
        if app == 'density' and a['density']['result'] is not None:
            return [f'Density: {a["density"]["result"]:.4f} g/cm3']
        if self.px and self.s['output_format'] == 'SICS':
            return [self._sics_line(self._net())]
        return [self._print_line('', self._net(), 'N' if self.tare_kg else 'G')]

    def _print(self, batch=None):
        if self.interface == 'none' or (self.px and self.interface != self.s['print_to']):
            return                                                         # PX Print To (p.36): the data go to the PC or to the printer
        s = self.s
        lines = []
        if self.px:
            if s['numeric_only']:
                lines = [f'{self.display_value(self._net())[0]}']
            else:
                today = self._clock().date().isoformat()
                if s['header'] and (not s['single_header'] or self.header_date != today):
                    lines += [h for h in s['glp_header']]                 # Single Header On: the header lines once a day (p.36)
                    self.header_date = today
                if s['date_time']:
                    fmt = {'YYYY/MM/DD': '%Y/%m/%d', 'MM/DD/YYYY': '%m/%d/%Y', 'DD/MM/YYYY': '%d/%m/%Y'}[s['date_format']]
                    lines.append(self._clock().strftime(fmt + (' %H:%M:%S' if s['time_format'] == '24HR' else ' %I:%M:%S %p')))
                if s['balance_id']:
                    lines.append('Balance ID: B000000001')
                if s['balance_name']:
                    lines.append(f'Balance Name: {s["balance_name_text"]}')
                if s['user_name']:
                    lines.append(f'User Name: {s["user_name_text"]}')
                if s['project_name']:
                    lines.append(f'Project Name: {s["project_name_text"]}')
                if s['application_name']:
                    lines.append({'weighing': 'Weighing', 'counting': 'Parts Counting', 'percent': 'Percent Weighing', 'dynamic': 'Dynamic Weighing',
                                  'density': 'Density', 'formulation': 'Formulation'}[self.app])
                if batch is not None:
                    lines += ['---- Sample Data (g) -----'] + [f'{i + 1}: {self.display_value(v, "g")[0]} g' for i, v in enumerate(batch)]
                elif s['result']:
                    lines += self._result_lines()
                for on, label, kg, flag in ((s['gross'], 'Gross: ', self._gross() - self.zero_kg, 'G'), (s['net'], 'Net: ', self._net(), 'N'),
                                            (s['tare'], 'Tare: ', self.tare_kg, 'T')):
                    ln = self._data_line(label, kg, flag) if on else None
                    if ln is not None:
                        lines.append(ln)
                if s['signature']:
                    lines += ['Signature: ____________', 'Verified By: ____________']
            lines += [''] * (s['line_feed'] - 1)
        else:
            c = s['content']
            res = [self._print_line('', self._net(), '')]
            if c['gross']:
                res.append(self._print_line('', self._gross() - self.zero_kg, 'G'))
            if c['net']:
                res.append(self._print_line('', self._net(), 'N'))
            if c['tare']:
                res.append(self._print_line('', self.tare_kg, 'T'))
            lines = res if c['result'] or len(res) == 1 else res[1:]
            if s['layout_format'] == 'single':
                lines = [' '.join(x.strip() for x in lines)]
            lines += [''] * ({'line': 0, '4lines': 3, 'form': 0}[s['feed']])
            if s['feed'] == 'form':
                lines.append('\f')
        for ln in lines:
            self.output.append(dict(time_s=round(self.t, 3), device=self.interface, line=ln))
        del self.output[:-OUT_MAX]
        self.last_print_t = self.t

    # ---- derived readings
    def _count(self):
        apw = self.appstate['counting']['apw_kg']
        return int(math.floor(self._net() / apw + 0.5)) if apw else None

    def _percent(self):
        ref = self.appstate['percent']['ref_kg']
        return self._net() / ref * 100.0 if ref else None

    def _check_state(self):
        c = self.appstate['check']
        if c['low_kg'] is None or c['high_kg'] is None:
            return None
        n = self._net()
        lo, hi = min(c['low_kg'], c['high_kg']), max(c['low_kg'], c['high_kg'])
        return 'under' if n < lo else 'over' if n > hi else 'accept'

    # ---- time
    def _step_legacy(self, dt):
        raw = self.load_kg * self.g_local
        e = self.err
        cap = self.b.capacity_kg
        raw = raw * (1 + e['gain']) + e['offset_kg'] * self.g_local + 4 * e['nonlinearity'] * cap * self.g_local * (self.load_kg / cap) * (1 - self.load_kg / cap)
        self.b.update(raw, dt, door_fraction=self.doors if self.p['analytical'] else 0.0, tilt_rad=self._tilt_total(), vibration=self.vibration)

    def _cmd_fault_inject(self, code, persistent=False):
        self.fault = dict(code=code, persistent=bool(persistent) or code not in RESTART_CLEARS)
        self.calproc = None                                               # a calibration in progress is lost

    def _cmd_fault_clear(self):
        self.fault = None

    def step(self, dt_s):
        """Advance the host clock: the legacy settling, then the firmware (calibration captures, AZT, auto tare, applications, auto print,
        auto off).  Large steps are taken in 0.1 s pieces while something depends on time (dynamic weighing, auto print, calibration)."""
        remaining = dt_s
        while remaining > 1e-12:
            h = min(remaining, 0.1 if self._busy() else remaining)
            self._tick(h)
            remaining -= h
        return self.t

    def _busy(self):
        return self.b.powered and (self.calproc is not None or self.app == 'dynamic' or self.s.get('auto_print', self.s.get('a_print')) != 'off' or
                                   self.s['azt'] != 'off' or self.b.elapsed_s < self.b.settling_s + 1)

    def _tick(self, h):
        was_stable = self.b.stable
        self._step_legacy(h)
        self.t += h
        if not self.b.powered or self.fault:
            return
        if self.init_error and self.b.stable:
            g = self._gross()
            lim = INIT_ZERO_RANGE * self.b.capacity_kg
            if -lim <= g <= lim and self.b.pan_installed:
                self.init_error = None                                    # load removed / pan installed: zeroes as at power-on
                self.zero_kg, self.tare_kg = g, 0.0
        self._cal_tick()
        d = self._d_kg()
        st = self.b.stable
        net = self._net()
        # auto zero tracking: keep zero while the gross reading near zero drifts slower than the AZT limit per second (PX p.31, SPX p.12)
        if self.s['azt'] != 'off' and st and self.tare_kg == 0.0 and self.calproc is None:
            g = self._gross()
            if abs(g - self.zero_kg) <= 2 * d:
                lim = float(self.s['azt']) * d * max(h, 1e-9)
                self.zero_kg += max(-lim, min(lim, g - self.zero_kg))
        # auto tare (PX p.32; SPX p.12): the first stable gross load is tared; re-armed when the pan is cleared
        at = self.s['auto_tare']
        if at in (True, 'on', 'on_acc') and self.calproc is None:
            gz = self._gross() - self.zero_kg
            if st and self.auto_tare_armed and self.tare_kg == 0.0 and gz > 5 * d and (at != 'on_acc' or self._check_state() == 'accept'):
                self.tare_kg = gz
                self.auto_tare_armed = False
            elif gz < 0.5 * d and self.tare_kg != 0.0 and not self.auto_tare_armed:
                self.tare_kg = 0.0
                self.auto_tare_armed = True
            elif abs(gz) < 0.5 * d:
                self.auto_tare_armed = True
        a = self.appstate
        if self.app == 'dynamic':
            dy = a['dynamic']
            if dy['phase'] == 'ready' and net > 5 * d:
                dy.update(phase='averaging', start=self.t, acc=0.0, n=0)
            elif dy['phase'] == 'averaging':
                dy['acc'] += net * h
                dy['n'] += h
                if self.t - dy['start'] >= dy['time_s'] - 1e-9:
                    dy.update(phase='held', result_kg=dy['acc'] / max(dy['n'], 1e-9))
            elif dy['phase'] == 'held' and abs(self._gross() - self.zero_kg - self.tare_kg) < 5 * d and st:
                self.zero_kg = self._gross() - self.tare_kg             # removed: set to zero, back to Ready (p.13)
                dy.update(phase='ready', result_kg=None)
        if self.app == 'hold':
            ho = a['hold']
            if ho['phase'] == 'ready' and st and net >= 5 * d:
                ho.update(phase='held', value_kg=net)
            elif ho['phase'] == 'held' and self.s['hold'] == 'peak' and st and net > ho['value_kg']:
                ho['value_kg'] = net
        if self.px and self.app == 'counting':
            c = a['counting']                                             # APW optimisation (p.15): 1-3 x the pieces already on the pan
            if c['apw_kg'] and st and not was_stable and c.get('opt_count'):
                n = self._count()
                if c['opt_count'] < n <= 3 * c['opt_count'] + c['opt_count']:
                    c['apw_kg'] = net / n
                c['opt_count'] = n
            elif c['apw_kg'] and st:
                c['opt_count'] = self._count()
        self._auto_print(st, was_stable, net, h)
        self._auto_off()

    def _auto_print(self, st, was_stable, net, h):
        if self.interface == 'none':
            return
        mode = self.s['auto_print'] if self.px else (self.s['a_print'] if self.rs232_kit else 'off')
        d = self._d_kg()
        if mode == 'off':
            return
        if mode == 'continuous':
            if self.last_print_t is None or self.t - self.last_print_t >= 1.0 - 1e-9:   # assumption: one line per second
                self._print()
        elif mode in ('interval',):
            iv = self.s['print_interval_s']
            if iv > 0 and (self.last_print_t is None or self.t - self.last_print_t >= iv - 1e-9):
                self._print()
        elif mode in ('on_stability', 'on_stable_load', 'on_stable_load_zero', 'accept'):
            zero_too = (self.s.get('auto_print_condition') == 'load_zero') if self.px else mode == 'on_stable_load_zero'
            if st and not was_stable and self.print_armed and (abs(net) > d or zero_too) and (mode != 'accept' or self._check_state() == 'accept'):
                self._print()
                self.print_armed = False
            if not st:
                self.print_armed = True

    def _auto_off(self):
        ao = self.s['auto_off']
        if ao == 'off':
            return
        idle = float(ao) * 60.0
        if self.t - max(self.last_motion_t, self.last_key_t) >= idle - 1e-9:
            self.b.powered = False
            self.calproc = None

    # ---- front panel host
    def panel_read(self):
        out = self.read('agent')
        out.update({'host.powered': self.b.powered and self._supply(), 'host.power_epoch': self.power_epoch})
        return out

    def panel_invoke(self, capability_id, arguments):
        try:
            validate(CAPS[self.model][capability_id]['input_schema'], arguments)
            snap = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ('panel', '_principals', '_requests', '_caller')})
            try:
                return self.invoke(capability_id, **arguments)
            except ActionRejected:
                self.__dict__.update(snap)
                raise
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def _panel(self, gesture, *args):
        try:
            return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_panel_press(self, input_id, duration_s=0.1):
        self.last_key_t = self.t
        return self._panel(self.panel.press, input_id, float(duration_s))

    def _cmd_panel_turn(self, input_id, detents):
        return self._panel(self.panel.turn, input_id, int(detents))

    def _cmd_panel_switch(self, input_id, position):
        return self._panel(self.panel.switch, input_id, position)

    def _cmd_panel_capture(self, display_id):
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

    def _cmd_events_read(self):
        return dict(output=dict(lines=copy.deepcopy(self.output)))

    def _cmd_truth_inspect(self):
        return dict(process=dict(load_kg=self.load_kg, raw_gross_kg=self.b.gross_kg, corrected_gross_kg=self._gross(), g_cal=self.b.g_cal, g_local=self.g_local,
                                 sensor_error=dict(self.err), linearity=copy.deepcopy(self.cal['lin']), stable=self.b.stable, elapsed_s=self.b.elapsed_s))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- observation
    def summary(self):
        return dict(powered=self.b.powered, status=self._status(), unit=self.unit, app=self.app, message=self.message)

    def _main_value(self):
        """(text, unit text, kind) of the main display field for the active application."""
        st = self._status()
        a, app = self.appstate, self.app
        if st in ('off', 'fault') or st.startswith('init_') or st in ('overload', 'underload', 'locked'):
            return None, None, st
        if app in ('counting', 'count') and a['counting']['apw_kg']:
            c = a['counting']
            if c['view'] == 'apw':
                return self.display_value(c['apw_kg'])[0], self.unit, 'apw'
            if c['view'] == 'weight':
                return self.display_value(self._net())[0], self.unit, 'weight'
            return str(self._count()), 'PCS', 'count'
        if app == 'percent' and a['percent']['ref_kg']:
            pc = a['percent']
            if pc['view'] == 'reference':
                return self.display_value(pc['ref_kg'])[0], self.unit, 'reference'
            if pc['view'] == 'weight':
                return self.display_value(self._net())[0], self.unit, 'weight'
            return f'{self._percent():.2f}' if self.px else f'{self._percent():.1f}', '%', 'percent'
        if app == 'dynamic' and a['dynamic']['phase'] == 'held':
            return self.display_value(a['dynamic']['result_kg'])[0], self.unit, 'held'
        if app == 'density' and a['density']['result'] is not None:
            return f'{a["density"]["result"]:.{a["density"]["result_digits"]}f}', 'g/cm3', 'density'
        if app == 'formulation' and a['formulation']['phase'] == 'total':
            f = a['formulation']
            vals = [sum(f['items']) + (f['filler_kg'] or 0.0)] + f['items'] + ([f['filler_kg']] if f['filler_kg'] is not None else [])
            return self.display_value(vals[f['view']])[0], self.unit, 'total' if f['view'] == 0 else f'item{f["view"]}'
        if app == 'check' and a['check'].get('view') == 'limits':
            return self.display_value(a['check']['low_kg'] or 0.0)[0], self.unit, 'limits'
        if app == 'total' and a['total']['flash_until'] is not None and self.t < a['total']['flash_until']:
            return self.display_value(a['total']['sum_kg'])[0], self.unit, 'total'
        if app == 'hold' and a['hold']['phase'] == 'held':
            return self.display_value(a['hold']['value_kg'])[0], self.unit, 'held'
        return self.display_value(self._net())[0], self.unit, 'weight'

    def read(self, visibility='agent'):
        b = self.b
        on = b.powered and self._supply()
        st = self._status()
        val, utext, kind = self._main_value() if on else (None, None, 'off')
        g = self._gross()
        observable = st in ('stable', 'unstable')
        a = self.appstate
        cp = self.calproc
        out = {
            'clock.time_s': self.t, 'clock.datetime': self._clock().isoformat(timespec='seconds'), 'power.adapter': self.adapter, 'power.on': on,
            'status': st, 'weighing.stable': bool(on and b.stable and not self.fault), 'weighing.net_kg': self._net() if on and observable else None,
            'weighing.gross_kg': (g - self.zero_kg) if on and observable else None, 'weighing.tare_kg': self.tare_kg, 'weighing.unit': self.unit,
            'weighing.units_enabled': self._units_enabled(), 'weighing.capacity_fraction': max(0.0, min(1.0, (g - self.zero_kg) / b.capacity_kg)) if on else 0.0,
            'weighing.at_zero': bool(on and observable and abs(self._net()) < 0.5 * self._d_kg() and self.tare_kg == 0.0),
            'weighing.preset_tare': False, 'display.main': val, 'display.unit': utext, 'display.kind': kind,
            'display.negative': bool(val is not None and val.startswith('-')), 'display.message': self.message if on else None,
            'calibration.state': dict(running=cp is not None, kind=cp['kind'] if cp else None, step=cp['step'] if cp else None,
                                      expected_g=cp['points_g'][cp['step']] if cp and cp['step'] < len(cp['points_g']) else None,
                                      points_g=list(cp['points_g']) if cp else None, result=cp['result'] if cp else None,
                                      calibrated=b.calibrated, last=copy.deepcopy(self.cal['last'])),
            'settings': copy.deepcopy(self.s), 'security.locked': b.security_locked, 'menu.locks': {m: self._locked(m) for m in
                                                                                                  ('calibration', 'setup', 'units', 'rs232', 'print', 'glp', 'factory')} if self.px else {},
            'password.entered': self.password_ok, 'app.active': self.app, 'app.enabled': [x for x in self.p['apps'] if self._app_enabled(x)],
            'app.counting': dict(apw_kg=a['counting']['apw_kg'], sample=a['counting']['sample'], view=a['counting']['view'], count=self._count() if on and observable else None),
            'app.percent': dict(ref_kg=a['percent']['ref_kg'], view=a['percent']['view'], percent=self._percent() if on and observable else None),
            'interface.device': self.interface, 'interface.rs232_kit': self.rs232_kit if not self.px else True, 'interface.lines': len(self.output),
            'pan.installed': b.pan_installed, 'transport.unlocked': b.transport_unlocked, 'display.backlight': self._backlight(on),
            'power.battery': bool(on and not self.adapter),
            'level.bubble_centered': self._tilt_total() <= LEVEL_OK_RAD,
        }
        if self.px:
            out['fault.code'] = self.fault['code'] if on and self.fault else None
            out.update({'app.batch': dict(items_kg=list(a['batch']['items']), active=bool(self.s['batch_printing'])),
                        'app.dynamic': dict(time_s=a['dynamic']['time_s'], phase=a['dynamic']['phase'], result_kg=a['dynamic']['result_kg'],
                                            remaining_s=max(0.0, a['dynamic']['time_s'] - (self.t - a['dynamic']['start'])) if a['dynamic']['phase'] == 'averaging' else None),
                        'app.density': {k: a['density'][k] for k in a['density']}, 'app.formulation': copy.deepcopy(a['formulation']), 'doors.open_fraction': self.doors})
        else:
            out.update({'app.check': dict(low_kg=a['check']['low_kg'], high_kg=a['check']['high_kg'], state=self._check_state() if on and observable else None,
                                          view=a['check'].get('view', 'weight')),
                        'app.total': dict(sum_kg=a['total']['sum_kg'], n=a['total']['n'], flashing=bool(a['total']['flash_until'] and self.t < a['total']['flash_until'])),
                        'app.hold': dict(phase=a['hold']['phase'], value_kg=a['hold']['value_kg'], mode=self.s['hold'])})
        if visibility != 'agent':
            out['truth.process'] = self._cmd_truth_inspect()['process']
        return out

    def _backlight(self, on):
        if not on:
            return 0
        if self.px:
            dim = self.s['auto_dim'] != 'off' and self.t - max(self.last_motion_t, self.last_key_t) >= float(self.s['auto_dim']) * 60.0 - 1e-9
            return {'low': 40, 'medium': 70, 'high': 100}[self.s['brightness']] // (3 if dim else 1)
        bl = self.s['backlight']
        if self.adapter:
            return 100                                                    # p.12: with the power pack the backlight is always on
        if bl == 'auto':
            return 100 if self.t - max(self.last_motion_t, self.last_key_t) < 10.0 else 30   # assumption: lit for 10 s after a key or a weight change
        return 100 if bl == 'on' else 30


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


class OhausBalanceSession:
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
        h = self._host
        if (instance_id is None) == (asset_type_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:
            self._target(instance_id)
        if asset_type_id is not None and asset_type_id != h.p['asset_type_id']:
            raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_contract_metadata(h.model)
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=h.p['asset_type_id'], asset_version=ASSET_VERSIONS[h.model],
                    capabilities={k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'},
                    state_slots={k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'})

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = _jsonable(h.read(self._role))
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        slots = SLOTS[h.model]
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=slots[k]['unit'], quality='unavailable' if values[k] is None else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h = self._host
        caps = ALL_CAPS[h.model]
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
                if digest is None or old != digest:
                    raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            if not isinstance(capability_id, str) or capability_id not in caps:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and caps[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(caps[capability_id]['input_schema'], arguments)
            h._caller = self._principal
            snapshot = copy.deepcopy({k: v for k, v in h.__dict__.items() if k not in ('_principals', '_requests', '_caller')})
            try:
                result = _jsonable(h.invoke(capability_id, **arguments))
            except ActionRejected:
                h.__dict__.update(snapshot)
                raise
            finally:
                h._caller = None
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None, status='succeeded',
                            state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=copy.deepcopy(result), observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            return response
        except ActionRejected as exc:
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None, status='rejected',
                            state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=str(exc), message=ERROR_ZH.get(str(exc), str(exc)), retryable=False, side_effects='none'))
            if key is not None and digest is not None and key not in h._requests:
                h._requests[key] = (digest, copy.deepcopy(response))
            return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        raise ActionRejected('ACCESS_DENIED')


ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'ACCESS_DENIED': '无权访问（真值或评测者能力）', 'NO_POWER': '没有接电源适配器', 'POWER_OFF': '天平关机',
    'NOT_WEIGHING': '不在称量状态（开机零点超范围、超载、欠载或运输锁锁住）', 'UNSTABLE': '读数不稳定', 'ZERO_RANGE': '超出置零范围（容量的±2 %）',
    'TARE_RANGE': '皮重超出范围（负值；SPX Err 8.5）', 'SECURITY_LOCKED': '安全开关锁定', 'UNIT_DISABLED': '该单位在单位菜单中未打开', 'NO_INTERFACE': '没有接口（SPX须装RS232套件）',
    'MENU_LOCKED': '该子菜单被锁定（Lockout或未输入密码）', 'MENU_HIDDEN': '批准模式下锁定菜单被隐藏', 'SECURITY_SWITCH': '批准模式开须安全开关锁定、关须解锁',
    'APPROVED_MODE': '批准模式（法制计量）下该设定锁定或不可用', 'NO_MODE': '至少要有一个应用模式', 'NO_CALIBRATION': '没有进行中的校准',
    'MODE_DISABLED': '该应用模式在MODE菜单中未打开', 'NO_FUNCTION': '当前应用没有该动作', 'OUT_OF_RANGE': '超出数量上限', 'NOT_POSITIVE': '只累计正值',
    'DISPLAY_OVERFLOW': '显示值超过999999（Err 8.6）', 'PAN_NOT_EMPTY': '秤盘不空，不能复位保持值', 'LOW_REFERENCE': '取样或参考重量太小',
    'BATCH_FULL': '批量称量已满10个', 'AUTO_PRINT': '自动打印开启时Print键不发送', 'PAN_LOCKED': '取盘须关机、空载（SPX先转开卡口）', 'PAN_INSTALLED': '运输锁在秤盘下，先取下秤盘',
    'UNKNOWN_OPERATION': '本运行层的能力都是同步的',
    'SYSTEM_ERROR': '系统错误（Error 2.0/9.5/5.3、IDNR error、Service，手册p.46）：按键无反应、不称量；2.0/9.5/5.3断电重启，仍在或IDNR/Service须联系维修',
}

# ---------------------------------------------------------------- state slots (per model)
SLOTS = {}


def _slots(model):
    px = model == 'PX224E'
    out = {}

    def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
        out[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)
    kg = nullable(number('kg'))
    slot('clock.time_s', number('s'), 's', 'derived', 'housing', '仿真时钟（宿主时钟）')
    slot('clock.datetime', text(25), '1', 'derived', 'housing', '天平日期时间（ISO）')
    slot('power.adapter', BOOL, '1', 'scene', 'rear_ports', '电源适配器是否接上')
    slot('power.on', BOOL, '1', 'setpoint', 'button_zero', '天平是否开机')
    slot('power.battery', BOOL, '1', 'derived', 'display', '电池供电（电池符号）')
    slot('status', enum(['off', 'init_over', 'init_under', 'overload', 'underload', 'locked', 'stable', 'unstable'] + (['fault'] if px else [])), '1', 'derived',
         'display', '称量状态（开机零点超/欠范围、超载、欠载、运输锁、稳定、不稳定' + ('、系统错误' if px else '') + '）')
    slot('weighing.stable', BOOL, '1', 'sensor', 'display', '稳定符号*')
    slot('weighing.net_kg', kg, 'kg', 'sensor', 'display', '净重（校准修正后，未取整）')
    slot('weighing.gross_kg', kg, 'kg', 'sensor', 'display', '毛重（零点后）')
    slot('weighing.tare_kg', number('kg'), 'kg', 'setpoint', 'button_tare', '皮重')
    slot('weighing.unit', text(8), '1', 'setpoint', 'button_print', '当前显示单位')
    slot('weighing.units_enabled', {'type': 'array', 'items': text(8), 'minItems': 0, 'maxItems': 32}, '1', 'setpoint', 'button_print', '单位菜单中打开的单位')
    slot('weighing.capacity_fraction', number('1', 0.0, 1.0), '1', 'derived', 'display', '容量条比例（毛重/容量）')
    slot('weighing.at_zero', BOOL, '1', 'derived', 'display', '零点中心符号>0<')
    slot('weighing.preset_tare', BOOL, '1', 'derived', 'display', '预置皮重PT（只能经接口命令设，本模型不设）')
    slot('display.main', nullable(text(16)), '1', 'derived', 'display', '主显示的数值文字（按单位分辨率取整）')
    slot('display.unit', nullable(text(8)), '1', 'derived', 'display', '主显示的单位文字（PCS、%、g/cm3或称量单位）')
    slot('display.kind', text(16), '1', 'derived', 'display', '主显示内容（weight、count、apw、percent、reference、held、density、total…）')
    slot('display.negative', BOOL, '1', 'derived', 'display', '负号')
    slot('display.message', nullable(text(40)), '1', 'derived', 'display', '最近的仪器信息')
    slot('display.backlight', number('%', 0, 100), '%', 'derived', 'display', '背光亮度（%）')
    slot('calibration.state', FREE, '1', 'derived', 'display', '校准：是否进行中、类型、步骤、当前要放的砝码(g)、结果、是否已校准、上次校准')
    slot('settings', FREE, '1', 'setpoint', 'button_tare', '全部用户菜单设定')
    slot('security.locked', BOOL, '1', 'scene', 'security_switch', '安全开关是否锁定')
    slot('menu.locks', FREE, '1', 'derived', 'button_tare', '各子菜单是否被锁（PX：Lockout或密码）')
    slot('password.entered', BOOL, '1', 'derived', 'button_tare', '本次开机是否已输入正确密码')
    slot('app.active', text(16), '1', 'setpoint', 'button_function', '当前应用')
    slot('app.enabled', {'type': 'array', 'items': text(16), 'minItems': 0, 'maxItems': 8}, '1', 'setpoint', 'button_function', '可用应用')
    slot('app.counting', FREE, '1', 'derived', 'button_function', '计数：APW(kg)、取样数、显示内容、件数')
    slot('app.percent', FREE, '1', 'derived', 'button_function', '百分比：参考重量(kg)、显示内容、百分比')
    slot('interface.device', enum(['none', 'pc', 'printer']), '1', 'scene', 'rear_ports', '接口连接的设备')
    slot('interface.rs232_kit', BOOL, '1', 'scene', 'rear_ports', '是否有RS232接口（PX内置；SPX须装套件）')
    slot('interface.lines', integer(0, 10 ** 6), '1', 'derived', 'rear_ports', '接口已输出行数（events.read读取）')
    slot('pan.installed', BOOL, '1', 'scene', 'pan', '秤盘是否装上')
    slot('level.bubble_centered', BOOL, '1', 'scene', 'level_indicator', '水平泡是否居中（倾斜≤0.2°）')
    slot('transport.unlocked', BOOL, '1', 'scene', 'transport_lock' if not px else 'pan', '运输锁是否解开（PX没有用户运输锁，恒为真）')
    if px:
        slot('fault.code', nullable(enum(FAULT_CODES)), '1', 'derived', 'display', '显示的系统错误（Error 2.0/9.5/5.3、IDNR error、Service；无为null）')
        slot('app.batch', FREE, '1', 'derived', 'button_function', '批量称量：已存重量(kg)、是否开启')
        slot('app.dynamic', FREE, '1', 'derived', 'button_function', '动态称量：平均时间、阶段（ready/averaging/held）、结果(kg)、剩余时间')
        slot('app.density', FREE, '1', 'derived', 'button_function', '密度：参数、空气中与液体中重量(kg)、结果(g/cm3)')
        slot('app.formulation', FREE, '1', 'derived', 'button_function', '配方：成分重量(kg)、阶段、填料、显示序号')
        slot('doors.open_fraction', number('1', 0.0, 1.0), '1', 'scene', 'door_left', '防风罩门开度')
    else:
        slot('app.check', FREE, '1', 'derived', 'button_function', '检重：上下限(kg)、under/accept/over、显示内容')
        slot('app.total', FREE, '1', 'derived', 'button_function', '累计：总量(kg)、次数、∑闪烁')
        slot('app.hold', FREE, '1', 'derived', 'button_function', '保持：阶段（idle/ready/held）、保持值(kg)、方式')
    slot('truth.process', FREE, '1', 'process_truth', 'pan', '秤盘真实载荷、原始读数、传感器误差、校准修正', 'evaluator')
    return out


for _m in PROFILES:
    SLOTS[_m] = _slots(_m)


# ---------------------------------------------------------------- contract metadata (assembled by outputs/panel_ui/ohaus_<model>/build_contract.py)
_ON = ['NO_POWER', 'POWER_OFF']
_W = _ON + ['NOT_WEIGHING', 'UNSTABLE']


def cap_errors(model):
    px = model == 'PX224E'
    menu = ['MENU_LOCKED'] if px else []
    e = {'plant.configure': [], 'power.connect_adapter': [], 'power.set': ['NO_POWER'], 'pan.set_load': [], 'pan.remove': ['PAN_LOCKED'], 'pan.install': [],
         'transport_lock.set': ['PAN_INSTALLED'], 'weighing.next_unit': _ON + ['SECURITY_LOCKED'], 'app.next': _ON, 'power.set_batteries': [], 'doors.set': [], 'security.set_switch': [], 'environment.set': [], 'level.turn_foot': ['FOOT_LIMIT'],
         'weighing.zero': _W + ['ZERO_RANGE'], 'weighing.tare': _W + ['TARE_RANGE'], 'weighing.set_unit': _ON + ['UNIT_DISABLED'] + ([] if px else ['SECURITY_LOCKED']),
         'print.send': _W + ([] if px else ['AUTO_PRINT']), 'interface.connect': [] if px else ['NO_INTERFACE'],
         'settings.configure': _ON + menu + (['MENU_HIDDEN', 'SECURITY_LOCKED', 'SECURITY_SWITCH', 'APPROVED_MODE'] if px else ['SECURITY_LOCKED', 'NO_INTERFACE', 'NO_MODE']),
         'settings.reset': _ON + (menu if px else ['NO_INTERFACE', 'SECURITY_LOCKED']), 'settings.enter_password': _ON, 'clock.set': _ON + menu,
         'glp.set_header': _ON + menu, 'calibration.start': _ON + ['SECURITY_LOCKED'] + menu + (['APPROVED_MODE'] if px else []),
         'calibration.select_point': _ON + ['NO_CALIBRATION'], 'calibration.abort': _ON + ['NO_CALIBRATION'], 'sensor.configure_error': [],
         'app.select': _ON + menu + ([] if px else ['MODE_DISABLED']),
         'app.function': _W + ['NO_FUNCTION', 'LOW_REFERENCE'] + (['BATCH_FULL', 'OUT_OF_RANGE'] if px else ['NOT_POSITIVE', 'DISPLAY_OVERFLOW', 'PAN_NOT_EMPTY']),
         'counting.clear_apw': _ON, 'counting.set_sample_size': _ON, 'counting.establish': _W + ['LOW_REFERENCE'], 'percent.clear_reference': _ON,
         'percent.establish': _W + ['LOW_REFERENCE'], 'batch.store': _W + ['BATCH_FULL'], 'batch.finish': _ON, 'dynamic.set_time': _ON, 'density.configure': _ON,
         'formulation.finish': _ON + ['NO_FUNCTION'], 'formulation.skip_filler': _ON + ['NO_FUNCTION'], 'check.set_limits': _ON, 'check.clear': _ON,
         'totalize.clear': _ON, 'events.read': [], 'truth.inspect': [], 'sim.step': [], 'fault.inject': [], 'fault.clear': []}
    if px:                                                                # a system error refuses everything the keys do (p.46)
        e = {k: v + ['SYSTEM_ERROR'] if 'POWER_OFF' in v or k == 'power.set' else v for k, v in e.items()}
    return {k: v for k, v in e.items() if k in CAPS[model]}


_DISP = ['display.main', 'display.unit', 'display.kind', 'display.negative', 'display.message']
EFFECTS = {
    'plant.configure': ['interface.rs232_kit', 'truth.process'], 'power.connect_adapter': ['power.adapter', 'power.on', 'status'] + _DISP,
    'power.set': ['power.on', 'status', 'weighing.net_kg', 'weighing.tare_kg', 'password.entered', 'fault.code'] + _DISP, 'pan.set_load': ['truth.process'],
    'pan.remove': ['pan.installed', 'status'], 'pan.install': ['pan.installed', 'status'], 'transport_lock.set': ['transport.unlocked', 'status'],
    'doors.set': ['doors.open_fraction', 'weighing.stable'], 'security.set_switch': ['security.locked', 'menu.locks'], 'environment.set': ['weighing.stable', 'truth.process'],
    'level.turn_foot': ['level.bubble_centered', 'weighing.stable'], 'weighing.next_unit': ['weighing.unit'] + _DISP, 'app.next': ['app.active'] + _DISP,
    'power.set_batteries': ['power.on', 'power.battery'], 'weighing.zero': ['weighing.net_kg', 'weighing.gross_kg', 'weighing.tare_kg', 'weighing.at_zero'] + _DISP,
    'weighing.tare': ['weighing.net_kg', 'weighing.tare_kg'] + _DISP, 'weighing.set_unit': ['weighing.unit'] + _DISP, 'print.send': ['interface.lines'],
    'interface.connect': ['interface.device'], 'settings.configure': ['settings', 'weighing.units_enabled', 'weighing.unit', 'menu.locks', 'app.enabled', 'display.backlight'] + _DISP,
    'settings.reset': ['settings', 'weighing.units_enabled', 'weighing.unit', 'app.enabled'] + _DISP, 'settings.enter_password': ['password.entered', 'menu.locks'],
    'clock.set': ['clock.datetime'], 'glp.set_header': ['settings'], 'calibration.start': ['calibration.state'] + _DISP,
    'calibration.select_point': ['calibration.state'] + _DISP, 'calibration.abort': ['calibration.state'] + _DISP, 'sensor.configure_error': ['truth.process'],
    'app.select': ['app.active', 'display.kind'] + _DISP, 'app.function': ['app.counting', 'app.percent', 'app.batch', 'app.density', 'app.formulation', 'app.check', 'app.total',
                                                                            'app.hold'] + _DISP,
    'counting.clear_apw': ['app.counting'] + _DISP, 'counting.set_sample_size': ['app.counting'], 'counting.establish': ['app.counting'] + _DISP,
    'percent.clear_reference': ['app.percent'] + _DISP, 'percent.establish': ['app.percent'] + _DISP, 'batch.store': ['app.batch'], 'batch.finish': ['app.batch', 'interface.lines'],
    'dynamic.set_time': ['app.dynamic'], 'density.configure': ['app.density'], 'formulation.finish': ['app.formulation'], 'formulation.skip_filler': ['app.formulation'] + _DISP,
    'check.set_limits': ['app.check'], 'check.clear': ['app.check'], 'totalize.clear': ['app.total'], 'events.read': [], 'truth.inspect': [], 'sim.step': ['clock.time_s'],
    'fault.inject': ['status', 'fault.code', 'calibration.state', 'weighing.net_kg', 'weighing.gross_kg'] + _DISP,
    'fault.clear': ['status', 'fault.code', 'weighing.net_kg', 'weighing.gross_kg'] + _DISP,
}
_EVOLVE = ['weighing.stable', 'weighing.net_kg', 'weighing.gross_kg', 'weighing.at_zero', 'weighing.capacity_fraction', 'weighing.tare_kg', 'status', 'calibration.state',
           'app.counting', 'app.dynamic', 'app.hold', 'interface.lines', 'power.on', 'display.backlight', 'truth.process'] + _DISP
SCENE_SLOTS = {'power.on', 'display.main', 'display.unit', 'display.message', 'pan.installed', 'transport.unlocked', 'security.locked', 'level.bubble_centered',
               'doors.open_fraction'}
PRECONDITIONS = {
    'power.set': '须接电源适配器', 'pan.remove': '须关机且空载（SPX先顺时针转20°）', 'transport_lock.set': '须先取下秤盘',
    'weighing.zero': '开机、称量状态、读数稳定、在置零范围（±2 %）内', 'weighing.tare': '开机、称量状态、读数稳定、皮重不为负',
    'weighing.set_unit': '开机；单位已在单位菜单打开', 'print.send': '开机、称量状态；Stable Only时须稳定',
    'settings.configure': '开机；子菜单未锁定（PX：Lockout、密码、批准模式+安全开关）；批准模式开须安全开关锁定、关须解锁；SPX的PRINT/RS232须装套件',
    'calibration.start': '开机；安全开关解锁；校准未被锁定', 'counting.establish': '读数稳定；取样重量足够', 'percent.establish': '读数稳定；参考重量足够',
    'app.function': '开机；按当前应用要求稳定读数等', 'panel.press': '面板按当前屏幕与手册规则处理按键：关机时只有On/Zero起作用；被调用能力的前提同样适用（被拒时显示仪器信息，手势不被接受）',
}
LIMITATIONS_COMMON = [
    '过程层为scripts/balance_runtime.py的称量抽象（秤盘法向力→质量、稳定判定、门/倾斜/振动门控、秤盘与运输锁互锁、单点力校准），不修改（XPR204E资产固定其哈希）；本运行层以子类FirmwareBalance只把稳定判定带宽改为Stable Range设定。',
    '秤盘载荷由宿主（Matterix）汇总接触力后给出（pan.set_load）；运行层不解接触、不模拟气流噪声；读数理想（无噪声），传感器零点、增益与非线性误差由评测者设定，默认0；真实测量误差与公差留给后续“校准/仿真与现实差距”任务。',
    '校准：跨度校准存储力增益（旧模型g_cal），线性校准另存分段线性修正；天平在读数稳定时自己采点；砝码偏离标称10 %以上为失败、60 s不稳定为超时（假设）。',
    '滤波Low/Medium/High按稳定时间×0.5/1/2（假设）；自动零跟踪、自动去皮、开机零点（±10 %，假设）、置零（±2 %，旧模型假设）按手册规则实现。',
    '单位换算用标准换算系数，显示分辨率取≥1 d换算值的1-2-5步长（与SPX规格表一致）；自定义单位按Factor×10^Exponent，分辨率为LSD×1 d换算值所在十进位（假设）。',
    '打印：按打印设定组成文本行写入接口输出记录（events.read）；串口波特率等只作为设定（没有对端设备的协议仿真）；远程接口命令未实现。',
    '面板层（panel.*）：按panel_ui.json（手册页码证据）实现；手册未写之处（菜单中的数值键、提示文字、退出方式）的选择见panel_ui.json的assumptions；手势不推进仿真时钟。',
    '场景：URDF/GLB有本体、四键、安全开关、调平脚、秤盘等；显示屏没有独立网格（几何缺失，属于控制面板网格）；屏幕纹理与按键关节触发属于后续Matterix步骤。',
]


def export_contract_metadata(model):
    caps_src = ALL_CAPS[model]
    slots_src = SLOTS[model]
    errs = cap_errors(model)
    slots, caps = {}, {}
    for k, v in slots_src.items():
        slots[k] = dict(component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'], reference_conditions=None, meaning_zh=v['meaning'],
                        origin=v['origin'], access='read_only', visibility=v['visibility'],
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'OhausBalanceSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description='宿主时钟推进时更新' if v['origin'] in ('sensor', 'derived', 'process_truth')
                                      else '命令被接受时改变'), source_ids=['runtime_source'])
    for k, v in caps_src.items():
        e = list(dict.fromkeys(errs.get(k, []) + ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'] + (['ACCESS_DENIED'] if v['visibility'] != 'agent' else [])))
        if k in ('panel.press', 'panel.turn', 'panel.switch'):
            fx = list(dict.fromkeys(s for c in _panel_ui.spec_capabilities(PANEL_SPEC[model]) for s in EFFECTS.get(c, []) if s in slots_src))
        else:
            fx = [s for s in EFFECTS.get(k, []) if s in slots_src]
        on_step = [s for s in _EVOLVE if s in slots_src] if k == 'sim.step' else []
        seen = sorted((set(fx) | set(on_step)) & SCENE_SLOTS)
        caps[k] = dict(
            function_zh=v['function_zh'], kind=v['kind'], visibility=v['visibility'], component_ids=list(v['component_ids']), availability='implemented',
            input_schema=copy.deepcopy(v['input_schema']), output_schema=copy.deepcopy(v['output_schema']),
            implementation={layer: 'absent' if layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ('process', 'scene', 'robot', 'hardware', 'host')},
            binding=dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.ohaus_balance.OhausBalanceSession.invoke',
                         invocation=f'OhausBalanceRuntime({model!r}).session(...).invoke(instance_id=..., capability_id={k!r}, arguments={v["example_arguments"]!r}, request_id="unique")'),
            preconditions=[dict(description_zh=PRECONDITIONS.get(k, '开机' if 'POWER_OFF' in e else '无') + '；被拒时状态不变。',
                                state_ids=['power.on'] if 'POWER_OFF' in e else [], enforced_by='runtime', check='OhausBalanceRuntime.invoke')],
            state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in fx] +
                          [dict(state_id=s, timing='on_step', description_zh='随宿主时钟演化（稳定判定、自动功能、应用、自动打印、自动关机）') for s in on_step if s not in fx],
            observes=[], model_effects=(dict(mode='host_required', binding_ids=[], description_zh='状态需宿主桥镜像到场景（' + '、'.join(seen) + '），运行层本身不驱动模型')
                                        if seen else dict(mode='none', binding_ids=[], description_zh='设定或过程变化，没有对应的场景关节或材质')),
            execution=dict(mode='synchronous', time_domain='simulation',
                           completion_criterion=('手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if k.startswith('panel.') and k not in
                                                 ('panel.capture', 'panel.frame') else '原子设定、配置或查询（读数与应用随之后的sim.step演化）'),
                           completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id',
                           resources=['host_clock'] + (['robot_or_operator'] if v['kind'] == 'robot_skill' else []),
                           partial_failure_zh='同步原子操作：成功时一次写入全部效果，被拒时会话恢复调用前状态'),
            errors=[dict(code=x, meaning_zh=ERROR_ZH.get(x, x), side_effects='none') for x in e], example_arguments=copy.deepcopy(v['example_arguments']),
            verification=[], limitations=(['面板层按panel_ui.json（手册页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟。'] if k.startswith('panel.')
                                          else ['过程层实现；机器人、场景与硬件层未实现。']),
            source_ids=['runtime_source', 'operating_manual'] + (['panel_spec'] if k.startswith('panel.') else []))
    p = PROFILES[model]
    mech = dict(component_ids=['pan', 'housing', 'display'], state_ids=list(slots), implementation='lumped',
                meaning_zh='称量：秤盘法向力经力校准与线性修正得到质量，稳定判定后按零点、皮重、单位与分辨率显示；固件实现应用、设定、打印与自动功能',
                equations=['m_raw = F / g_cal;  F = m g_local (1 + gain) + offset g_local + 4 nl Cap g_local x (1 - x), x = m / Cap',
                           'm = piecewise_linear(m_raw; linearity points);  net = m - zero - tare;  shown = round(net / u, step_u)',
                           'stable: |m - m_prev| <= Stable Range x d for t >= settling x filter factor, doors closed, tilt <= 0.2 deg, no vibration'],
                parameters={'capacity_kg': dict(value=p['capacity_g'] / 1000.0, unit='kg', meaning_zh='最大称量', evidence='documented', source_ids=['operating_manual'], uncertainty=None),
                            'readability_kg': dict(value=p['d_g'] / 1000.0, unit='kg', meaning_zh='可读性d', evidence='documented', source_ids=['operating_manual'], uncertainty=None),
                            'settling_s': dict(value=p['settling_s'], unit='s', meaning_zh='典型稳定时间（Filter Medium）', evidence='documented', source_ids=['operating_manual'],
                                               uncertainty='典型值'),
                            'span_points_kg': dict(value=[x / 1000.0 for x in p['span_points_g']], unit='kg', meaning_zh='跨度校准点', evidence='documented',
                                                   source_ids=['operating_manual'], uncertainty=None),
                            'linearity_points_kg': dict(value=[0.0] + [x / 1000.0 for x in p['lin_points_g']], unit='kg', meaning_zh='线性校准点', evidence='documented',
                                                        source_ids=['operating_manual'], uncertainty=None)},
                source_ids=['runtime_source', 'operating_manual'], limitations=list(LIMITATIONS_COMMON))
    return dict(asset_type_id=p['asset_type_id'], capabilities=caps, state_slots=slots, mechanisms={'weighing_firmware': mech})


LINK_ZH = {
    'housing': ('机身', '承载称量单元、电子与显示', '外壳与底座'), 'control_panel': ('控制面板', '显示屏与四个按键所在的前面板', '面板网格（含显示窗口）'),
    'level_indicator': ('水平泡', '显示天平是否水平（居中=倾斜≤0.2°）', '气泡水平仪'), 'button_zero': ('On/Zero Off/Yes键', '开机/置零；长按关机；菜单中为Yes', '按键'),
    'button_print': ('Print Unit/No键', '打印；长按单位；菜单中为No', '按键'), 'button_function': ('Function Mode/Back键', '应用动作；长按应用模式；菜单中为Back', '按键'),
    'button_tare': ('Tare Menu/Exit键', '去皮；长按菜单；菜单中为Exit', '按键'), 'chamber_floor': ('称量室底板', '防风罩内底板', '结构件'),
    'pan_support': ('秤盘支架', '把秤盘的载荷传到称重传感器', '结构件'), 'pan': ('秤盘', '放置样品与容器；法向力为称量输入', '不锈钢秤盘'),
    'draftshield_frame': ('防风罩框架', '承载玻璃门', '结构件'), 'front_glass': ('前玻璃', '防风罩前壁', '玻璃'), 'rear_glass': ('后玻璃', '防风罩后壁', '玻璃'),
    'door_left': ('左门', '防风罩左侧推拉门（开时读数不稳定）', '玻璃门'), 'door_right': ('右门', '防风罩右侧推拉门', '玻璃门'), 'door_top': ('顶门', '防风罩顶部推拉门', '玻璃门'),
    'antistatic_bar': ('防静电条', '减少静电（被动件）', '结构件'), 'rear_ports': ('后部接口', '电源适配器、RS232与USB接口（SPX须装接口套件）', '接口'),
    'security_switch': ('安全开关', '锁定后菜单受限（PX批准模式需要；SPX隐藏校准、锁定单位）', '滑动开关'), 'weigh_below': ('下挂称量孔', '下挂钩称量', '结构件'),
    'security_anchor': ('防盗锁孔', '安全锁缆（被动件）', '结构件'), 'transport_lock': ('运输锁', '秤盘下的红色运输锁（逆时针90°解锁）', '旋转锁'),
    'battery_cover': ('电池盖', '4节AA电池仓盖', '结构件'), 'battery_1': ('电池1', 'AA电池', '电池'), 'battery_2': ('电池2', 'AA电池', '电池'), 'battery_3': ('电池3', 'AA电池', '电池'),
    'battery_4': ('电池4', 'AA电池', '电池'), 'foot_front_left': ('左前调平脚', '旋转调节高度使水平泡居中（1 mm/圈）', '螺纹脚'),
    'foot_front_right': ('右前调平脚', '旋转调节高度', '螺纹脚'), 'foot_rear_left': ('左后调平脚', '旋转调节高度', '螺纹脚'), 'foot_rear_right': ('右后调平脚', '旋转调节高度', '螺纹脚'),
}
