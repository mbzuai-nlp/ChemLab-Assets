# BEGIN CHEM-180 ALICAT MC APPEND-ONLY RUNTIME
"""CHEM-180 simulator adapter; no hardware, robot, or real valve driver.

Only the trusted AlicatMCRuntime owns identity, gas compatibility evidence,
boundary conditions, and the common clock. Give an agent AlicatMCSession,
not the host. API filtering is not a Python/shared-filesystem sandbox.
"""
import copy as _amc_copy
import hashlib as _amc_hashlib
import json as _amc_json
import math as _amc_math
import tempfile as _amc_tempfile
import threading as _amc_threading
from pathlib import Path as _amc_Path

ALICAT_MC_TYPE = "instrument.alicat_mc_1slpm_rev6"
ALICAT_MC_FS_M3_S = 1e-3 / 60
ALICAT_MC_MIN_M3_S = ALICAT_MC_FS_M3_S * 0.0001
ALICAT_MC_PSI_PA = 6894.757293168
ALICAT_MC_R = 8.31446261815324
# 2026-10 template hook: NIST gas-property library (scripts/basic40_templates/alicat_mc.py); host-registered gases keep the legacy path.
_AMC_TEMPLATE_CAPABILITIES = ("template.list", "template.read", "flow.read_template")

# Front panel (panel.press / panel.capture / panel.frame): the backlit monochrome LCD with six softkeys and the logo backlight key of the
# operating manual (MC series, firmware 9v, Rev.4; panel_ui.json beside the contract), run by the generic engine
# scripts/asset_runtime/_panel_ui.py on top of the capabilities below (no second copy of the logic).  Device functions behind the panel
# (all settable through the API as well, menu.configure and the commands below): setpoint setup (autotare and its delay, setpoint source
# serial / analog with the 0-5 V input, power-up setpoint, setpoint limits), control loop (mass or volumetric flow; PD/PDF or PD2I gains and the
# pressure-control settings are kept), setpoint ramping (delta / time, up / down, power-up and zero-setpoint options), engineering units,
# flow / pressure averaging, zero band, serial settings (kept; no serial port is simulated), display settings (actions menu, valve drive line,
# contrast, power-up backlight, rotation), the totalizer timer and batch dispensing, COMPOSER gas mixes, the Recent gas list, the front-panel
# lock (LCK) and Factory Restore.
ALICAT_MC_ASSET_DIR = _amc_Path(__file__).resolve().parents[1] / "assets/basic40_batch02/t5_automation/alicat_mc"
ALICAT_MC_FS_PRESSURE_PA = 160 * ALICAT_MC_PSI_PA      # ABOUT > Full Scale Ranges: absolute pressure (catalog working range 11.5-160 PSIA)
ALICAT_MC_TIMER_MAX_S = 9999 * 3600 + 59 * 60 + 59     # totalizer elapsed-time counter freezes at 9999:59:59 (manual p.11)
ALICAT_MC_ANALOG_FS_V = 5.0                             # 0-5 Vdc analog setpoint input, Mini-DIN pin 4 (manual p.9)
ALICAT_MC_MIX_NUMBERS = tuple(range(255, 235, -1))      # COMPOSER mixes 236-255, numbers given from 255 down (manual p.25); 20 mixes (p.18)
ALICAT_MC_RECENT = 5                                    # length of the Gas Select 'Recent' list (not stated: assumption)
ALICAT_MC_BATCH_MAX_M3 = 1000.0                         # batch size entry bound (7 totalizer digits; simulation bound, not a vendor limit)
ALICAT_MC_DP_FS_PA = 1.5 * ALICAT_MC_PSI_PA              # 1 SLPM: pressure drop at full scale venting air to atmosphere (datasheet); linear valve assumed
ALICAT_MC_FACTORY_GAINS = (167, 1280)                   # factory P / D gains (manual p.14 picture): the host tau is the closed-loop response at them
ALICAT_MC_FASTEST_TAU_S = 0.03                          # fastest T63 30 ms (datasheet): lower bound of the gain-scaled closed-loop time constant
_AMC_UNITS = {   # engineering units the front panel offers per parameter (manual p.29: the standard-volume and actual-volume subset)
    "units_mass_flow": ["SLPM", "SCCM", "SCCS", "SmL/s", "SmL/m", "SL/s", "SL/h", "SCFM", "SCFH", "%"],
    "units_volumetric_flow": ["LPM", "CCM", "CCS", "mL/s", "mL/m", "L/s", "L/h", "CFM", "CFH", "%"],
    "units_pressure": ["PSIA", "PaA", "hPaA", "kPaA", "MPaA", "mbarA", "barA", "atm", "torrA", "mmHgA", "inHgA"],
    "units_temperature": ["C", "F", "K", "R"],
    "units_mass_total": ["SL", "SmL", "Sm3"],
    "units_volumetric_total": ["L", "mL", "m3"],
    "units_time_of_total": ["h:m:s", "ms", "s", "m", "hour", "day"],
}


def _amc_enum(*values):
    return {"type": "string", "enum": list(values)}


def _amc_int(minimum, maximum):
    return {"type": "integer", "minimum": minimum, "maximum": maximum, "x-unit": "1"}


_AMC_SETTINGS = {   # front-panel device settings (menu.configure; read in device.settings): schema, factory value
    "autotare": ({"type": "boolean"}, True),
    "autotare_delay_s": (None, 2.0),
    "setpoint_source": (_amc_enum("serial", "analog"), "serial"),
    "modbus_timeout_action": (_amc_enum("zero", "hold"), "zero"),
    "power_up_setpoint_mode": (_amc_enum("last", "fixed"), "last"),
    "power_up_setpoint_m3_s": (None, 0.0),
    "setpoint_min_m3_s": (None, 0.0),
    "setpoint_max_m3_s": (None, ALICAT_MC_FS_M3_S),
    "control_variable": (_amc_enum("mass_flow", "volumetric_flow", "absolute_pressure", "valve_drive"), "mass_flow"),
    "loop_type": (_amc_enum("pd_pdf", "pd2i"), "pd_pdf"),
    "p_gain": (_amc_int(0, 65535), 167),
    "d_gain": (_amc_int(0, 65535), 1280),
    "i_gain": (_amc_int(0, 65535), 0),
    "flow_limit_type": (_amc_enum("mass_flow", "volumetric_flow"), "mass_flow"),
    "flow_limit_max_m3_s": (None, ALICAT_MC_FS_M3_S),
    "limiter_gain": (_amc_int(0, 65535), 500),
    "deadband_Pa": (None, 0.0),
    "deadband_action": (_amc_enum("hold", "close"), "hold"),
    "ramp_delta_m3_s": (None, 0.0),
    "ramp_time_s": (None, 1.0),
    "ramp_time_unit": (_amc_enum("ms", "s", "m", "hour"), "s"),
    "ramp_up": ({"type": "boolean"}, True),
    "ramp_down": ({"type": "boolean"}, False),
    "ramp_power_up": ({"type": "boolean"}, False),
    "ramp_zero_setpoint": ({"type": "boolean"}, False),
    "show_valve": (_amc_enum("none", "main_screen", "digital_data", "screen_and_digital"), "none"),
    **{key: (_amc_enum(*units), units[0]) for key, units in _AMC_UNITS.items()},
    "ref_temperature_unit": (_amc_enum("C", "F", "K", "R"), "C"),
    "ref_pressure_unit": (_amc_enum(*_AMC_UNITS["units_pressure"]), "atm"),
    "flow_averaging_ms": (_amc_int(0, 255), 0),
    "pressure_averaging_ms": (_amc_int(0, 255), 0),
    "zero_band_pct": (None, 0.0),
    "unit_id": (_amc_enum(*"ABCDEFGHIJKLMNOPQRSTUVWXYZ@"), "A"),
    "modbus_address": (_amc_int(1, 247), 1),
    "modbus_idle_s": ({"anyOf": [{"type": "number", "minimum": 0, "maximum": 99999.9, "x-unit": "s"}, {"type": "null"}]}, None),
    "baud": ({"type": "integer", "enum": [2400, 4800, 9600, 19200, 38400, 57600, 115200], "x-unit": "1/s"}, 19200),
    "any_key_action": (_amc_enum("highlight", "actions"), "highlight"),
    "contrast": (_amc_int(-10, 10), 0),
    "power_up_backlight": ({"type": "boolean"}, True),
    "rotation": (_amc_enum("normal", "inverted"), "normal"),
}
_AMC_NUMBERS = {   # numeric settings: unit and bounds (front-panel entry ranges; manual pp.13-22 or assumption)
    "autotare_delay_s": ("s", 0, 99.9), "power_up_setpoint_m3_s": ("m^3/s", 0, ALICAT_MC_FS_M3_S), "setpoint_min_m3_s": ("m^3/s", 0, ALICAT_MC_FS_M3_S),
    "setpoint_max_m3_s": ("m^3/s", 0, ALICAT_MC_FS_M3_S), "flow_limit_max_m3_s": ("m^3/s", 0, ALICAT_MC_FS_M3_S), "deadband_Pa": ("Pa", 0, ALICAT_MC_FS_PRESSURE_PA),
    "ramp_delta_m3_s": ("m^3/s", 0, 100 * ALICAT_MC_FS_M3_S), "ramp_time_s": ("s", 0.001, 360000.0), "zero_band_pct": ("%", 0, 6.38),
}
for _key, (_unit, _lo, _hi) in _AMC_NUMBERS.items():
    _AMC_SETTINGS[_key] = ({"type": "number", "minimum": _lo, "maximum": _hi, "x-unit": _unit}, _AMC_SETTINGS[_key][1])
_AMC_SETTINGS_SCHEMA = {"type": "object", "properties": {k: v[0] for k, v in _AMC_SETTINGS.items()}, "required": list(_AMC_SETTINGS),
                        "additionalProperties": False}
_AMC_UNSUPPORTED_CONTROL = ("absolute_pressure",)   # pressure control: the host gives both pressures as boundaries (no process volume the valve fills)
_AMC_MIX_COMPONENT = {"type": "object", "properties": {"gas_id": {"type": "string", "minLength": 1},
                                                       "percent": {"type": "number", "minimum": 0.01, "maximum": 99.99, "x-unit": "%"}},
                      "required": ["gas_id", "percent"], "additionalProperties": False}
_AMC_MIX = {"type": "object", "properties": {
    "mix_number": {"type": "integer", "minimum": 236, "maximum": 255, "x-unit": "1"},
    "short_name": {"type": "string", "minLength": 1, "maxLength": 6, "pattern": "^[A-Z0-9 .,-]+$"},
    "long_name": {"type": "string", "maxLength": 20, "pattern": "^[A-Z0-9 .,-]*$"},
    "components": {"type": "array", "items": _AMC_MIX_COMPONENT, "minItems": 2, "maxItems": 5}},
    "required": ["mix_number", "short_name", "long_name", "components"], "additionalProperties": False}


def _amc_valid(schema, value):
    """Value against the schema keywords the new front-panel commands use (types, enum, bounds, lengths, pattern, items, anyOf, objects)."""
    import re
    if "anyOf" in schema:
        return any(_amc_valid(s, value) for s in schema["anyOf"])
    t = schema.get("type")
    if t == "null":
        return value is None
    if t == "boolean" and type(value) is not bool:
        return False
    if t in ("number", "integer") and (not _amc_number(value) or (t == "integer" and type(value) is not int)):
        return False
    if t == "string" and not isinstance(value, str):
        return False
    if t == "array" and not isinstance(value, list):
        return False
    if t == "object" and not isinstance(value, dict):
        return False
    if "enum" in schema and (value not in schema["enum"] or type(value) is bool):
        return False
    if t in ("number", "integer") and (value < schema.get("minimum", -_amc_math.inf) or value > schema.get("maximum", _amc_math.inf)):
        return False
    if t == "string" and (not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10 ** 6)
                          or ("pattern" in schema and not re.match(schema["pattern"], value))):
        return False
    if t == "array" and (not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10 ** 6)
                         or not all(_amc_valid(schema.get("items", {}), v) for v in value)):
        return False
    if t == "object":
        props = schema.get("properties", {})
        if (schema.get("additionalProperties") is False and set(value) - set(props)) or set(schema.get("required", [])) - set(value):
            return False
        if len(value) < schema.get("minProperties", 0) or not all(_amc_valid(props[k], v) for k, v in value.items() if k in props):
            return False
    return True


_AMC_PANEL = {}


def _amc_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _AMC_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if "_chemsim_panel_ui" not in sys.modules:
                spec = importlib.util.spec_from_file_location("_chemsim_panel_ui", _amc_Path(__file__).resolve().parent / "asset_runtime/_panel_ui.py")
                sys.modules["_chemsim_panel_ui"] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules["_chemsim_panel_ui"])
            engine = sys.modules["_chemsim_panel_ui"]
        caps = set(export_alicat_mc_contract_metadata(panel=False)["asset_types"][ALICAT_MC_TYPE]["capabilities"])
        spec = engine.load_spec(ALICAT_MC_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _AMC_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _AMC_PANEL


def _amc_templates():
    try:
        from basic40_templates import alicat_mc as module
    except ImportError:
        from scripts.basic40_templates import alicat_mc as module
    return module


def _amc_template_backed(host, s, inst):
    return inst is not None and ((s["gas.selected_id"] is not None and s["gas.selected_id"] not in host._gases) or inst.get("template_line_gas_id") is not None)


def _amc_number(value):
    try:
        return type(value) in (int, float) and _amc_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _amc_finite_result(value):
    return value if _amc_number(value) else None


def _amc_num(unit, minimum=None, maximum=None, nullable=False):
    schema = {"type": "number", "x-unit": unit}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _amc_obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _amc_slot(schema, unit, origin, visibility="agent", capability=None, parameter=None):
    return {"value_schema": schema, "unit": unit, "origin": origin, "visibility": visibility,
            "access": "read_write" if capability else "read_only",
            "write_via": {"capability_id": capability, "parameter": parameter, "fixed_arguments": {}} if capability else None,
            "reference_conditions": None, "sampling": {"mode": "instant", "time_domain": "simulation", "window": None}}


_AMC_NULL_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_AMC_SLOTS = {
    "power.enabled": _amc_slot({"type": "boolean"}, "1", "setpoint", capability="power.set", parameter="enabled"),
    "gas.selected_id": _amc_slot(_AMC_NULL_STRING, "1", "setpoint", capability="gas.select", parameter="gas_id"),
    "gas.reference_temperature_k": _amc_slot(_amc_num("K", 0), "K", "setpoint"),
    "gas.reference_pressure_pa": _amc_slot(_amc_num("Pa", 0), "Pa", "setpoint"),
    "flow.setpoint_m3_s": _amc_slot(_amc_num("m^3/s", 0, ALICAT_MC_FS_M3_S), "m^3/s", "setpoint", capability="flow.set_setpoint", parameter="flow_rate_m3_s"),
    "flow.zeroed": _amc_slot({"type": "boolean"}, "1", "derived"),
    "ports.upstream_node_id": _amc_slot(_AMC_NULL_STRING, "1", "scene"),
    "ports.downstream_node_id": _amc_slot(_AMC_NULL_STRING, "1", "scene"),
    "simulation.dynamic_model_configured": _amc_slot({"type": "boolean"}, "1", "derived"),
    "measurement.standard_flow_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "derived"),
    "measurement.actual_flow_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "derived"),
    "measurement.mass_flow_kg_s": _amc_slot(_amc_num("kg/s", 0, nullable=True), "kg/s", "derived"),
    "measurement.pressure_pa": _amc_slot(_amc_num("Pa", 0, nullable=True), "Pa", "derived"),
    "measurement.temperature_k": _amc_slot(_amc_num("K", 0, nullable=True), "K", "derived"),
    "measurement.total_standard_volume_m3": _amc_slot(_amc_num("m^3", 0, nullable=True), "m^3", "derived"),
    "measurement.standard_accuracy_bound_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "derived"),
    "measurement.accuracy_conditions_met": _amc_slot({"type": "boolean"}, "1", "derived"),
    "truth.standard_flow_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "process_truth", "debug"),
    "truth.total_standard_volume_m3": _amc_slot(_amc_num("m^3", 0, nullable=True), "m^3", "process_truth", "debug"),
    "truth.upstream_pressure_pa": _amc_slot(_amc_num("Pa", 0, nullable=True), "Pa", "process_truth", "debug"),
    "truth.downstream_pressure_pa": _amc_slot(_amc_num("Pa", 0, nullable=True), "Pa", "process_truth", "debug"),
    "truth.temperature_k": _amc_slot(_amc_num("K", 0, nullable=True), "K", "process_truth", "debug"),
    "truth.equilibrium": _amc_slot({"type": "boolean"}, "1", "process_truth", "debug"),
    "truth.no_flow": _amc_slot({"type": "boolean"}, "1", "process_truth", "debug"),
    "truth.response_time_constant_s": _amc_slot(_amc_num("s", 0, nullable=True), "s", "process_truth", "debug"),
    # front-panel device functions (menu settings, totalizer timer and batch, display, COMPOSER mixes)
    "device.settings": _amc_slot(_AMC_SETTINGS_SCHEMA, "1", "setpoint"),
    "flow.active_setpoint_m3_s": _amc_slot(_amc_num("m^3/s", 0, ALICAT_MC_FS_M3_S), "m^3/s", "derived"),
    "flow.ramp_rate_m3_s2": _amc_slot(_amc_num("m^3/s^2", 0), "m^3/s^2", "derived"),
    "setpoint.analog_input_v": _amc_slot(_amc_num("V", 0, ALICAT_MC_ANALOG_FS_V), "V", "scene"),
    "totalizer.batch_size_m3": _amc_slot(_amc_num("m^3", 0, ALICAT_MC_BATCH_MAX_M3, nullable=True), "m^3", "setpoint", capability="totalizer.set_batch",
                                         parameter="batch_size_m3"),
    "totalizer.batch_remaining_m3": _amc_slot(_amc_num("m^3", 0, nullable=True), "m^3", "derived"),
    "totalizer.elapsed_s": _amc_slot(_amc_num("s", 0, ALICAT_MC_TIMER_MAX_S), "s", "derived"),
    "display.backlight_on": _amc_slot({"type": "boolean"}, "1", "setpoint", capability="display.set_backlight", parameter="on"),
    "display.locked": _amc_slot({"type": "boolean"}, "1", "setpoint", capability="display.set_lock", parameter="locked"),
    "gas.user_mixes": _amc_slot({"type": "array", "items": _AMC_MIX, "maxItems": 20}, "1", "setpoint"),
    "gas.recent_ids": _amc_slot({"type": "array", "items": {"type": "string"}, "maxItems": ALICAT_MC_RECENT}, "1", "derived"),
    "valve.drive_percent": _amc_slot(_amc_num("%", 0, 100, nullable=True), "%", "derived"),
    "valve.drive_setpoint_percent": _amc_slot(_amc_num("%", 0, 100), "%", "setpoint", capability="valve.set_drive_setpoint", parameter="percent"),
    # status messages of the main display (manual p.6): ADC injected by the evaluator; POV / TOV when the host's inlet pressure or gas temperature
    # leaves the device range (160 PSIA, -10..60 C, datasheet)
    "device.alarms": _amc_slot({"type": "array", "items": {"type": "string", "enum": ["ADC", "POV", "TOV"]}, "maxItems": 3}, "1", "derived"),
    "truth.adc_fault": _amc_slot({"type": "boolean"}, "1", "process_truth", "debug"),
}
_AMC_PANEL_DEVICE_CAPABILITIES = ("menu.configure", "totalizer.set_batch", "display.set_backlight", "display.set_lock", "gas.create_mix", "gas.delete_mix",
                                  "device.factory_restore", "valve.set_drive_setpoint")
_AMC_FAULT_CAPABILITIES = ("fault.inject", "fault.clear")
ALICAT_MC_TEMPERATURE_RANGE_K = (263.15, 333.15)        # operating temperature -10..60 C, ambient and gas (datasheet)
_AMC_MEASUREMENTS = [key for key in _AMC_SLOTS if key.startswith("measurement.")]
_AMC_LIMITATIONS = [
    "仅为合成过程与接口实现，无硬件驱动、机器人或真实阀动作。",
    "气体兼容性由可信宿主登记；厂家98种气体和20个混气定义的数据库未实现。未知摩尔质量及性质保持null。",
    "动态没有缺省时间常数。宿主必须给出明确假设及证据；最快T63 30 ms不是稳定时间，也不自动作为tau。",
    "简化一阶闭环模型仅在有效供压/背压和温度条件下使用：时间常数=宿主tau×(167/P)×(D/1280)（PD/PDF；PD2I用P+I），出厂增益即宿主tau，下限30 ms；"
    "阀按线性阀假设（100 %驱动在规格书满量程压降1.5 PSID下通过满量程），流量受此饱和。真实阀曲线、超调/振荡、真实稳定判据和非理想气体性质未实现。",
    "模型读数均为estimated；压力使用上游边界代理，温度使用宿主气温代理。不是实测传感器或厂家精度验证。",
    "精度界限仅在归零且宿主声明平衡时计算 max(0.006|Q|,0.001FS)；不生成误差分布，不从固定时长推断平衡。",
    "标准流量和累计标准体积使用当前参考条件；改气体/参考条件使归零和读数失效并开启新累计量区间。",
    "参考条件配置采用仿真定义：1 SLPM数值量程按当前参考条件解释；不声称复现厂商改变标准温压后内部标度处理。",
    "气路节点引用由宿主保管。本适配器不做气源库存、守恒物质转移或压力网络求解，后续气路宿主需接入这些过程。",
    "重放去重仅当前宿主内存有效，不声称跨重启恰好一次。API过滤不是Python或文件系统隔离。",
    "前面板（panel.*）：按操作手册（MC系列固件9v，Rev.4）实现单色背光LCD、上下各三个软键与标志背光键；主显示、总量器、主菜单、CONTROL/SETUP/ABOUT列表、"
    "选项列表、逐位数值输入、Gas Select类别与气体表、COMPOSER混气、确认画面和LCK锁定；每个面板动作都调用本运行层的能力（没有第二份仪器逻辑）。",
    "面板背后的设备功能已在运行层实现：自动归零（零设定保持延时后、宿主确认无流与平衡时）、设定来源串口/模拟（0–5 V输入，超限按限值）、上电设定值、设定值上下限、"
    "质量或体积流量控制、设定值斜坡（delta/time、升/降、上电与零设定选项）、批量给气（达到批量后阀关闭）与总量计时器、工程单位、流量/压力平均（一阶滞后）、零带、"
    "背光、对比度、屏幕倒置、COMPOSER混气（库纯气体按Dalton/Wilke求物性）、Recent列表、LCK锁定与Factory Restore。",
    "控制变量绝对压力未仿真：上下游压力是宿主给出的边界，没有被阀充入或排出的过程容积（宿主未提供下游容积/压力模型），menu.configure对其返回unsupported；流量限制器与控制死区"
    "只在压力控制时起作用，因此只保存显示。控制变量阀驱动已实现（手册p.14；串口统计45“设定值直接驱动所选阀”）：设定值为阀驱动百分比（valve.set_drive_setpoint，"
    "面板SETPT以%输入），绕过流量闭环，流量按线性阀=驱动×1.5 PSID满量程流量×压差比例并以宿主tau一阶响应；切换控制变量时设定值按满量程百分比换算（假设，手册对设定值上下限如此说明）；"
    "阀驱动模式下flow.set_setpoint被拒，模拟输入0–5 V对应0–100 %驱动，上电保持上次驱动设定（假设），斜坡不作用于驱动。"
    "PD/PDF、PD2I增益作用于闭环响应（见上）；阀驱动百分比=控制器输出（所需驱动，0–100 %），主屏可显示。",
    "串口设置（Unit ID、Modbus地址与超时动作、波特率）只作为设备设置保存与显示；仿真API不是Alicat ASCII/Modbus命令集，没有模拟串口或Modbus主站。",
    "主显示状态消息（手册p.6）：POV/TOV在宿主给出的进口压力高于160 PSIA或气温超出−10…60 °C（规格书）时显示（此时读数不可用）；ADC（模数转换器错误）由评测者fault.inject注入、"
    "fault.clear清除，只显示状态消息（手册未写其它行为与处理，读数照常为假设）；EXH（MCD双阀）、HLD（串口阀保持命令）与TMF（压力控制）不出现。",
    "工程单位只提供手册单位表中的标准体积与实际体积子集（无N开头的标准态单位与真质量单位，因此NTP菜单不出现）；流量与压力平均对宿主瞬时改变的边界同样按一阶滞后显示，"
    "两步之间读数保持；平均与零带默认0（厂家默认值未写）。",
]


def export_alicat_mc_contract_metadata(panel=True):
    """Type-only contract inputs; excludes all instance gas/boundary values.  panel_capabilities: the front-panel commands (panel.*)."""
    slots = _amc_copy.deepcopy(_AMC_SLOTS)
    for sid, slot in slots.items():
        slot["read_binding"] = "AlicatMCSession.read(instance_id=..., state_ids=['%s'])" % sid
        if "standard" in sid or sid == "flow.setpoint_m3_s" or sid.startswith("totalizer.batch"):
            slot["reference_conditions"] = "Standard volume at gas.reference_temperature_k and gas.reference_pressure_pa; defaults 298.15 K and 101325 Pa"
        elif sid == "flow.active_setpoint_m3_s":
            slot["reference_conditions"] = "Control variable: standard flow (mass flow control) or actual flow at inlet conditions (volumetric flow control)"
        elif "pressure" in sid:
            slot["reference_conditions"] = "Absolute pressure"
        elif sid == "measurement.actual_flow_m3_s":
            slot["reference_conditions"] = "Ideal-gas conversion at host upstream absolute pressure and gas temperature; no real gas correction"
    arguments = {
        "power.set": {"enabled": {"type": "boolean"}},
        "gas.select": {"gas_id": {"type": "string", "description": "An explicitly host-registered compatible gas ID; no bundled gas library"}},
        "gas.set_reference_conditions": {"temperature_K": {"type": "number", "exclusiveMinimum": 0, "x-unit": "K"}, "pressure_Pa": {"type": "number", "exclusiveMinimum": 0, "x-unit": "Pa"}},
        "flow.set_setpoint": {"flow_rate_m3_s": {"type": "number", "minimum": 0, "maximum": ALICAT_MC_FS_M3_S, "x-unit": "m^3/s", "x-reference-conditions": "gas.reference_temperature_k, gas.reference_pressure_pa", "description": "0 stops the target; positive values must be >= 0.0001 full scale"}},
        "flow.zero": {}, "flow.reset_total": {}, "flow.read": {}, "control.set_pid": {},
        "menu.configure": {k: _amc_copy.deepcopy(v[0]) for k, v in _AMC_SETTINGS.items()},
        "totalizer.set_batch": {"batch_size_m3": dict(_amc_num("m^3", 0, ALICAT_MC_BATCH_MAX_M3, nullable=True), **{"x-unit": "m^3"})},
        "display.set_backlight": {"on": {"type": "boolean"}}, "display.set_lock": {"locked": {"type": "boolean"}},
        "gas.create_mix": {"mix_number": {"anyOf": [{"type": "integer", "minimum": 236, "maximum": 255, "x-unit": "1"}, {"type": "integer", "enum": [0], "x-unit": "1"}],
                                          "description": "236-255 (an existing mix with that number is overwritten), or 0 for the next free number from 255 down"},
                           "short_name": _amc_copy.deepcopy(_AMC_MIX["properties"]["short_name"]), "long_name": _amc_copy.deepcopy(_AMC_MIX["properties"]["long_name"]),
                           "components": {"type": "array", "items": _amc_copy.deepcopy(_AMC_MIX_COMPONENT), "minItems": 2, "maxItems": 5,
                                          "description": "Library gases of Gas Select (template.list, standard MC); molar percentages to 0.01 %, total exactly 100 %"}},
        "gas.delete_mix": {"mix_number": {"type": "integer", "minimum": 236, "maximum": 255, "x-unit": "1"}},
        "device.factory_restore": {},
        "valve.set_drive_setpoint": {"percent": {"type": "number", "minimum": 0, "maximum": 100, "x-unit": "%", "description": "valve drive in percent (control variable valve_drive)"}},
        "fault.inject": {"code": {"type": "string", "enum": ["ADC"], "description": "status message of manual p.6 injected by the evaluator"}},
        "fault.clear": {},
    }
    direct = {"power.set": ["power.enabled"], "gas.select": ["gas.selected_id"],
              "gas.set_reference_conditions": ["gas.reference_temperature_k", "gas.reference_pressure_pa"],
              "flow.set_setpoint": ["flow.setpoint_m3_s", "truth.equilibrium"], "flow.zero": ["flow.zeroed", "truth.standard_flow_m3_s"],
              "flow.reset_total": ["truth.total_standard_volume_m3"], "flow.read": [], "control.set_pid": [],
              "menu.configure": ["device.settings"], "totalizer.set_batch": ["totalizer.batch_size_m3"], "display.set_backlight": ["display.backlight_on"],
              "display.set_lock": ["display.locked"], "gas.create_mix": ["gas.user_mixes"], "gas.delete_mix": ["gas.user_mixes"],
              "device.factory_restore": ["device.settings", "gas.selected_id", "gas.reference_temperature_k", "gas.reference_pressure_pa"],
              "valve.set_drive_setpoint": ["valve.drive_setpoint_percent"], "fault.inject": ["device.alarms"], "fault.clear": ["device.alarms"]}
    # effects of the device functions behind the front panel on the original commands (new slots only; the old effects are unchanged)
    added = {"power.set": ["flow.setpoint_m3_s", "flow.active_setpoint_m3_s", "truth.equilibrium", "display.backlight_on", "totalizer.elapsed_s",
                           "totalizer.batch_remaining_m3", "device.alarms"], "fault.inject": ["truth.adc_fault"], "fault.clear": ["truth.adc_fault"],
             "gas.select": ["gas.recent_ids", "totalizer.batch_remaining_m3"], "gas.set_reference_conditions": ["totalizer.batch_remaining_m3"],
             "flow.set_setpoint": ["flow.active_setpoint_m3_s"], "flow.reset_total": ["totalizer.elapsed_s", "totalizer.batch_remaining_m3"],
             "menu.configure": ["flow.setpoint_m3_s", "flow.active_setpoint_m3_s", "truth.equilibrium", "flow.ramp_rate_m3_s2", "valve.drive_setpoint_percent"],
             "totalizer.set_batch": ["totalizer.batch_remaining_m3"], "valve.set_drive_setpoint": ["truth.equilibrium"],
             "gas.create_mix": [], "gas.delete_mix": ["gas.recent_ids"], "display.set_backlight": [], "display.set_lock": [],
             "device.factory_restore": ["flow.zeroed", "truth.standard_flow_m3_s", "truth.total_standard_volume_m3", "totalizer.batch_size_m3",
                                        "totalizer.batch_remaining_m3", "flow.active_setpoint_m3_s", "flow.ramp_rate_m3_s2"]}
    summaries = {"power.set": "设置仿真供电；断电令测量不可用且归零失效，未假设失电阀位。",
        "gas.select": "零目标下选用宿主登记气体；尚未接气路或宿主确认无流；清除归零和累计量区间。",
        "gas.set_reference_conditions": "零目标下设置标准体积的参考温压；尚未接气路或宿主确认无流；清除归零和累计量区间。",
        "flow.set_setpoint": "写入0或0.01–100%FS目标；成功不代表达到目标或平衡。",
        "flow.zero": "只在宿主提供无流、平衡与有效边界条件时记录一次合成归零；不推进时间。",
        "flow.reset_total": "清零仿真累计标准体积的显示区间，不转移或删除气体。",
        "flow.read": "读取当前估计观测，不推进时间。",
        "control.set_pid": "旧占位命令（无参数，unsupported）：PID增益经menu.configure（loop_type、p_gain、d_gain、i_gain）设置，并按最小闭环模型作用于响应；真实阀曲线未实现。",
        "menu.configure": "前面板菜单的设备设置（可只给要改的项）：自动归零与延时、设定来源串口/模拟、Modbus超时动作、上电设定值、设定值上下限、控制变量（质量/体积流量、"
                          "阀驱动；绝对压力返回unsupported；切换时设定值按满量程百分比换算）、PD/PDF或PD2I增益、流量限制器、死区、设定值斜坡、阀驱动显示、工程单位、参考单位、"
                          "平均、零带、串口设置、主屏按键动作、对比度、上电背光、屏幕倒置。",
        "valve.set_drive_setpoint": "控制变量为阀驱动时的设定值：阀驱动百分比0–100 %（设定值直接驱动阀，绕过流量闭环）；流量随线性阀与压差以宿主tau响应。",
        "totalizer.set_batch": "总量器批量（标准体积；null或0为-NONE-）：有非零设定值时，示值累计达到批量后阀关闭，RESET后重新开始；断电保留。",
        "display.set_backlight": "单色LCD背光开关（面板上的Alicat标志键）。",
        "display.set_lock": "串口锁定/解锁前面板（LCK）；锁定时面板只接受同时按住四个外侧键解锁。",
        "gas.create_mix": "COMPOSER自定义混气：2–5种Gas Select库气体，摩尔百分比精确到0.01 %且合计100 %；编号236–255（0为从255向下的下一个空号），最多20种。",
        "gas.delete_mix": "删除一个COMPOSER混气（当前选用的混气不能删除）。",
        "device.factory_restore": "恢复出厂设置：菜单设置、标准参考条件25 °C/1 atm、气体Air、清除批量；须在零目标且停流时进行（与换气相同），归零与累计量区间失效。",
        "fault.inject": "评测者：注入手册p.6的状态消息ADC（模数转换器错误），主显示在读数右侧显示ADC，直到fault.clear；用于测试视觉agent对状态消息的处理（手册未写其它行为）。",
        "fault.clear": "评测者：清除注入的ADC状态。"}
    examples = {"power.set": {"enabled": True}, "gas.select": {"gas_id": "host_registered_compatible_gas"},
        "gas.set_reference_conditions": {"temperature_K": 298.15, "pressure_Pa": 101325.0},
        "flow.set_setpoint": {"flow_rate_m3_s": ALICAT_MC_FS_M3_S / 2},
        "flow.zero": {}, "flow.reset_total": {}, "flow.read": {}, "control.set_pid": {},
        "menu.configure": {"autotare": True, "units_mass_flow": "SCCM"}, "totalizer.set_batch": {"batch_size_m3": 0.001},
        "display.set_backlight": {"on": False}, "display.set_lock": {"locked": True},
        "gas.create_mix": {"mix_number": 0, "short_name": "MYGAS1", "long_name": "CH4 CO2 N2", "components": [
            {"gas_id": "gas_methane", "percent": 71.35}, {"gas_id": "gas_carbon_dioxide", "percent": 19.25}, {"gas_id": "gas_nitrogen", "percent": 9.4}]},
        "gas.delete_mix": {"mix_number": 255}, "device.factory_restore": {}, "valve.set_drive_setpoint": {"percent": 40.0}, "fault.inject": {"code": "ADC"},
        "fault.clear": {}}
    preconditions = {"power.set": [], "gas.select": ["已上电；目标为0；尚未接气路或宿主确认无流；gas_id由宿主登记兼容证据"],
        "gas.set_reference_conditions": ["已上电；目标为0；尚未接气路或宿主确认无流；参考温压有限且大于0"],
        "flow.set_setpoint": ["已上电且已选宿主登记气体；正目标须>=0.0001FS；写入目标不要求过程已就绪",
                              "设定来源为串口/前面板（模拟来源时拒绝）；目标在设定值上下限之内",
                              "控制变量不是阀驱动（阀驱动时设定值为百分比，用valve.set_drive_setpoint）"],
        "flow.zero": ["已上电且已选气体；目标为0；进出气节点已接入；有效温压；宿主no_flow及equilibrium均为true"],
        "flow.reset_total": ["已上电"], "flow.read": [], "control.set_pid": [],
        "menu.configure": ["已上电；至少一项；设定值下限<=上限；控制变量为压力或阀驱动时返回unsupported"],
        "totalizer.set_batch": ["已上电"], "display.set_backlight": ["已上电"], "display.set_lock": ["已上电"],
        "gas.create_mix": ["已上电；成分为不同的Gas Select库气体（template.list中standard_mc）；百分比合计100.00 %；已有20种且编号为0时拒绝"],
        "gas.delete_mix": ["已上电；该编号的混气存在且不是当前气体"],
        "device.factory_restore": ["已上电；目标为0；尚未接气路或宿主确认无流（与gas.select相同）"],
        "valve.set_drive_setpoint": ["已上电；控制变量为阀驱动；设定来源为串口/前面板（模拟来源时拒绝）；在设定值上下限换算的百分比之内"],
        "fault.inject": ["调用者为evaluator/debug身份；当前没有注入的ADC"], "fault.clear": ["调用者为evaluator/debug身份"]}
    caps = {}
    for cid, props in arguments.items():
        effects = list(direct[cid])
        if cid == "power.set":
            effects += ["flow.zeroed", "truth.standard_flow_m3_s", "truth.total_standard_volume_m3"]
        if cid in ("gas.select", "gas.set_reference_conditions"):
            effects += ["flow.zeroed", "truth.standard_flow_m3_s", "truth.total_standard_volume_m3"]
        if cid not in ("flow.read", "control.set_pid") + _AMC_FAULT_CAPABILITIES:
            effects += _AMC_MEASUREMENTS + ["valve.drive_percent"]
        effects += added.get(cid, [])
        effect_entries = [{"state_id": sid, "timing": "immediate", "description_zh": "同步变更设定或刷新明确标注的观测代理"} for sid in dict.fromkeys(effects)]
        if cid == "flow.set_setpoint":
            immediate = ["flow.setpoint_m3_s", "truth.equilibrium", "measurement.accuracy_conditions_met", "measurement.standard_accuracy_bound_m3_s"]
            future = ["truth.standard_flow_m3_s", "truth.total_standard_volume_m3", "measurement.standard_flow_m3_s", "measurement.actual_flow_m3_s", "measurement.mass_flow_kg_s", "measurement.total_standard_volume_m3"]
            effect_entries = [{"state_id": sid, "timing": "immediate", "description_zh": "写入目标且撤销上次平衡状态的精度条件"} for sid in immediate]
            effect_entries += [{"state_id": sid, "timing": "on_step", "description_zh": "仅在宿主显式配置动态及有效气路边界后，随共同时间推进简化流量响应和积分"} for sid in future]
            effect_entries += [{"state_id": "flow.active_setpoint_m3_s", "timing": "immediate", "description_zh": "无斜坡时当前生效目标随即等于新目标；有斜坡时随宿主时间推进"}]
            effect_entries += [{"state_id": "valve.drive_percent", "timing": "immediate", "description_zh": "控制器输出（阀驱动%）：无流或反压时随新目标在0与100 %之间切换，其余随流量响应变化"}]
        result_ids = _AMC_MEASUREMENTS if cid == "flow.read" else direct[cid]
        result_ids = [sid for sid in result_ids if slots[sid]["visibility"] == "agent"]
        if cid == "flow.reset_total":
            result_ids = ["measurement.total_standard_volume_m3"]
        caps[cid] = {"availability": "unsupported" if cid == "control.set_pid" else "implemented", "visibility": "evaluator" if cid in _AMC_FAULT_CAPABILITIES else "agent",
            "kind": "query" if cid == "flow.read" else "configuration" if cid.startswith("gas.") or cid == "menu.configure" else
            "simulation" if cid in _AMC_FAULT_CAPABILITIES else "device_command",
            "description_zh": summaries[cid], "input_schema": dict(_amc_obj(props), required=[], minProperties=1) if cid == "menu.configure" else _amc_obj(props),
            "output_schema": _amc_obj({sid: _amc_copy.deepcopy(slots[sid]["value_schema"]) for sid in result_ids}),
            "state_effects": effect_entries,
            "observes": result_ids, "preconditions_zh": preconditions[cid], "example_arguments": [examples[cid]],
            "binding": None if cid == "control.set_pid" else "basic40_runtime_automation.AlicatMCSession.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % cid,
            "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "未实现屏幕跨层同步；内阀行程与机构未知，未用归一化参数假冒机械关节"}}
    panel_caps = {}
    if panel and (ALICAT_MC_ASSET_DIR / "panel_ui.json").is_file():
        p = _amc_panel()
        called = p["engine"].spec_capabilities(p["spec"])
        gesture_effects = list({e["state_id"]: dict(e, description_zh="面板动作调用的能力的效果：" + e["description_zh"])
                                for c in called for e in caps[c]["state_effects"] if e["timing"] == "immediate"}.values())
        for name, d in p["caps"].items():
            gesture = name not in ("panel.capture", "panel.frame")
            panel_caps[name] = {"availability": "implemented", "visibility": d["visibility"], "kind": d["kind"], "description_zh": d["function_zh"],
                "component_ids": d["component_ids"], "input_schema": d["input_schema"], "output_schema": d["output_schema"],
                "example_arguments": [d["example_arguments"]], "observes": [], "state_effects": _amc_copy.deepcopy(gesture_effects) if gesture else [],
                "preconditions_zh": [],
                "binding": "basic40_runtime_automation.AlicatMCSession.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % name,
                "completion_criterion_zh": "手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看" if gesture else "同步返回当前画面",
                "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "没有3D模型绑定：屏幕纹理与按键关节联动属于后续Matterix步骤"}}
    return {"asset_types": {ALICAT_MC_TYPE: {"catalog_id": "CHEM-180", "model": "Alicat Scientific MC, 1 SLPM, standard accuracy, Rev6 (2025-07)",
        "asset_version": "1.4.1", "state_slots": slots, "capabilities": caps, "panel_capabilities": panel_caps,
        "parameters": {
            "full_scale_standard_flow_m3_s": {"value": ALICAT_MC_FS_M3_S, "unit": "m^3/s", "evidence": "catalog"},
            "minimum_positive_control_m3_s": {"value": ALICAT_MC_MIN_M3_S, "unit": "m^3/s", "evidence": "catalog"},
            "fastest_control_t63_s": {"value": 0.03, "unit": "s", "evidence": "catalog", "note_zh": "配置与调整相关的最快T63，非稳定时间且不设为默认tau"},
            "default_reference_temperature_K": {"value": 298.15, "unit": "K", "evidence": "catalog"},
            "default_reference_pressure_Pa": {"value": 101325.0, "unit": "Pa", "evidence": "catalog"},
            "default_dynamic_time_constant_s": {"value": None, "unit": "s", "evidence": "unknown"},
            "gas_molar_mass_kg_mol": {"value": None, "unit": "kg/mol", "evidence": "unknown"},
            "valve_travel_m": {"value": None, "unit": "m", "evidence": "unknown"},
            "pid_gains": {"value": None, "unit": "1", "evidence": "unknown", "note_zh": "实际参数结构及单位未知；1仅为此未知占位项的合同单位"},
            "full_scale_pressure_Pa": {"value": ALICAT_MC_FS_PRESSURE_PA, "unit": "Pa", "evidence": "catalog", "note_zh": "工作压力上限160 PSIA，作为ABOUT>Full Scale Ranges的绝对压力量程"},
            "analog_setpoint_full_scale_V": {"value": ALICAT_MC_ANALOG_FS_V, "unit": "V", "evidence": "public_document", "note_zh": "手册p.9：Mini-DIN第4脚模拟设定输入，按标准0–5 Vdc对应0–100 %FS"},
            "totalizer_timer_max_s": {"value": ALICAT_MC_TIMER_MAX_S, "unit": "s", "evidence": "public_document", "note_zh": "手册p.11：计时器最大9999:59:59"},
            "front_panel_factory_settings": {"value": {k: v[1] for k, v in _AMC_SETTINGS.items()}, "unit": "1", "evidence": "assumption",
                                             "note_zh": "手册写明的默认值（自动归零开、2 s、19200波特、STP 25 °C/1 atm、单位按订货）之外为假设或手册示例画面上的值（P 167、D 1280、限制器增益500）"}},
        "limitations": list(_AMC_LIMITATIONS)}},
        "host_capabilities": {
            "host.register_gas": {"binding": "AlicatMCRuntime.host_register_gas", "scope": "Trusted gas IDs and compatibility evidence; optional host molar mass evidence"},
            "host.connect_ports": {"binding": "AlicatMCRuntime.host_connect_ports", "scope": "instance upstream inlet node -> MFC -> downstream outlet node; does not solve the network"},
            "host.set_boundary": {"binding": "AlicatMCRuntime.host_set_boundary", "scope": "Trusted absolute pressures, temperature, no-flow and equilibrium assertions"},
            "host.configure_dynamics": {"binding": "AlicatMCRuntime.host_configure_dynamics", "scope": "Explicit positive first-order tau and assumption evidence; no default tau"},
            "sim.step": {"binding": "AlicatMCRuntime.host_step(dt_s)", "scope": "All registered MFC instances use one host clock; scheduling bound 0..3600s, not a vendor limit"},
            "host.set_analog_setpoint_input": {"binding": "AlicatMCRuntime.host_set_analog_setpoint_input(instance_id, *, volts)",
                                               "scope": "0-5 V signal on the analog setpoint pin; used when the setpoint source is Analog (default 0 V: nothing connected)"}},
        "result_schema_scope": "output_schema validates response.result; public observations never include truth.*"}


class AlicatMCRuntime:
    """Trusted host; state and deduplication are isolated per runtime/instance."""
    def __init__(self):
        self.time_s = 0.0
        self._instances = {}
        self._gases = {}
        self._principals = {"__alicat_panel__": ("agent", None)}
        self._requests = {}
        self._logs = {"__alicat_panel__": []}
        self._lock = _amc_threading.RLock()
        self._panel_session = AlicatMCSession(self, "__alicat_panel__")    # the front panel's own path to the commands (not logged)
        self._panels = {}
        self.panel_capture_dir = None                                        # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)

    def create_instance(self, instance_id, asset_type_id=ALICAT_MC_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != ALICAT_MC_TYPE:
                raise ValueError("Unique nonempty instance ID and CHEM-180 asset type required")
            values = {sid: None for sid in _AMC_SLOTS}
            values.update({"power.enabled": False, "gas.reference_temperature_k": 298.15, "gas.reference_pressure_pa": 101325.0,
                "flow.setpoint_m3_s": 0.0, "flow.zeroed": False, "simulation.dynamic_model_configured": False,
                "measurement.accuracy_conditions_met": False, "truth.equilibrium": False, "truth.no_flow": False,
                "device.settings": {k: _amc_copy.deepcopy(v[1]) for k, v in _AMC_SETTINGS.items()}, "flow.active_setpoint_m3_s": 0.0, "flow.ramp_rate_m3_s2": 0.0,
                "setpoint.analog_input_v": 0.0, "totalizer.elapsed_s": 0.0, "display.backlight_on": True, "display.locked": False,
                "gas.user_mixes": [], "gas.recent_ids": [], "valve.drive_percent": 0.0, "valve.drive_setpoint_percent": 0.0,
                "device.alarms": [], "truth.adc_fault": False})
            self._instances[instance_id] = {"instance_id": instance_id, "values": values, "revision": 0, "dynamics_evidence": None,
                                            "power_epoch": 0, "zero_since_s": None, "autotare_done": False, "averages": {}}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Host-assigned principal must be unique and have a valid visibility")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(k, str) or k not in self._instances for k in instance_ids)):
                raise ValueError("Invalid instance scope")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals:
            raise ValueError("Host authentication and registration required")
        return AlicatMCSession(self, principal_id)

    def host_register_gas(self, gas_id, *, compatibility_evidence, molar_mass_kg_mol=None, property_evidence=None):
        with self._lock:
            if not isinstance(gas_id, str) or not gas_id.strip() or gas_id in self._gases or not isinstance(compatibility_evidence, str) or not compatibility_evidence.strip():
                raise ValueError("Unique gas ID and compatibility evidence required")
            if molar_mass_kg_mol is not None and (not _amc_number(molar_mass_kg_mol) or molar_mass_kg_mol <= 0 or not isinstance(property_evidence, str) or not property_evidence.strip()):
                raise ValueError("Finite positive molar mass requires explicit property evidence")
            self._gases[gas_id] = {"compatibility_evidence": compatibility_evidence,
                "molar_mass_kg_mol": molar_mass_kg_mol, "property_evidence": property_evidence}

    def host_enable_library_gas(self, gas_id, *, compatibility_evidence):
        """Template hook: trusted approval of an S-Series-only/unlisted library gas (standard-MC Gas Select gases need none)."""
        return _amc_templates().host_enable_library_gas(self, gas_id, compatibility_evidence=compatibility_evidence)

    def host_set_line_gas(self, instance_id, *, gas_id):
        """Template hook: trusted declaration of the library gas physically in the line (None = the selected gas)."""
        return _amc_templates().host_set_line_gas(self, instance_id, gas_id=gas_id)

    def _require(self, instance_id):
        if not isinstance(instance_id, str) or instance_id not in self._instances:
            raise ValueError("Unknown instance")
        return self._instances[instance_id]

    def _commit(self, inst, values):
        self._refresh(values, inst)
        if inst["values"] != values:
            inst["values"] = values
            inst["revision"] += 1

    @staticmethod
    def _boundaries_valid(s):
        lower, upper = 11.5 * ALICAT_MC_PSI_PA, 160 * ALICAT_MC_PSI_PA
        return (s["ports.upstream_node_id"] is not None and s["ports.downstream_node_id"] is not None
            and all(_amc_number(s[key]) for key in ("truth.upstream_pressure_pa", "truth.downstream_pressure_pa", "truth.temperature_k"))
            and lower <= s["truth.upstream_pressure_pa"] <= upper and lower <= s["truth.downstream_pressure_pa"] <= upper
            and 263.15 <= s["truth.temperature_k"] <= 333.15)

    @staticmethod
    def _stopped_for_configuration(s):
        unconnected = s["ports.upstream_node_id"] is None and s["ports.downstream_node_id"] is None
        return s["flow.setpoint_m3_s"] == 0 and ((unconnected and s["truth.standard_flow_m3_s"] is None)
            or (s["truth.no_flow"] and s["truth.standard_flow_m3_s"] in (None, 0)))

    def _refresh(self, s, inst=None, averaged=True):
        """Readings from the process truth, then the flow / pressure averages and the zero band (manual p.20), then the batch remainder."""
        self._readings(s, inst)
        if averaged and inst is not None:
            for sid, value in inst.get("averages", {}).items():
                if s[sid] is not None:
                    s[sid] = value
        q = s["measurement.standard_flow_m3_s"]
        if q is not None and abs(q) < s["device.settings"]["zero_band_pct"] / 100.0 * ALICAT_MC_FS_M3_S:
            for sid in ("measurement.standard_flow_m3_s", "measurement.actual_flow_m3_s", "measurement.mass_flow_kg_s"):
                if s[sid] is not None:
                    s[sid] = 0.0
        total, batch = s["measurement.total_standard_volume_m3"], s["totalizer.batch_size_m3"]
        s["totalizer.batch_remaining_m3"] = None if batch is None or total is None else max(0.0, batch - total)
        s["flow.ramp_rate_m3_s2"] = self._ramp_rate(s)
        s["valve.drive_percent"] = self._drive(s)

    # ---- control loop and valve (minimal model)
    @staticmethod
    def _loop_tau(s, tau):
        """Closed-loop time constant from the gains (manual p.14: PD/PDF larger P faster, larger D slower; PD2I P and I faster, D slower): the
        host tau at the factory gains, scaled by (P / 167) x (1280 / D) (PD2I: P + I), not below 30 ms; None when P (and I) is 0 (no correction)."""
        if tau is None:
            return None
        st = s["device.settings"]
        p = st["p_gain"] + (st["i_gain"] if st["loop_type"] == "pd2i" else 0)
        if p <= 0:
            return None
        strength = (p / ALICAT_MC_FACTORY_GAINS[0]) * (ALICAT_MC_FACTORY_GAINS[1] / max(st["d_gain"], 1))
        return max(tau / strength, min(tau, ALICAT_MC_FASTEST_TAU_S))

    @staticmethod
    def _open_flow(s):
        """True standard flow at 100 % valve drive: full scale at the datasheet pressure drop, linear in the pressure difference (assumption)."""
        up, down = s["truth.upstream_pressure_pa"], s["truth.downstream_pressure_pa"]
        return None if up is None or down is None else ALICAT_MC_FS_M3_S * (up - down) / ALICAT_MC_DP_FS_PA

    def _drive(self, s):
        """Valve drive (controller output, %): the drive the present flow needs on the linear valve; 100 % when the setpoint asks for flow the
        valve cannot pass (no flow, reverse pressure); 0 % unpowered or at zero setpoint without flow; None when the flow is undefined."""
        if not s["power.enabled"]:
            return 0.0
        if s["device.settings"]["control_variable"] == "valve_drive":                 # the setpoint drives the valve directly
            return float(s["valve.drive_setpoint_percent"])
        q, q_open, active = s["truth.standard_flow_m3_s"], self._open_flow(s), s["flow.active_setpoint_m3_s"]
        if q_open is None or s["gas.selected_id"] is None:
            return None
        if s["truth.no_flow"] or q_open <= 0 or q is None:
            return (100.0 if active > 0 else 0.0) if (s["truth.no_flow"] or q_open <= 0) and self._boundaries_valid(s) and s["flow.zeroed"] else None
        return min(100.0, 100.0 * q / q_open)

    def _update_averages(self, s, inst, dt_s):
        """Flow / pressure averaging: first-order running averages of the readings with the set time constants (0 ms = off); between host
        steps the averaged readings are held."""
        raw = _amc_copy.deepcopy(s)
        self._readings(raw, inst)
        store, st = inst.setdefault("averages", {}), s["device.settings"]
        for sid, key in (("measurement.standard_flow_m3_s", "flow_averaging_ms"), ("measurement.actual_flow_m3_s", "flow_averaging_ms"),
                         ("measurement.mass_flow_kg_s", "flow_averaging_ms"), ("measurement.pressure_pa", "pressure_averaging_ms")):
            tau, value, previous = st[key] / 1000.0, raw[sid], store.get(sid)
            if tau <= 0 or value is None:
                store.pop(sid, None)
            else:
                decay = _amc_math.exp(-dt_s / tau)
                store[sid] = value if previous is None or decay < 1e-12 else value + (previous - value) * decay

    def _readings(self, s, inst=None):
        for sid in _AMC_MEASUREMENTS:
            s[sid] = False if sid == "measurement.accuracy_conditions_met" else None
        p, temp, lo, hi = s["truth.upstream_pressure_pa"], s["truth.temperature_k"], *ALICAT_MC_TEMPERATURE_RANGE_K
        s["device.alarms"] = [] if not s["power.enabled"] else \
            (["ADC"] if s["truth.adc_fault"] else []) + (["POV"] if _amc_number(p) and p > ALICAT_MC_FS_PRESSURE_PA else []) + \
            (["TOV"] if _amc_number(temp) and not lo <= temp <= hi else [])          # status messages of the main display (manual p.6)
        if not s["power.enabled"] or s["gas.selected_id"] is None or not self._boundaries_valid(s):
            return
        s["measurement.pressure_pa"] = s["truth.upstream_pressure_pa"]
        s["measurement.temperature_k"] = s["truth.temperature_k"]
        q = s["truth.standard_flow_m3_s"]
        if not s["flow.zeroed"] or q is None:
            return
        if _amc_template_backed(self, s, inst):  # template hook: library gas (NIST real-gas density, LFE viscosity)
            return _amc_templates().refresh_readings(self, s, inst)
        s["measurement.standard_flow_m3_s"] = q
        s["measurement.actual_flow_m3_s"] = _amc_finite_result(q * (s["truth.temperature_k"] / s["gas.reference_temperature_k"]) * (s["gas.reference_pressure_pa"] / s["truth.upstream_pressure_pa"]))
        molar_mass = self._gases[s["gas.selected_id"]]["molar_mass_kg_mol"]
        if molar_mass is not None:
            s["measurement.mass_flow_kg_s"] = _amc_finite_result(q * s["gas.reference_pressure_pa"] * molar_mass / (ALICAT_MC_R * s["gas.reference_temperature_k"]))
        s["measurement.total_standard_volume_m3"] = s["truth.total_standard_volume_m3"]
        if s["truth.equilibrium"]:
            s["measurement.accuracy_conditions_met"] = True
            s["measurement.standard_accuracy_bound_m3_s"] = max(0.006 * abs(q), 0.001 * ALICAT_MC_FS_M3_S)

    def host_connect_ports(self, instance_id, *, upstream_node_id, downstream_node_id):
        with self._lock:
            inst = self._require(instance_id)
            if any(not isinstance(v, str) or not v.strip() for v in (upstream_node_id, downstream_node_id)) or upstream_node_id == downstream_node_id:
                raise ValueError("Two distinct nonempty gas-network node IDs are required")
            s = _amc_copy.deepcopy(inst["values"])
            if s["ports.upstream_node_id"] == upstream_node_id and s["ports.downstream_node_id"] == downstream_node_id:
                return
            if not self._stopped_for_configuration(s):
                raise ValueError("Stop flow before reconnecting topology")
            s.update({"ports.upstream_node_id": upstream_node_id, "ports.downstream_node_id": downstream_node_id,
                "flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None,
                "truth.upstream_pressure_pa": None, "truth.downstream_pressure_pa": None, "truth.temperature_k": None,
                "truth.no_flow": False, "truth.equilibrium": False})
            self._commit(inst, s)

    def host_set_boundary(self, instance_id, *, upstream_pressure_Pa, downstream_pressure_Pa, temperature_K, equilibrium, no_flow):
        with self._lock:
            inst = self._require(instance_id)
            if any(not _amc_number(v) or v <= 0 for v in (upstream_pressure_Pa, downstream_pressure_Pa, temperature_K)) or type(equilibrium) is not bool or type(no_flow) is not bool:
                raise ValueError("Positive finite SI boundaries and boolean host assertions required")
            s = _amc_copy.deepcopy(inst["values"])
            s.update({"truth.upstream_pressure_pa": float(upstream_pressure_Pa), "truth.downstream_pressure_pa": float(downstream_pressure_Pa),
                "truth.temperature_k": float(temperature_K), "truth.equilibrium": equilibrium, "truth.no_flow": no_flow})
            if no_flow:
                s["truth.standard_flow_m3_s"] = 0.0
            elif not self._boundaries_valid(s) or upstream_pressure_Pa <= downstream_pressure_Pa:
                s["truth.standard_flow_m3_s"] = None
                s["truth.total_standard_volume_m3"] = None
            self._commit(inst, s)

    def host_configure_dynamics(self, instance_id, *, time_constant_s, evidence):
        with self._lock:
            inst = self._require(instance_id)
            if not _amc_number(time_constant_s) or time_constant_s <= 0 or not isinstance(evidence, str) or not evidence.strip():
                raise ValueError("A positive finite assumed tau and explicit evidence are required")
            s = _amc_copy.deepcopy(inst["values"])
            if not self._stopped_for_configuration(s):
                raise ValueError("Configure dynamics only while flow is stopped")
            s["truth.response_time_constant_s"] = float(time_constant_s)
            s["simulation.dynamic_model_configured"] = True
            inst["dynamics_evidence"] = evidence
            self._commit(inst, s)

    def host_step(self, dt_s):
        """One common host time step; no gas inventory transaction is asserted."""
        if not _amc_number(dt_s) or not 0 <= dt_s <= 3600:
            raise ValueError("Host scheduling dt_s must be finite in [0,3600]")
        if dt_s == 0:
            return
        with self._lock:
            pending = []
            for inst in self._instances.values():
                s = _amc_copy.deepcopy(inst["values"])
                old, tau = s["truth.standard_flow_m3_s"], s["truth.response_time_constant_s"]
                ready = s["power.enabled"] and s["gas.selected_id"] is not None and s["flow.zeroed"] and self._boundaries_valid(s)
                templated = ready and _amc_template_backed(self, s, inst)  # template hook
                factor = _amc_templates().indication_factor(self, s, inst) if templated else 1.0
                ready = ready and factor is not None  # condensed / outside library data: flow undefined
                self._apply_analog(s, inst)
                segments = self._setpoint_segments(s, dt_s)
                conv = self._volumetric_conversion(s, inst) if ready and s["device.settings"]["control_variable"] == "volumetric_flow" else 1.0
                valve = s["device.settings"]["control_variable"] == "valve_drive"
                if ready and s["truth.no_flow"]:
                    s["truth.standard_flow_m3_s"] = 0.0
                elif ready and valve and tau is not None and old is not None and s["truth.upstream_pressure_pa"] > s["truth.downstream_pressure_pa"]:
                    # open loop: the drive setpoint sets the linear valve, the flow follows with the host tau (loop gains bypassed)
                    target = s["valve.drive_setpoint_percent"] / 100.0 * self._open_flow(s) * factor
                    s["truth.standard_flow_m3_s"], integral = self._integrate(s, old, tau, factor, 1.0, [(dt_s, target, 0.0)])
                    if s["truth.total_standard_volume_m3"] is not None:
                        if templated:
                            _amc_templates().totalize(inst, s["truth.total_standard_volume_m3"], integral, factor)
                        s["truth.total_standard_volume_m3"] += integral
                elif ready and conv is not None and tau is not None and old is not None and s["truth.upstream_pressure_pa"] > s["truth.downstream_pressure_pa"]:
                    # controller servos on the indicated (k * true) flow; k=1.0 legacy
                    s["truth.standard_flow_m3_s"], integral = self._integrate(s, old, self._loop_tau(s, tau), factor, conv, segments, self._open_flow(s))
                    if s["truth.total_standard_volume_m3"] is not None:
                        if templated:
                            _amc_templates().totalize(inst, s["truth.total_standard_volume_m3"], integral, factor)
                        s["truth.total_standard_volume_m3"] += integral
                else:
                    s["truth.standard_flow_m3_s"] = None
                    s["truth.total_standard_volume_m3"] = None
                if s["power.enabled"]:
                    end = segments[-1][1] + segments[-1][2] * segments[-1][0] if segments[-1][2] else segments[-1][1]
                    s["flow.active_setpoint_m3_s"] = min(max(end, 0.0), ALICAT_MC_FS_M3_S)
                    if s["truth.total_standard_volume_m3"] is not None:                     # totalizer timer (freezes at 9999:59:59)
                        s["totalizer.elapsed_s"] = min(s["totalizer.elapsed_s"] + dt_s, float(ALICAT_MC_TIMER_MAX_S))
                self._autotare(s, inst, dt_s)
                self._update_averages(s, inst, dt_s)
                self._refresh(s, inst)
                pending.append((inst, s))
            self.time_s += dt_s
            for inst, s in pending:
                self._commit(inst, s)

    # ---- device functions behind the front panel (setpoint path, ramp, batch, autotare)
    @staticmethod
    def _ramp_rate(s):
        st = s["device.settings"]
        return st["ramp_delta_m3_s"] / st["ramp_time_s"] if st["ramp_delta_m3_s"] > 0 else 0.0

    def _ramps(self, s, active, target):
        """Does a change of the active setpoint to target honour the ramp rate (manual p.16: Ramp Up / Ramp Down, 0 Setpt)?"""
        st = s["device.settings"]
        if self._ramp_rate(s) <= 0 or target == active:
            return False
        if target > active:
            return st["ramp_up"]
        return st["ramp_zero_setpoint"] if target == 0 else st["ramp_down"]

    def _setpoint_segments(self, s, dt_s):
        """The active setpoint over a host step: [(duration, start, slope)], ramping toward the setpoint at the maximum rate."""
        active, target = s["flow.active_setpoint_m3_s"], s["flow.setpoint_m3_s"]
        if not s["power.enabled"]:
            return [(dt_s, active, 0.0)]
        if not self._ramps(s, active, target):
            return [(dt_s, target, 0.0)]
        rate = self._ramp_rate(s)
        slope, t_reach = (rate if target > active else -rate), abs(target - active) / rate
        if t_reach >= dt_s:
            return [(dt_s, active, slope)]
        return [(t_reach, active, slope), (dt_s - t_reach, target, 0.0)]

    def _volumetric_conversion(self, s, inst):
        """Standard flow per actual (inlet) flow of the selected gas, for volumetric flow control; None when the properties are unavailable."""
        if _amc_template_backed(self, s, inst) and s["gas.selected_id"] not in self._gases:
            t = _amc_templates()
            users = t.user_records(s)
            actual = t.gas_state(s["gas.selected_id"], s["truth.temperature_k"], s["truth.upstream_pressure_pa"], users)
            standard = t.gas_state(s["gas.selected_id"], s["gas.reference_temperature_k"], s["gas.reference_pressure_pa"], users)
            return None if actual is None or standard is None else actual["density_kg_m3"] / standard["density_kg_m3"]
        return _amc_finite_result((s["gas.reference_temperature_k"] / s["truth.temperature_k"]) * (s["truth.upstream_pressure_pa"] / s["gas.reference_pressure_pa"]))

    def _integrate(self, s, old, tau, factor, conv, segments, q_open=None):
        """Exact first-order response x' = (target(t) - x) / tau over the active-setpoint segments (a ramp makes the target linear in time); the
        controller servos on the indicated flow (k = factor).  With a batch the valve closes (target 0) when the indicated total reaches it
        (manual p.12): the crossing inside a segment is found by bisection.  Returns (flow at the end of the step, integral of the flow)."""
        x, total = old, 0.0
        if tau is None:                                                                      # P (and I) gain 0: the controller does not correct
            return x, x * sum(seg[0] for seg in segments)
        batch, shown = s["totalizer.batch_size_m3"], s["measurement.total_standard_volume_m3"]
        left = None if batch is None or shown is None else (batch - shown) / factor        # true standard volume still to dispense
        queue = list(segments)
        while queue:
            duration, start, slope = queue.pop(0)
            if left is not None and left <= 0:
                target, slope_x = 0.0, 0.0                                                   # batch done: the valve stays closed
            elif conv == 1.0:
                target, slope_x = start / factor, slope / factor
            else:
                target, slope_x = start * conv / factor, slope * conv / factor
            if q_open is not None:                                                           # 100 % valve drive passes at most q_open
                if target >= q_open:
                    target, slope_x = q_open, 0.0
                elif slope_x > 0 and target + slope_x * duration > q_open:
                    t_c = (q_open - target) / slope_x
                    queue.insert(0, (duration - t_c, start + slope * t_c, slope))
                    duration = t_c
            end, integral = _amc_lag(x, target, slope_x, tau, duration)
            if left is not None and left > 0 and integral >= left:
                lo, hi = 0.0, duration
                for _ in range(80):
                    mid = (lo + hi) / 2.0
                    lo, hi = (mid, hi) if _amc_lag(x, target, slope_x, tau, mid)[1] < left else (lo, mid)
                end, integral = _amc_lag(x, target, slope_x, tau, hi)
                queue.insert(0, (duration - hi, start + slope * hi, slope))
            x, total = end, total + integral
            if left is not None:
                left -= integral
        return x, total

    def _new_setpoint(self, s, inst, value):
        """A new setpoint from the front panel, the API, the analog input or power-up: zero-setpoint time (autotare) and the active setpoint."""
        if s["flow.setpoint_m3_s"] != value:
            s["truth.equilibrium"] = False
            if value == 0:
                inst["zero_since_s"], inst["autotare_done"] = self.time_s, False
        if value != 0:
            inst["zero_since_s"] = None
        s["flow.setpoint_m3_s"] = float(value)
        if not self._ramps(s, s["flow.active_setpoint_m3_s"], s["flow.setpoint_m3_s"]):
            s["flow.active_setpoint_m3_s"] = s["flow.setpoint_m3_s"]

    def _apply_analog(self, s, inst):
        """Setpoint source Analog (manual pp.13-14): 0-5 V is 0-100 % of full scale; outside the setpoint limits it is held at the nearest limit."""
        st = s["device.settings"]
        if st["setpoint_source"] != "analog" or not s["power.enabled"]:
            return
        if st["control_variable"] == "valve_drive":                           # 0-5 V is 0-100 % drive, within the limits as % of full scale
            lo, hi = (100.0 * st[k] / ALICAT_MC_FS_M3_S for k in ("setpoint_min_m3_s", "setpoint_max_m3_s"))
            s["valve.drive_setpoint_percent"] = min(max(s["setpoint.analog_input_v"] / ALICAT_MC_ANALOG_FS_V * 100.0, lo), hi)
            return
        value = min(max(s["setpoint.analog_input_v"] / ALICAT_MC_ANALOG_FS_V * ALICAT_MC_FS_M3_S, st["setpoint_min_m3_s"]), st["setpoint_max_m3_s"])
        self._new_setpoint(s, inst, 0.0 if value < ALICAT_MC_MIN_M3_S else value)

    def _autotare(self, s, inst, dt_s):
        """Autotare (manual p.10, p.13): a zero setpoint held for the delay tares the flow reading once, at a host-confirmed no-flow equilibrium."""
        st, since = s["device.settings"], inst.get("zero_since_s")
        if not (st["autotare"] and s["power.enabled"] and s["flow.setpoint_m3_s"] == 0 and since is not None and not inst.get("autotare_done")):
            return
        if self.time_s + dt_s - since + 1e-9 >= st["autotare_delay_s"] and s["gas.selected_id"] is not None and s["truth.no_flow"] \
                and s["truth.equilibrium"] and self._boundaries_valid(s):
            s["flow.zeroed"], s["truth.standard_flow_m3_s"] = True, 0.0
            inst["autotare_done"] = True

    def _power_up(self, s, inst):
        """Power-up (manual p.13, p.16, p.22): last or fixed setpoint, any ramp starting from zero (honoured or jumped), backlight lit or dark."""
        st = s["device.settings"]
        inst["power_epoch"] += 1
        s["display.backlight_on"] = st["power_up_backlight"]
        s["totalizer.elapsed_s"] = 0.0
        value = float(st["power_up_setpoint_m3_s"] if st["power_up_setpoint_mode"] == "fixed" else s["flow.setpoint_m3_s"])
        if value != s["flow.setpoint_m3_s"]:
            s["truth.equilibrium"] = False
        s["flow.setpoint_m3_s"] = value
        inst["zero_since_s"], inst["autotare_done"] = (self.time_s if value == 0 else None), False
        s["flow.active_setpoint_m3_s"] = 0.0 if st["ramp_power_up"] and self._ramps(s, 0.0, value) else value
        self._apply_analog(s, inst)

    def host_set_analog_setpoint_input(self, instance_id, *, volts):
        """Trusted host: the signal on the analog setpoint pin (0-5 V); the setpoint follows it while the setpoint source is Analog."""
        with self._lock:
            inst = self._require(instance_id)
            if not _amc_number(volts) or not 0 <= volts <= ALICAT_MC_ANALOG_FS_V:
                raise ValueError("Analog setpoint input must be finite in 0..5 V")
            s = _amc_copy.deepcopy(inst["values"])
            s["setpoint.analog_input_v"] = float(volts)
            self._apply_analog(s, inst)
            self._commit(inst, s)

    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame); created on first use."""
        self._require(instance_id)
        if instance_id not in self._panels:
            self._panels[instance_id] = _amc_panel()["engine"].PanelUI(_amc_panel()["spec"], _AlicatPanelHost(self, instance_id))
        return self._panels[instance_id]

    def debug_snapshot(self):
        with self._lock:
            return _amc_copy.deepcopy({"time_s": self.time_s, "instances": self._instances, "gases": self._gases})


class AlicatMCSession:
    """Filtered public API. Identity is fixed by the trusted host registry."""
    def __init__(self, host, principal_id):
        self.__host = host
        self.__principal_id = principal_id

    def _instance(self, instance_id):
        if not isinstance(instance_id, str):
            return None
        inst = self.__host._instances.get(instance_id)
        _, scope = self.__host._principals[self.__principal_id]
        return inst if inst and (scope is None or instance_id in scope) else None

    def _visible(self, sid):
        role, _ = self.__host._principals[self.__principal_id]
        return sid in _AMC_SLOTS and (_AMC_SLOTS[sid]["visibility"] == "agent" or role in ("debug", "evaluator"))

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id if isinstance(instance_id, str) else None,
            "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None, "status": status,
            "state_revision": inst["revision"] if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": {} if result is None else _amc_copy.deepcopy(result), "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Instance is unavailable in this session.", "UNKNOWN_CAPABILITY": "Capability is unavailable for this asset.",
            "INVALID_ARGUMENT": "Arguments do not meet the declared schema or limits.", "PRECONDITION_FAILED": "Required operating configuration or state is unavailable.",
            "ACCESS_DENIED": "Requested operation is outside the session access policy.", "REQUEST_CONFLICT": "Request ID already identifies a different logical request.",
            "UNSUPPORTED": "This capability has no executable implementation."}
        return self._envelope(instance_id, request_id, status=status,
            error={"code": code, "message": messages[code], "retryable": False, "side_effects": "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None:
                if self._instance(instance_id) is None:
                    return self._error(instance_id, "UNKNOWN_INSTANCE")
                asset_type_id = ALICAT_MC_TYPE
            if asset_type_id != ALICAT_MC_TYPE:
                return self._error(instance_id, "INVALID_ARGUMENT")
            _, scope = self.__host._principals[self.__principal_id]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = export_alicat_mc_contract_metadata()["asset_types"][ALICAT_MC_TYPE]
            _amc_templates().augment_describe(spec)  # template hook: library queries + gas.select library IDs
            role, _ = self.__host._principals[self.__principal_id]
            spec["capabilities"].update({k: v for k, v in spec.pop("panel_capabilities").items() if v["visibility"] == "agent" or role in ("debug", "evaluator")})
            spec["capabilities"] = {k: v for k, v in spec["capabilities"].items() if v.get("visibility", "agent") == "agent" or role in ("debug", "evaluator")}
            spec["state_slots"] = {sid: value for sid, value in spec["state_slots"].items() if self._visible(sid)}
            # Capability effects are metadata too: hide restricted slot identifiers.
            for cap in spec["capabilities"].values():
                cap["state_effects"] = [e for e in cap["state_effects"] if self._visible(e["state_id"])]
            spec.update({"protocol_version": "1.0.0", "asset_type_id": ALICAT_MC_TYPE, "instance_id": instance_id, "adapter": "basic40_runtime_automation.AlicatMCSession"})
            return spec

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if state_ids is None:
                state_ids = [sid for sid in _AMC_SLOTS if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(set(state_ids)) != len(state_ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(not self._visible(sid) for sid in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            observations = {}
            for sid in state_ids:
                value = _amc_copy.deepcopy(inst["values"][sid])
                observations[sid] = {"value": value, "unit": _AMC_SLOTS[sid]["unit"],
                    "quality": "unavailable" if value is None else "estimated" if _AMC_SLOTS[sid]["origin"] == "derived" else "valid",
                    "sample_time_s": self.__host.time_s, "sample_window_s": None}
                if value is None:
                    observations[sid]["reason"] = "Required configuration, supported process model or property evidence is unavailable."
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id or len(request_id) > 256:
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = _amc_hashlib.sha256(_amc_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError, RecursionError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal_id, instance_id, request_id)
            if key in self.__host._requests:
                previous_digest, response = self.__host._requests[key]
                return _amc_copy.deepcopy(response) if digest == previous_digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._invoke_checked(inst, capability_id, arguments, request_id)
            self.__host._requests[key] = (digest, _amc_copy.deepcopy(response))
            self.__host._logs[self.__principal_id].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": _amc_copy.deepcopy(response)})
            return response

    def _invoke_checked(self, inst, cid, args, request_id):
        sid = inst["instance_id"]
        fail = lambda code, status="rejected": self._error(sid, code, request_id, status)
        exported = export_alicat_mc_contract_metadata()["asset_types"][ALICAT_MC_TYPE]
        metadata = exported["capabilities"]
        if cid in _AMC_TEMPLATE_CAPABILITIES:  # template hook: read-only public library queries
            return _amc_templates().invoke(self, self.__host, inst, cid, args, request_id)
        if cid in exported["panel_capabilities"]:
            return self._invoke_panel(inst, cid, args, request_id)
        if cid not in metadata:
            return fail("UNKNOWN_CAPABILITY")
        if cid in _AMC_FAULT_CAPABILITIES:                                   # evaluator: the ADC status message (manual p.6)
            if self.__host._principals[self.__principal_id][0] not in ("debug", "evaluator"):
                return fail("ACCESS_DENIED")
            if not _amc_valid(metadata[cid]["input_schema"], args):
                return fail("INVALID_ARGUMENT")
            s = _amc_copy.deepcopy(inst["values"])
            if cid == "fault.inject" and s["truth.adc_fault"]:
                return fail("PRECONDITION_FAILED")
            s["truth.adc_fault"] = cid == "fault.inject"
            self.__host._commit(inst, s)
            return self._envelope(sid, request_id, result={"device.alarms": _amc_copy.deepcopy(inst["values"]["device.alarms"])})
        if cid == "control.set_pid":
            return fail("UNSUPPORTED", "unsupported")
        props = metadata[cid]["input_schema"]["properties"]
        if cid in _AMC_PANEL_DEVICE_CAPABILITIES:
            if not _amc_valid(metadata[cid]["input_schema"], args):
                return fail("INVALID_ARGUMENT")
            props = {}
        elif set(args) != set(props):
            return fail("INVALID_ARGUMENT")
        for name, spec in props.items():
            value = args[name]
            if spec["type"] == "number" and (not _amc_number(value) or ("minimum" in spec and value < spec["minimum"]) or ("maximum" in spec and value > spec["maximum"]) or ("exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"])):
                return fail("INVALID_ARGUMENT")
            if spec["type"] == "boolean" and type(value) is not bool:
                return fail("INVALID_ARGUMENT")
            if spec["type"] == "string" and (not isinstance(value, str) or not value.strip()):
                return fail("INVALID_ARGUMENT")
        s = _amc_copy.deepcopy(inst["values"])
        if cid not in ("power.set", "flow.read") and not s["power.enabled"]:
            return fail("PRECONDITION_FAILED")
        if cid in ("gas.select", "gas.set_reference_conditions") and not self.__host._stopped_for_configuration(s):
            return fail("PRECONDITION_FAILED")
        host = self.__host
        if cid in ("device.factory_restore",) and not host._stopped_for_configuration(s):
            return fail("PRECONDITION_FAILED")
        if cid == "power.set":
            was = s["power.enabled"]
            s["power.enabled"] = args["enabled"]
            if not args["enabled"]:
                s.update({"flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None})
                s["totalizer.elapsed_s"] = 0.0
            elif not was:
                host._power_up(s, inst)
        elif cid == "gas.select":
            mixes = {_amc_templates().composer_id(m["mix_number"]) for m in s["gas.user_mixes"]}
            if args["gas_id"] not in host._gases and args["gas_id"] not in mixes and not _amc_templates().is_selectable(host, args["gas_id"]):  # template hook
                return fail("PRECONDITION_FAILED")
            s["gas.selected_id"] = args["gas_id"]
            s.update({"flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None})
            s["gas.recent_ids"] = ([args["gas_id"]] + [g for g in s["gas.recent_ids"] if g != args["gas_id"]])[:ALICAT_MC_RECENT]
        elif cid == "gas.set_reference_conditions":
            s["gas.reference_temperature_k"], s["gas.reference_pressure_pa"] = float(args["temperature_K"]), float(args["pressure_Pa"])
            s.update({"flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None})
        elif cid == "flow.set_setpoint":
            value = args["flow_rate_m3_s"]
            if 0 < value < ALICAT_MC_MIN_M3_S:
                return fail("INVALID_ARGUMENT")
            if s["gas.selected_id"] is None:
                return fail("PRECONDITION_FAILED")
            st = s["device.settings"]
            if st["setpoint_source"] == "analog" or not st["setpoint_min_m3_s"] <= value <= st["setpoint_max_m3_s"] or st["control_variable"] == "valve_drive":
                return fail("PRECONDITION_FAILED")                     # analog source ignores serial setpoints; setpoint limits (manual pp.13-14)
            host._new_setpoint(s, inst, value)
        elif cid == "flow.zero":
            if not (s["gas.selected_id"] is not None and s["flow.setpoint_m3_s"] == 0 and s["truth.no_flow"] and s["truth.equilibrium"] and self.__host._boundaries_valid(s)):
                return fail("PRECONDITION_FAILED")
            s["flow.zeroed"] = True
            s["truth.standard_flow_m3_s"] = 0.0
        elif cid == "flow.reset_total":
            s["truth.total_standard_volume_m3"] = 0.0
            s["totalizer.elapsed_s"] = 0.0
        elif cid in _AMC_PANEL_DEVICE_CAPABILITIES:
            outcome = self._device_command(inst, s, cid, args)
            if outcome is not None:
                return fail(*outcome)
        self.__host._commit(inst, s)
        result = {key: inst["values"][key] for key in metadata[cid]["output_schema"]["properties"]}
        return self._envelope(sid, request_id, result=result)

    def _device_command(self, inst, s, cid, args):
        """The device functions behind the front panel; returns (code, status) when refused (state unchanged), else None."""
        host, templates = self.__host, _amc_templates()
        if cid == "menu.configure":
            settings = dict(s["device.settings"], **_amc_copy.deepcopy(args))
            if settings["setpoint_min_m3_s"] > settings["setpoint_max_m3_s"]:
                return ("PRECONDITION_FAILED", "rejected")
            if args.get("control_variable") in _AMC_UNSUPPORTED_CONTROL:
                return ("UNSUPPORTED", "unsupported")
            before = s["device.settings"]["control_variable"]
            s["device.settings"] = settings
            if before != settings["control_variable"] and "valve_drive" in (before, settings["control_variable"]):
                if settings["control_variable"] == "valve_drive":             # the setpoint keeps its percentage of full scale (assumption, p.13)
                    s["valve.drive_setpoint_percent"] = 100.0 * s["flow.setpoint_m3_s"] / ALICAT_MC_FS_M3_S
                else:
                    flow = s["valve.drive_setpoint_percent"] / 100.0 * ALICAT_MC_FS_M3_S
                    host._new_setpoint(s, inst, 0.0 if flow < ALICAT_MC_MIN_M3_S else min(max(flow, settings["setpoint_min_m3_s"]), settings["setpoint_max_m3_s"]))
                s["truth.equilibrium"] = False
            if {"flow_averaging_ms", "pressure_averaging_ms"} & set(args):
                inst["averages"] = {}                                       # a new time constant restarts the running averages
            if not host._ramps(s, s["flow.active_setpoint_m3_s"], s["flow.setpoint_m3_s"]):
                s["flow.active_setpoint_m3_s"] = s["flow.setpoint_m3_s"]
            host._apply_analog(s, inst)
        elif cid == "valve.set_drive_setpoint":
            st = s["device.settings"]
            lo, hi = (100.0 * st[k] / ALICAT_MC_FS_M3_S for k in ("setpoint_min_m3_s", "setpoint_max_m3_s"))
            if st["control_variable"] != "valve_drive" or st["setpoint_source"] == "analog" or not lo - 1e-9 <= args["percent"] <= hi + 1e-9:
                return ("PRECONDITION_FAILED", "rejected")
            if s["valve.drive_setpoint_percent"] != args["percent"]:
                s["truth.equilibrium"] = False
            s["valve.drive_setpoint_percent"] = float(args["percent"])
        elif cid == "totalizer.set_batch":
            size = args["batch_size_m3"]
            s["totalizer.batch_size_m3"] = None if not size else float(size)    # CLEAR then SET (0) removes the batch: -NONE-
        elif cid == "display.set_backlight":
            s["display.backlight_on"] = args["on"]
        elif cid == "display.set_lock":
            s["display.locked"] = args["locked"]
        elif cid == "gas.create_mix":
            mixes = [m for m in s["gas.user_mixes"]]
            number = args["mix_number"] or next((n for n in ALICAT_MC_MIX_NUMBERS if n not in {m["mix_number"] for m in mixes}), None)
            parts = args["components"]
            gases = [p["gas_id"] for p in parts]
            hundredths = [p["percent"] * 100.0 for p in parts]
            if number is None or s["gas.selected_id"] == templates.composer_id(number) or len(set(gases)) != len(gases) \
                    or any(not templates.is_selectable(host, g) for g in gases) or any(abs(h - round(h)) > 1e-6 for h in hundredths) \
                    or sum(round(h) for h in hundredths) != 10000:
                return ("PRECONDITION_FAILED", "rejected")
            mix = {"mix_number": number, "short_name": args["short_name"], "long_name": args["long_name"],
                   "components": [{"gas_id": g, "percent": round(h) / 100.0} for g, h in zip(gases, hundredths)]}
            s["gas.user_mixes"] = sorted([m for m in mixes if m["mix_number"] != number] + [mix], key=lambda m: -m["mix_number"])
        elif cid == "gas.delete_mix":
            gas_id = templates.composer_id(args["mix_number"])
            if args["mix_number"] not in {m["mix_number"] for m in s["gas.user_mixes"]} or s["gas.selected_id"] == gas_id:
                return ("PRECONDITION_FAILED", "rejected")
            s["gas.user_mixes"] = [m for m in s["gas.user_mixes"] if m["mix_number"] != args["mix_number"]]
            s["gas.recent_ids"] = [g for g in s["gas.recent_ids"] if g != gas_id]
        elif cid == "device.factory_restore":
            s["device.settings"] = {k: _amc_copy.deepcopy(v[1]) for k, v in _AMC_SETTINGS.items()}
            s.update({"gas.reference_temperature_k": 298.15, "gas.reference_pressure_pa": 101325.0, "totalizer.batch_size_m3": None,
                      "flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None})
            if templates.is_selectable(host, "mix_air"):
                s["gas.selected_id"] = "mix_air"
            s["flow.active_setpoint_m3_s"] = s["flow.setpoint_m3_s"]
            inst["averages"] = {}
        return None

    def _invoke_panel(self, inst, cid, args, request_id):
        """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        sid, panel, host = inst["instance_id"], _amc_panel(), self.__host
        definition = panel["caps"][cid]
        role, _ = host._principals[self.__principal_id]
        if definition["visibility"] != "agent" and role not in ("debug", "evaluator"):
            return self._error(sid, "ACCESS_DENIED", request_id)
        if panel["engine"].check_arguments(definition["input_schema"], args):
            return self._error(sid, "INVALID_ARGUMENT", request_id)
        ui = host.panel(sid)
        try:
            if cid == "panel.frame":
                return self._envelope(sid, request_id, result={"frame": ui.frame()})
            if cid == "panel.capture":
                data, width, height = ui.png_bytes(args["display_id"])
                digest = _amc_hashlib.sha256(data).hexdigest()
                folder = _amc_Path(host.panel_capture_dir) if host.panel_capture_dir else _amc_Path(_amc_tempfile.gettempdir()) / "chemistry_sim_panel" / sid
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ("%s_%s.png" % (args["display_id"], digest[:16]))
                if not path.exists():
                    path.write_bytes(data)
                return self._envelope(sid, request_id, result={"display_id": args["display_id"], "time_s": host.time_s,
                                                               "image": {"path": str(path), "format": "png", "width": width, "height": height, "sha256": digest}})
            if cid == "panel.press":
                accepted = ui.press(args["input_id"], float(args.get("duration_s", 0.1)), held=args.get("held", ()))
            elif cid == "panel.switch":
                accepted = ui.switch(args["input_id"], args["position"], held=args.get("held", ()))
            else:
                accepted = ui.turn(args["input_id"], int(args["detents"]))
        except panel["engine"].PanelError:
            return self._error(sid, "INVALID_ARGUMENT", request_id)
        return self._envelope(sid, request_id, result={"accepted": bool(accepted)})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(state_id, str) or not self._visible(state_id) or _AMC_SLOTS[state_id]["access"] != "read_write":
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = _AMC_SLOTS[state_id]["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _amc_copy.deepcopy(self.__host._logs[self.__principal_id])


def _amc_lag(x0, target, slope, tau, t):
    """First-order lag x' = (target + slope * t - x) / tau from x0 over t: (x(t), integral of x); slope 0 is the original constant-target form."""
    response = -_amc_math.expm1(-t / tau)
    if slope == 0:
        integral = x0 * t + (target - x0) * (t - tau * response)
        return max(0.0, x0 + (target - x0) * response), min(max(integral, min(x0, target) * t), max(x0, target) * t)
    c = x0 - target + slope * tau
    x = target + slope * t - slope * tau + c * (1.0 - response)
    return max(0.0, x), max(0.0, target * t + slope * t * t / 2.0 - slope * tau * t + c * tau * response)


class _AlicatPanelHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py for one instance: what the front-panel firmware reads, calls and times by."""
    def __init__(self, runtime, instance_id):
        self.runtime, self.instance_id = runtime, instance_id

    def panel_read(self):
        inst = self.runtime._instances[self.instance_id]
        out = {sid: _amc_copy.deepcopy(v) for sid, v in inst["values"].items() if _AMC_SLOTS[sid]["visibility"] == "agent"}
        out.update({"host.powered": inst["values"]["power.enabled"], "host.power_epoch": inst["power_epoch"]})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action takes the checked path of a direct call (same preconditions and revision)."""
        inst = self.runtime._instances[self.instance_id]
        response = self.runtime._panel_session._invoke_checked(inst, capability_id, dict(arguments), None)
        if response["status"] != "succeeded":
            raise _amc_panel()["engine"].PanelRejected(response["error"]["code"])
        return response["result"]

    def panel_time(self):
        return self.runtime.time_s
# END CHEM-180 ALICAT MC APPEND-ONLY RUNTIME

# CHEM-181 P-702CV batch-scope runtime; appended as a self-contained namespace.
import copy as _p702cv_copy
import json as _p702cv_json
import math as _p702cv_math
import threading as _p702cv_threading
import hashlib as _p702cv_hashlib
import tempfile as _p702cv_tempfile
from pathlib import Path as _p702cv_Path

P702CV_ASSET_TYPE_ID = 'instrument.bronkhorst_p702cv_5bar_gauge_n2'
P702CV_SENSOR_FULL_SCALE_PA = 500000.0
P702CV_MEASUREMENT_MIN_PA = 10000.0
P702CV_CONTROL_MIN_PA = 100000.0
P702CV_GAS_CONSTANT = 8.31446261815324
# Multifunctional switch, status LEDs and the instrument functions behind them (EL-PRESS manual 9.17.101 rev. O, PDF pages):
P702CV_ASSET_DIR = _p702cv_Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t5_automation/bronkhorst_p702cv'
P702CV_CONTROL_MODES = {0: 'bus_rs232', 1: 'analog_input', 3: 'valve_close', 4: 'controller_idle', 7: 'setpoint_100_percent', 8: 'valve_fully_open',
                        10: 'analog_slave', 12: 'setpoint_0_percent', 18: 'rs232', 20: 'valve_steering', 21: 'analog_valve_steering',
                        22: 'valve_safe_state'}           # p.34; 2 / 13 (FLOW-BUS slave) need the absent FLOW-BUS, 9 is the factory calibration mode
P702CV_AUTOMATIC_MODES = (0, 1, 7, 10, 12, 18)          # the PID controller runs (setpoint source per p.34)
P702CV_VOLATILE_MODES = (0, 1, 9, 18)                   # back to the default control mode at the next power-up or reset (p.34)
P702CV_FACTORY_CONTROL_MODE = 0                         # default 0 or 1 'as ordered' (p.34): digital (RS-232) assumed for this asset
P702CV_INITIAL_CONTROL_MODE = 20                        # valve steering: the runtime's documented start (manual valve opening, closed)
P702CV_ZERO_SETPOINT_PA = 0.02 * P702CV_SENSOR_FULL_SCALE_PA   # setpoints below 2 % of full scale count as 0 % (p.28): valve closed (p.20)
P702CV_ALARM_KINDS = ['minimum', 'maximum', 'response', 'power_up']
P702CV_ALARM_DEFAULTS = {'alarm.mode': 'off', 'alarm.min_limit_pa': 0.0, 'alarm.max_limit_pa': 0.0, 'alarm.delay_s': 0, 'alarm.new_setpoint_pa': None,
                         'alarm.reset_enable': {'switch': True, 'command': True, 'automatic': True}}     # p.38-40 defaults
_P702CV_PANEL = {}


P702CV_AUTOZERO_BAND_PA = 0.02 * P702CV_SENSOR_FULL_SCALE_PA   # assumption: a zeroing signal within +-2 % FS of the factory zero is accepted, else 255
P702CV_SENSOR_DEFAULTS = {'sensor.zero_offset_pa': 0.0, 'sensor.calibration_mode': 0, 'sensor.offset_error_pa': 0.0, 'sensor.gain_error': 0.0,
                          'sensor.drift_error_pa_s': 0.0, 'sensor.drift_offset_pa': 0.0}   # sensor error ideal (0) by default; set by the evaluator only


def _p702cv_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return _p702cv_math.isfinite(value)
    except (ValueError, OverflowError):
        return False


def _p702cv_num(unit, minimum=None, maximum=None, nullable=False):
    s = {'type': 'number', 'x-unit': unit}
    if minimum is not None:
        s['minimum'] = minimum
    if maximum is not None:
        s['maximum'] = maximum
    return {'anyOf': [s, {'type': 'null'}]} if nullable else s


def _p702cv_obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _p702cv_base_metadata():
    """Type metadata of the original batch runtime (extended by export_p702cv_contract_metadata)."""
    number = _p702cv_num
    flag = {'type': 'boolean'}
    enum = lambda values: {'type': 'string', 'enum': values}
    scalar = {'type': 'number', 'x-unit': '1'}
    defs = {
        'power.set': ({'enabled': flag}, {'enabled': flag}, {'enabled': True}, ['power.enabled', 'valve.opening', 'control.status', 'pressure.reading_pa', 'pressure.deviation_pa', 'process.outflow_mol_s'], ['electronics', 'control_valve', 'pressure_sensor'], 'agent'),
        'pressure.set_setpoint': ({'pressure_Pa': number('Pa', 100000, 500000)}, {'pressure_Pa': number('Pa', 100000, 500000)}, {'pressure_Pa': 200000.0}, ['pressure.setpoint_pa', 'pressure.active_setpoint_pa', 'pressure.deviation_pa'], ['electronics'], 'agent'),
        'control.set_mode': ({'mode': enum(['manual', 'automatic'])}, {'mode': enum(['manual', 'automatic'])}, {'mode': 'manual'}, ['control.mode', 'control.status'], ['electronics', 'control_valve'], 'agent'),
        'control.set_gain': ({'gain_per_Pa_s': number('1/(Pa*s)', 0)}, {'gain_per_Pa_s': number('1/(Pa*s)', 0)}, {'gain_per_Pa_s': 0.000001}, ['control.gain_per_pa_s'], ['electronics'], 'agent'),
        'control.set_ramp_rate': ({'rate_Pa_s': number('Pa/s', 0, nullable=True)}, {'rate_Pa_s': number('Pa/s', 0, nullable=True)}, {'rate_Pa_s': 10000.0}, ['control.ramp_rate_pa_s'], ['electronics'], 'agent'),
        'valve.set_opening': ({'opening': number('1', 0, 1)}, {'opening': number('1', 0, 1)}, {'opening': 0.5}, ['valve.opening', 'process.outflow_mol_s'], ['control_valve'], 'agent'),
        'scene.set_valve_position': ({'position': number('1', 0, 1)}, {'position': number('1', 0, 1)}, {'position': 0.5}, ['scene.valve_position'], ['control_valve'], 'agent'),
        'simulation.step': ({'dt_s': number('s', 1e-9, 3600), 'world_time_s': number('s', 0)}, {'world_time_s': number('s', 0)}, {'dt_s': 0.1, 'world_time_s': 0.1}, ['pressure.active_setpoint_pa', 'valve.opening', 'control.status', 'pressure.reading_pa', 'pressure.deviation_pa', 'process.upstream_absolute_pressure_pa', 'process.upstream_moles_mol', 'process.outflow_mol_s', 'process.integrated_inflow_mol', 'process.integrated_outflow_mol'], ['flow_body', 'control_valve', 'pressure_sensor', 'electronics'], 'debug'),
        'simulation.set_boundary': ({'inflow_mol_s': number('mol/s', 0), 'downstream_absolute_pressure_Pa': number('Pa', 0)}, {'boundary_updated': flag}, {'inflow_mol_s': 0.0001, 'downstream_absolute_pressure_Pa': 101325.0}, ['process.inflow_mol_s', 'process.downstream_absolute_pressure_pa', 'process.outflow_mol_s'], ['flow_body'], 'debug'),
    }
    caps = {}
    for key, (inp, out, example, effects, components, visibility) in defs.items():
        caps[key] = {'input_schema': _p702cv_obj(inp), 'output_schema': _p702cv_obj(out), 'example_arguments': example, 'state_effects': effects, 'component_ids': components, 'visibility': visibility}
    slots = {
        'power.enabled': (flag, '1', 'setpoint', 'agent', 'power.set', 'enabled', ['electronics']),
        'pressure.setpoint_pa': (number('Pa', 100000, 500000, True), 'Pa', 'setpoint', 'agent', 'pressure.set_setpoint', 'pressure_Pa', ['electronics']),
        'pressure.active_setpoint_pa': (number('Pa', 100000, 500000, True), 'Pa', 'derived', 'agent', None, None, ['electronics']),
        'pressure.reading_pa': (number('Pa', 10000, 500000, True), 'Pa', 'sensor', 'agent', None, None, ['pressure_sensor']),
        'pressure.deviation_pa': (number('Pa', nullable=True), 'Pa', 'derived', 'agent', None, None, ['electronics']),
        'control.mode': (enum(['manual', 'automatic']), '1', 'setpoint', 'agent', 'control.set_mode', 'mode', ['electronics']),
        'control.gain_per_pa_s': (number('1/(Pa*s)', 0), '1/(Pa*s)', 'setpoint', 'agent', 'control.set_gain', 'gain_per_Pa_s', ['electronics']),
        'control.ramp_rate_pa_s': (number('Pa/s', 0, nullable=True), 'Pa/s', 'setpoint', 'agent', 'control.set_ramp_rate', 'rate_Pa_s', ['electronics']),
        'control.status': (enum(['power_off', 'manual', 'regulating', 'sensor_unavailable']), '1', 'derived', 'agent', None, None, ['electronics']),
        'valve.opening': (number('1', 0, 1), '1', 'derived', 'agent', 'valve.set_opening', 'opening', ['control_valve']),
        'scene.valve_position': (number('1', 0, 1), '1', 'scene', 'agent', 'scene.set_valve_position', 'position', ['control_valve']),
        'process.upstream_absolute_pressure_pa': (number('Pa', 0), 'Pa', 'process_truth', 'debug', None, None, ['flow_body']),
        'process.upstream_moles_mol': (number('mol', 0), 'mol', 'process_truth', 'debug', None, None, ['flow_body']),
        'process.inflow_mol_s': (number('mol/s', 0), 'mol/s', 'process_truth', 'debug', None, None, ['flow_body']),
        'process.downstream_absolute_pressure_pa': (number('Pa', 0), 'Pa', 'process_truth', 'debug', None, None, ['flow_body']),
        'process.outflow_mol_s': (number('mol/s', 0), 'mol/s', 'process_truth', 'debug', None, None, ['flow_body']),
        'process.integrated_inflow_mol': (number('mol', 0), 'mol', 'process_truth', 'debug', None, None, ['flow_body']),
        'process.integrated_outflow_mol': (number('mol', 0), 'mol', 'process_truth', 'debug', None, None, ['flow_body']),
    }
    state_slots = {}
    for key, (schema, unit, origin, visibility, write_cap, argument, components) in slots.items():
        state_slots[key] = {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': visibility, 'write_via': None if write_cap is None else {'capability_id': write_cap, 'argument': argument, 'fixed_arguments': {}}, 'component_ids': components}
    return {'asset_type_id': P702CV_ASSET_TYPE_ID, 'state_slots': state_slots, 'capabilities': caps,
        'configuration': {'gas': 'N2', 'pressure_mode': 'gauge', 'sensor_min_Pa': 0.0, 'sensor_full_scale_Pa': 500000.0, 'measurement_valid_min_Pa': 10000.0, 'control_setpoint_min_Pa': 100000.0, 'configuration_evidence': 'assumption; fixed for this asset, not a confirmed ordering code', 'sensor_response_s': 0.002, 'sensor_response_scope': 'manufacturer sensor-only figure; no dynamic sensor lag or full-loop settling-time model', 'vendor_valve_Kv': None, 'actual_valve_stroke_m': None, 'closed_loop_response_s': None},
        'limits': ['Host must supply process volume, temperature, initial absolute pressure, downstream absolute pressure, inflow, linear valve conductance, gauge reference and control gain; these are scenario values, not vendor component specifications.', 'Ideal isothermal N2 gas only: no liquid, real-gas, choking, reverse flow, leakage, noise, hysteresis or calibrated valve characteristic.', 'Discrete integral output control is a simulation assumption; stability and step size depend on explicit host parameters.', 'Power-off forces modeled outlet opening to zero as a scenario assumption; unpowered gas accumulation still occurs. Real fail-safe state is unknown.', 'Scene valve position is independent normalized explanatory state; no process-to-scene actuation or physical valve stroke is claimed.', 'Agent API filtering is implemented; trusted Python host introspection and file access are outside this boundary.', 'Idempotency is per principal and instance in memory only; no cross-restart exactly-once promise.']}


def export_p702cv_contract_metadata(panel=True):
    """Type metadata and executable input/output schemas, with the instrument functions of the multifunctional switch (control modes, alarms,
    reset, factory settings, configuration mode, FLASH mode) and, with panel=True, the panel.* capabilities; no live state values."""
    meta = _p702cv_base_metadata()
    number = _p702cv_num
    flag = {'type': 'boolean'}
    modes = {'type': 'integer', 'enum': sorted(P702CV_CONTROL_MODES), 'x-unit': '1'}
    pa = lambda nullable=False: number('Pa', 0, P702CV_SENSOR_FULL_SCALE_PA, nullable)        # noqa: E731
    alarm_mode = {'type': 'string', 'enum': ['off', 'absolute', 'response', 'power_up']}
    delay = {'type': 'integer', 'minimum': 0, 'maximum': 255, 'x-unit': 's'}
    enable = _p702cv_obj({'switch': flag, 'command': flag, 'automatic': flag})
    info = {'type': 'array', 'items': {'type': 'string', 'enum': list(P702CV_ALARM_KINDS)}, 'uniqueItems': True, 'maxItems': 3}
    caps, slots = meta['capabilities'], meta['state_slots']
    slots['pressure.active_setpoint_pa']['value_schema'] = number('Pa', 0, 500000, True)       # 0 % setpoints (mode 12, analog input < 2 %)
    slots['control.status']['value_schema']['enum'].append('program_stopped')                  # FLASH mode: program not running
    slots['control.status']['value_schema']['enum'].append('critical_error')                   # red LED continuous: needs servicing (p.21)
    caps['control.set_ramp_rate']['input_schema']['properties']['rate_Pa_s']['x-unit'] = 'Pa/s'   # as written in the contract
    slots['control.ramp_rate_pa_s']['value_schema']['x-unit'] = 'Pa/s'
    caps['control.set_mode']['state_effects'] += ['control.control_mode']
    caps['power.set']['state_effects'] += ['control.control_mode', 'control.mode', 'pressure.active_setpoint_pa', 'alarm.active', 'alarm.info', 'device.flash_mode']
    valve = ['valve.opening', 'process.outflow_mol_s', 'pressure.active_setpoint_pa', 'pressure.deviation_pa', 'control.status']
    alarm_settings = ['alarm.mode', 'alarm.min_limit_pa', 'alarm.max_limit_pa', 'alarm.delay_s', 'alarm.new_setpoint_pa', 'alarm.reset_enable']
    new = {
        'control.set_control_mode': ({'control_mode': modes, 'set_default': flag}, {'control_mode': modes, 'set_default': flag},
                                     {'control_mode': 0, 'set_default': False}, ['control.control_mode', 'control.default_control_mode', 'control.mode'] + valve,
                                     ['electronics', 'control_valve'], 'agent'),
        'alarm.configure': ({'mode': alarm_mode, 'min_limit_Pa': pa(), 'max_limit_Pa': pa(), 'delay_s': delay, 'new_setpoint_Pa': pa(True),
                             'reset_by_switch': flag, 'reset_by_command': flag, 'reset_automatic': flag},
                            {'mode': alarm_mode, 'min_limit_Pa': pa(), 'max_limit_Pa': pa(), 'delay_s': delay, 'new_setpoint_Pa': pa(True),
                             'reset_by_switch': flag, 'reset_by_command': flag, 'reset_automatic': flag},
                            {'mode': 'absolute', 'min_limit_Pa': 100000.0, 'max_limit_Pa': 300000.0, 'delay_s': 0, 'new_setpoint_Pa': None,
                             'reset_by_switch': True, 'reset_by_command': True, 'reset_automatic': False},
                            alarm_settings + ['alarm.active', 'alarm.info'], ['electronics'], 'agent'),
        'alarm.reset': ({}, {'cleared': flag}, {}, ['alarm.active', 'alarm.info'], ['electronics'], 'agent'),
        'device.reset': ({}, {'restarted': flag}, {}, ['alarm.active', 'alarm.info', 'control.control_mode', 'control.mode'] + valve,
                         ['electronics', 'control_valve'], 'agent'),
        'device.factory_restore': ({}, {'restored': flag}, {}, alarm_settings + ['alarm.active', 'alarm.info', 'control.control_mode', 'control.default_control_mode',
                                                                                  'control.mode', 'control.gain_per_pa_s', 'control.ramp_rate_pa_s'] + valve,
                                   ['electronics', 'control_valve'], 'agent'),
        'device.set_configuration_mode': ({'enabled': flag}, {'enabled': flag}, {'enabled': True}, ['device.configuration_mode'],
                                          ['electronics', 'electrical_connector'], 'agent'),
        'device.enter_flash_mode': ({}, {'flash_mode': flag}, {}, ['device.flash_mode', 'valve.opening', 'control.status', 'pressure.reading_pa',
                                                                   'pressure.deviation_pa', 'process.outflow_mol_s'], ['electronics', 'control_valve'], 'agent'),
        'simulation.set_analog_input': ({'fraction': number('1', 0, 1, True)}, {'fraction': number('1', 0, 1, True)}, {'fraction': 0.5},
                                        ['setpoint.analog_input_fraction', 'valve.opening', 'process.outflow_mol_s'], ['electrical_connector'], 'debug'),
        'fault.inject': ({'code': {'type': 'string', 'enum': ['critical_error']}}, {'critical_error': flag}, {'code': 'critical_error'},
                         ['device.critical_error', 'valve.opening', 'control.status', 'process.outflow_mol_s'], ['electronics', 'control_valve'], 'evaluator'),
        'fault.clear': ({}, {'critical_error': flag}, {}, ['device.critical_error', 'control.status'], ['electronics'], 'evaluator'),
    }
    fs = P702CV_SENSOR_FULL_SCALE_PA
    calmode = {'type': 'integer', 'enum': [0, 255], 'x-unit': '1'}
    shown = ['pressure.reading_pa', 'pressure.deviation_pa', 'control.status']
    error_in = {'offset_error_Pa': number('Pa', -fs, fs), 'gain_error': number('1', -0.5, 0.5), 'drift_error_Pa_s': number('Pa/s', -1000.0, 1000.0)}
    new.update({
        'sensor.calibrate_zero': ({}, {'calibration_mode': calmode, 'zero_offset_Pa': number('Pa', -fs, fs)}, {},
                                  ['sensor.zero_offset_pa', 'sensor.calibration_mode'] + shown, ['pressure_sensor', 'electronics'], 'agent'),
        'sensor.reset_calibration': ({}, {'zero_offset_Pa': number('Pa', -fs, fs)}, {}, ['sensor.zero_offset_pa', 'sensor.calibration_mode'] + shown,
                                     ['pressure_sensor', 'electronics'], 'agent'),
        'sensor.configure_error': (error_in, error_in, {'offset_error_Pa': 3000.0, 'gain_error': 0.0, 'drift_error_Pa_s': 0.0},
                                   ['sensor.offset_error_pa', 'sensor.gain_error', 'sensor.drift_error_pa_s', 'sensor.drift_offset_pa'] + shown,
                                   ['pressure_sensor'], 'evaluator')})
    for key, (inp, out, example, effects, components, visibility) in new.items():
        caps[key] = {'input_schema': _p702cv_obj(inp), 'output_schema': _p702cv_obj(out), 'example_arguments': example, 'state_effects': effects,
                     'component_ids': components, 'visibility': visibility}
    caps['device.factory_restore']['state_effects'] += ['sensor.zero_offset_pa', 'sensor.calibration_mode', 'pressure.reading_pa']
    caps['simulation.step']['state_effects'] += ['sensor.drift_offset_pa']
    new_slots = {
        'control.control_mode': (modes, '1', 'setpoint', 'agent', ['electronics']),
        'control.default_control_mode': (modes, '1', 'setpoint', 'agent', ['electronics']),
        'setpoint.analog_input_fraction': (number('1', 0, 1, True), '1', 'sensor', 'agent', ['electrical_connector']),
        'alarm.mode': (alarm_mode, '1', 'setpoint', 'agent', ['electronics']),
        'alarm.min_limit_pa': (pa(), 'Pa', 'setpoint', 'agent', ['electronics']),
        'alarm.max_limit_pa': (pa(), 'Pa', 'setpoint', 'agent', ['electronics']),
        'alarm.delay_s': (delay, 's', 'setpoint', 'agent', ['electronics']),
        'alarm.new_setpoint_pa': (pa(True), 'Pa', 'setpoint', 'agent', ['electronics']),
        'alarm.reset_enable': (enable, '1', 'setpoint', 'agent', ['electronics']),
        'alarm.active': (flag, '1', 'derived', 'agent', ['electronics']),
        'alarm.info': (info, '1', 'derived', 'agent', ['electronics']),
        'device.configuration_mode': (flag, '1', 'setpoint', 'agent', ['electronics', 'electrical_connector']),
        'device.flash_mode': (flag, '1', 'derived', 'agent', ['electronics']),
        'device.critical_error': (flag, '1', 'derived', 'agent', ['electronics']),
        'sensor.zero_offset_pa': (number('Pa', -fs, fs), 'Pa', 'setpoint', 'agent', ['pressure_sensor', 'electronics']),
        'sensor.calibration_mode': (calmode, '1', 'derived', 'agent', ['electronics']),
        'sensor.offset_error_pa': (number('Pa', -fs, fs), 'Pa', 'process_truth', 'evaluator', ['pressure_sensor']),
        'sensor.gain_error': (number('1', -0.5, 0.5), '1', 'process_truth', 'evaluator', ['pressure_sensor']),
        'sensor.drift_error_pa_s': (number('Pa/s', -1000.0, 1000.0), 'Pa/s', 'process_truth', 'evaluator', ['pressure_sensor']),
        'sensor.drift_offset_pa': (number('Pa'), 'Pa', 'process_truth', 'evaluator', ['pressure_sensor']),
    }
    for key, (schema, unit, origin, visibility, components) in new_slots.items():
        slots[key] = {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': visibility, 'write_via': None, 'component_ids': components}
    slots['device.configuration_mode']['write_via'] = {'capability_id': 'device.set_configuration_mode', 'argument': 'enabled', 'fixed_arguments': {}}
    meta['configuration'].update({'initial_control_mode': P702CV_INITIAL_CONTROL_MODE, 'factory_default_control_mode': P702CV_FACTORY_CONTROL_MODE,
                                  'control_modes': {str(k): v for k, v in P702CV_CONTROL_MODES.items()}, 'analog_slave_factor_percent': 100.0,
                                  'valve_safe_state': 'closed (normally closed valve assumed; power-off already closes the modeled outlet)'})
    meta['limits'] += [
        'Multifunctional switch and LEDs (manual 9.17.101 p.20-26): switch functions call the same commands as the digital interface. Network (node '
        'address / baud) switch procedures, FLOW-BUS auto-install and FLOW-BUS slave modes need the absent fieldbus; the RS-232 route of '
        'auto-zero (Control Mode 9 + Calibration Mode 9, p.34) is folded into sensor.calibrate_zero, so control mode 9 is not offered separately.',
        'Sensor model: indicated gauge pressure = true gauge pressure x (1 + gain error) + offset error + accumulated drift - stored zero. The '
        'errors are ideal 0 by default and set only by the evaluator (sensor.configure_error); auto-zero (switch 8-12 s, p.23) stores the signal at '
        'the reference condition (vented to the gauge reference) as the zero when it lies within +-2 % FS (assumption), else Calibration Mode 255. '
        'Reading, deviation, controller and alarms all use the indicated value. Realistic sensor errors are a later calibration / sim-to-real task.',
        'Alarm model: min/max alarm on absolute limits or limits around the setpoint, power-up alarm, delay, new setpoint and reset methods (p.38-40); '
        'evaluated at host steps only. Analog input is a host-supplied fraction of its range; analog slave factor fixed at 100 %.',
        'Critical error (red LED continuous, p.21: the instrument needs servicing before it can be used) is injected by the evaluator (fault.inject) and '
        'removed by fault.clear (servicing): the controller stops with the valve in its safe state (closed), every command except power, the evaluator '
        'functions and the analog input is refused, and it stays through a power cycle; the reading itself continues (assumption). Communication-loss, '
        'wink-command and fieldbus LED patterns need the absent fieldbus / host commands and do not occur.']
    meta['asset_version'] = '1.3.1'  # 1.3.0 (2026-10-20): switch and LED parts in the 3D model; 1.3.1 (2026-10-21): case body under the top cap (geometry only)
    if panel:
        meta['panel_capabilities'] = _p702cv_copy.deepcopy(_p702cv_panel()['caps'])
    return meta


def _p702cv_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _P702CV_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if '_chemsim_panel_ui' not in sys.modules:
                spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', P702CV_ASSET_DIR.parents[3] / 'scripts/asset_runtime/_panel_ui.py')
                sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
            engine = sys.modules['_chemsim_panel_ui']
        caps = set(export_p702cv_contract_metadata(panel=False)['capabilities'])
        spec = engine.load_spec(P702CV_ASSET_DIR / 'panel_ui.json', capabilities=caps)
        _P702CV_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _P702CV_PANEL


def _p702cv_target(s):
    """Setpoint the controller follows in the present control mode (p.34); the active setpoint ramps to it."""
    m = s['control.control_mode']
    if m in P702CV_AUTOMATIC_MODES and s['alarm.active'] and s['alarm.new_setpoint_pa'] is not None:
        return s['alarm.new_setpoint_pa']                                     # safe setpoint during an alarm until reset (p.40)
    if m == 7:
        return P702CV_SENSOR_FULL_SCALE_PA
    if m == 12:
        return 0.0
    if m in (1, 10):                                                          # analog input (slave factor 100 %); < 2 % counts as 0 % (p.28)
        v = (s['setpoint.analog_input_fraction'] or 0.0) * P702CV_SENSOR_FULL_SCALE_PA
        return 0.0 if v < P702CV_ZERO_SETPOINT_PA else v
    return s['pressure.setpoint_pa']


def _p702cv_force_valve(s):
    """Valve output where the controller does not set it (p.34): closed, fully open, analog steering; a 0 % setpoint closes the valve (p.20)."""
    m, a = s['control.control_mode'], s['pressure.active_setpoint_pa']
    if m in P702CV_AUTOMATIC_MODES:
        if a is None or a < P702CV_ZERO_SETPOINT_PA:
            s['valve.opening'] = 0.0
    elif m in (3, 22):
        s['valve.opening'] = 0.0
    elif m == 8:
        s['valve.opening'] = 1.0
    elif m == 21:
        s['valve.opening'] = float(s['setpoint.analog_input_fraction'] or 0.0)


class P702CVRuntime:
    """One host-owned instance. Public describe/read/invoke/write use agent visibility.

    The host constructs authenticated sessions and supplies explicit scenario inputs.
    The default world time is zero, power is off, manual opening is closed and no
    pressure target is configured. These are simulation initialization choices.
    """
    def __init__(self, instance_id, *, upstream_volume_m3, temperature_K,
                 initial_upstream_absolute_pressure_Pa, downstream_absolute_pressure_Pa,
                 inflow_mol_s, valve_conductance_mol_s_Pa, pressure_reference_Pa,
                 control_gain_per_Pa_s, scene_adapter=None, initial_world_time_s=0.0):
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError('A nonempty instance_id is required')
        params = {'upstream_volume_m3': upstream_volume_m3, 'temperature_K': temperature_K,
                  'initial_upstream_absolute_pressure_Pa': initial_upstream_absolute_pressure_Pa,
                  'downstream_absolute_pressure_Pa': downstream_absolute_pressure_Pa,
                  'inflow_mol_s': inflow_mol_s, 'valve_conductance_mol_s_Pa': valve_conductance_mol_s_Pa,
                  'pressure_reference_Pa': pressure_reference_Pa, 'control_gain_per_Pa_s': control_gain_per_Pa_s,
                  'initial_world_time_s': initial_world_time_s}
        if not all(_p702cv_number(v) and v >= 0 for v in params.values()):
            raise ValueError('Scenario inputs must be finite nonnegative numbers')
        if upstream_volume_m3 <= 0 or temperature_K <= 0 or valve_conductance_mol_s_Pa <= 0:
            raise ValueError('Volume, temperature and linear conductance must be positive')
        if not 263.15 <= temperature_K <= 343.15:
            raise ValueError('Temperature outside specified operating interval')
        if scene_adapter is not None and not callable(scene_adapter):
            raise ValueError('scene_adapter must be callable')
        self.instance_id = instance_id
        self._config = {k: float(params[k]) for k in ['upstream_volume_m3', 'temperature_K', 'valve_conductance_mol_s_Pa', 'pressure_reference_Pa']}
        self._rt_over_v = P702CV_GAS_CONSTANT * temperature_K / upstream_volume_m3
        moles = initial_upstream_absolute_pressure_Pa / self._rt_over_v
        if not _p702cv_number(self._rt_over_v) or self._rt_over_v <= 0 or not _p702cv_number(moles):
            raise ValueError('Scenario is outside numerical support')
        self._state = {'power.enabled': False, 'pressure.setpoint_pa': None, 'pressure.active_setpoint_pa': None,
            'pressure.reading_pa': None, 'pressure.deviation_pa': None,
            'control.mode': 'manual', 'control.gain_per_pa_s': float(control_gain_per_Pa_s),
            'control.ramp_rate_pa_s': None, 'control.status': 'power_off',
            'valve.opening': 0.0, 'scene.valve_position': 0.0,
            'process.upstream_absolute_pressure_pa': float(initial_upstream_absolute_pressure_Pa),
            'process.upstream_moles_mol': moles,
            'process.inflow_mol_s': float(inflow_mol_s),
            'process.downstream_absolute_pressure_pa': float(downstream_absolute_pressure_Pa),
            'process.outflow_mol_s': 0.0, 'process.integrated_inflow_mol': 0.0, 'process.integrated_outflow_mol': 0.0}
        self._time_s = float(initial_world_time_s)
        self._revision = 0
        self._scene_adapter = scene_adapter
        self._principals = {'__agent__': 'agent'}
        self._requests = {}
        self._lock = _p702cv_threading.RLock()
        self._metadata = export_p702cv_contract_metadata(panel=False)
        self._state.update({'control.control_mode': P702CV_INITIAL_CONTROL_MODE, 'control.default_control_mode': P702CV_FACTORY_CONTROL_MODE,
                            'setpoint.analog_input_fraction': None, **_p702cv_copy.deepcopy(P702CV_ALARM_DEFAULTS), 'alarm.active': False,
                            'alarm.info': [], 'device.configuration_mode': False, 'device.flash_mode': False, 'device.critical_error': False,
                            **_p702cv_copy.deepcopy(P702CV_SENSOR_DEFAULTS)})
        self._factory_gain = float(control_gain_per_Pa_s)                       # this scenario's controller setting restored by factory reset
        self._aux = {'power_epoch': 0, 'powered_at_s': None, 'alarm_since_s': None, 'alarm_clear_since_s': None}
        self._panel = None
        self.panel_capture_dir = None

    def register_principal(self, principal_id, *, visibility):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('New host-authenticated principal and visibility required')
            self._principals[principal_id] = visibility

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals:
            raise ValueError('Principal must first be registered by the host')
        return P702CVSession(self, principal_id)

    def describe(self, *, asset_type_id=None, instance_id=None):
        return self.session('__agent__').describe(asset_type_id=asset_type_id, instance_id=instance_id)

    def read(self, *, state_ids=None, instance_id=None):
        return self.session('__agent__').read(state_ids=state_ids, instance_id=instance_id)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        return self.session('__agent__').invoke(capability_id=capability_id, arguments=arguments, request_id=request_id, instance_id=instance_id)

    def write(self, *, state_id, value, request_id, instance_id=None):
        return self.session('__agent__').write(state_id=state_id, value=value, request_id=request_id, instance_id=instance_id)

    def debug_snapshot(self):
        with self._lock:
            return _p702cv_copy.deepcopy({'state': self._state, 'configuration': self._config, 'state_revision': self._revision, 'world_time_s': self._time_s})

    def sync_scene(self):
        """Trusted scene adapter receives only explanatory normalized position."""
        with self._lock:
            if self._scene_adapter is None:
                raise RuntimeError('No scene adapter is registered')
            self._scene_adapter(self.instance_id, {'scene.valve_position': self._state['scene.valve_position']})

    def _signal(self, state):
        """Sensor signal (Pa gauge, before the stored zero): true gauge pressure with the evaluator's offset / gain / drift errors (ideal 0)."""
        pg = state['process.upstream_absolute_pressure_pa'] - self._config['pressure_reference_Pa']
        return pg * (1.0 + state['sensor.gain_error']) + state['sensor.offset_error_pa'] + state['sensor.drift_offset_pa']

    def _observe(self, state):
        state['control.mode'] = 'automatic' if state['control.control_mode'] in P702CV_AUTOMATIC_MODES else 'manual'
        pg = self._signal(state) - state['sensor.zero_offset_pa']           # indicated value: every reading path (read, controller, alarms, panel)
        valid = state['power.enabled'] and not state['device.flash_mode'] and P702CV_MEASUREMENT_MIN_PA <= pg <= P702CV_SENSOR_FULL_SCALE_PA
        state['pressure.reading_pa'] = pg if valid else None
        target = state['pressure.active_setpoint_pa']
        state['pressure.deviation_pa'] = pg - target if valid and target is not None else None
        state['control.status'] = ('power_off' if not state['power.enabled'] else 'program_stopped' if state['device.flash_mode'] else 'critical_error'
                                   if state['device.critical_error'] else 'manual' if state['control.mode'] == 'manual' else 'regulating' if valid else 'sensor_unavailable')
        state['process.outflow_mol_s'] = self._config['valve_conductance_mol_s_Pa'] * state['valve.opening'] * max(state['process.upstream_absolute_pressure_pa'] - state['process.downstream_absolute_pressure_pa'], 0.0)

    def set_boundary(self, *, inflow_mol_s, downstream_absolute_pressure_Pa):
        """Trusted host updates fixed boundary for the next step; no time advance."""
        with self._lock:
            if not all(_p702cv_number(x) and x >= 0 for x in (inflow_mol_s, downstream_absolute_pressure_Pa)):
                raise ValueError('Finite nonnegative boundary inputs required')
            state = _p702cv_copy.deepcopy(self._state)
            state['process.inflow_mol_s'] = float(inflow_mol_s)
            state['process.downstream_absolute_pressure_pa'] = float(downstream_absolute_pressure_Pa)
            self._observe(state)
            if not _p702cv_number(state['process.outflow_mol_s']):
                raise ValueError('Boundary outside numerical support')
            self._state = state
            self._revision += 1
            return {'boundary_updated': True}

    def step(self, dt_s, *, world_time_s):
        """Advance only when trusted host supplies contiguous common-world time.

        Fixed opening per host step. q=C*u*max(p_up-p_down,0). The positive
        pressure-difference branch is integrated analytically; a below-downstream
        initial state first accumulates gas until it reaches downstream pressure.
        """
        with self._lock:
            if not _p702cv_number(dt_s) or not 1e-9 <= dt_s <= 3600 or not _p702cv_number(world_time_s) or world_time_s < 0:
                raise ValueError('Invalid host step or clock')
            if world_time_s <= self._time_s or not _p702cv_math.isclose(world_time_s, self._time_s + dt_s, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError('Host clock must be contiguous with this instance')
            s = _p702cv_copy.deepcopy(self._state)
            aux = dict(self._aux)
            target = _p702cv_target(s)
            active = s['pressure.active_setpoint_pa']
            if target is not None and active is not None:
                rate = s['control.ramp_rate_pa_s']
                s['pressure.active_setpoint_pa'] = target if rate is None else active + max(-rate * dt_s, min(rate * dt_s, target - active))
            elif target is not None:
                s['pressure.active_setpoint_pa'] = target
            running = s['power.enabled'] and not s['device.flash_mode'] and not s['device.critical_error']
            if running:
                _p702cv_force_valve(s)
            if (running and s['control.control_mode'] in P702CV_AUTOMATIC_MODES and s['pressure.reading_pa'] is not None
                    and s['pressure.active_setpoint_pa'] is not None and s['pressure.active_setpoint_pa'] >= P702CV_ZERO_SETPOINT_PA):
                error = s['pressure.reading_pa'] - s['pressure.active_setpoint_pa']
                change = s['control.gain_per_pa_s'] * error * dt_s
                if not _p702cv_number(change):
                    raise ValueError('Control update outside numerical support')
                s['valve.opening'] = min(1.0, max(0.0, s['valve.opening'] + change))
            p0 = s['process.upstream_absolute_pressure_pa']
            pd = s['process.downstream_absolute_pressure_pa']
            qin = s['process.inflow_mol_s']
            k = self._config['valve_conductance_mol_s_Pa'] * s['valve.opening']
            a = self._rt_over_v
            duration = dt_s
            p_start = p0
            if k == 0:
                p1 = p0 + a * qin * duration
            else:
                if p_start < pd:
                    if qin == 0:
                        duration = 0.0
                    else:
                        hit_time = (pd - p_start) / (a * qin)
                        used = min(duration, hit_time)
                        p_start += a * qin * used
                        duration -= used
                if duration <= 0:
                    p1 = p_start
                else:
                    fraction = -_p702cv_math.expm1(-a * k * duration)
                    p1 = p_start + (pd - p_start + qin / k) * fraction
            moles1 = p1 / a
            inflow = qin * dt_s
            outflow = s['process.upstream_moles_mol'] + inflow - moles1
            tolerance = 1e-12 * max(1.0, s['process.upstream_moles_mol'], inflow)
            if outflow < -tolerance:
                raise ValueError('Gas balance outside numerical support')
            if outflow < 0:
                outflow = 0.0
                moles1 = s['process.upstream_moles_mol'] + inflow
                p1 = moles1 * a
            s['process.upstream_absolute_pressure_pa'] = p1
            s['process.upstream_moles_mol'] = moles1
            s['process.integrated_inflow_mol'] += inflow
            s['process.integrated_outflow_mol'] += outflow
            s['sensor.drift_offset_pa'] += s['sensor.drift_error_pa_s'] * dt_s
            self._observe(s)
            if not all(v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or _p702cv_number(v) for v in s.values()):
                raise ValueError('Host step outside numerical support')
            self._alarm_update(s, aux, float(world_time_s))
            self._state = s
            self._aux = aux
            self._time_s = float(world_time_s)
            self._revision += 1
            return {'world_time_s': self._time_s}

    def _alarm_update(self, s, aux, t):
        """Min/max alarm at a host step (p.38-40): absolute limits or limits around the active setpoint, Alarm Delay Time before it comes on and
        before an automatic reset (when that method is enabled); a power-up alarm stays until it is reset."""
        mode, r = s['alarm.mode'], s['pressure.reading_pa']
        if mode not in ('absolute', 'response') or r is None:
            return
        a = s['pressure.active_setpoint_pa']
        if mode == 'absolute':
            hi, lo = r > s['alarm.max_limit_pa'], r < s['alarm.min_limit_pa']
        elif a is None:
            hi = lo = False
        else:
            hi, lo = r > a + s['alarm.max_limit_pa'], r < a - s['alarm.min_limit_pa']
        kinds = [k for k, on in (('minimum', lo), ('maximum', hi)) if on] + (['response'] if mode == 'response' and (hi or lo) else [])
        delay = s['alarm.delay_s']
        if kinds:
            aux['alarm_clear_since_s'] = None
            if aux['alarm_since_s'] is None:
                aux['alarm_since_s'] = t
            if t - aux['alarm_since_s'] >= delay - 1e-9:
                s['alarm.active'], s['alarm.info'] = True, kinds
        else:
            aux['alarm_since_s'] = None
            if s['alarm.active'] and s['alarm.reset_enable']['automatic'] and 'power_up' not in s['alarm.info']:
                if aux['alarm_clear_since_s'] is None:
                    aux['alarm_clear_since_s'] = t
                if t - aux['alarm_clear_since_s'] >= delay - 1e-9:
                    s['alarm.active'], s['alarm.info'], aux['alarm_clear_since_s'] = False, [], None

    def _mode_changed(self, s):
        if s['pressure.active_setpoint_pa'] is None and _p702cv_target(s) is not None:
            s['pressure.active_setpoint_pa'] = _p702cv_target(s)
        if s['power.enabled'] and not s['device.flash_mode'] and not s['device.critical_error']:
            _p702cv_force_valve(s)

    def _restart(self, s, aux):
        """Power-up or reset: control modes 0 / 1 / 9 / 18 return to the default control mode (p.34); alarm timers restart."""
        if s['control.control_mode'] in P702CV_VOLATILE_MODES:
            s['control.control_mode'] = s['control.default_control_mode']
        s['control.mode'] = 'automatic' if s['control.control_mode'] in P702CV_AUTOMATIC_MODES else 'manual'
        aux.update(power_epoch=aux['power_epoch'] + 1, alarm_since_s=None, alarm_clear_since_s=None)
        self._mode_changed(s)

    def _apply(self, s, aux, cid, args, source):
        """One instrument command on a state copy, from the digital interface (source 'command') or the multifunctional switch ('panel');
        returns the result, or an error code with nothing changed.  The caller observes and commits."""
        if cid in ('fault.inject', 'fault.clear'):                            # evaluator: critical error (p.21) / its servicing
            if cid == 'fault.inject' and s['device.critical_error']:
                return 'PRECONDITION_FAILED'
            s['device.critical_error'] = cid == 'fault.inject'
            if s['device.critical_error']:
                s['valve.opening'] = 0.0                                      # the valve in its safe state (normally closed)
            return {'critical_error': s['device.critical_error']}
        if cid not in ('power.set', 'simulation.set_analog_input', 'sensor.configure_error') and (not s['power.enabled'] or s['device.flash_mode']
                                                                                                 or s['device.critical_error']):
            return 'PRECONDITION_FAILED'
        result = dict(args)
        if cid == 'power.set':
            was = s['power.enabled']
            s['power.enabled'] = args['enabled']
            if not args['enabled']:
                s['valve.opening'] = 0.0
            elif not was:                                                     # power-up: FLASH mode left, default mode, power-up alarm (p.23, p.34, p.38)
                s['device.flash_mode'] = False
                s['alarm.active'], s['alarm.info'] = (True, ['power_up']) if s['alarm.mode'] == 'power_up' else (False, [])
                self._restart(s, aux)
                aux['powered_at_s'] = self._time_s
        elif cid == 'pressure.set_setpoint':
            s['pressure.setpoint_pa'] = args['pressure_Pa']
            if s['pressure.active_setpoint_pa'] is None:
                s['pressure.active_setpoint_pa'] = args['pressure_Pa']
        elif cid == 'control.set_mode':
            if args['mode'] == 'automatic' and (s['pressure.active_setpoint_pa'] is None or s['pressure.reading_pa'] is None):
                return 'PRECONDITION_FAILED'
            s['control.control_mode'] = 0 if args['mode'] == 'automatic' else P702CV_INITIAL_CONTROL_MODE
            s['control.mode'] = args['mode']
            self._mode_changed(s)
        elif cid == 'control.set_control_mode':
            s['control.control_mode'] = args['control_mode']
            if args['set_default']:
                s['control.default_control_mode'] = args['control_mode']
            self._mode_changed(s)
        elif cid == 'control.set_gain':
            s['control.gain_per_pa_s'] = args['gain_per_Pa_s']
        elif cid == 'control.set_ramp_rate':
            s['control.ramp_rate_pa_s'] = args['rate_Pa_s']
        elif cid == 'valve.set_opening':
            if s['control.control_mode'] != P702CV_INITIAL_CONTROL_MODE:       # valve steering: the setpoint drives the valve output (p.34)
                return 'PRECONDITION_FAILED'
            s['valve.opening'] = args['opening']
        elif cid == 'alarm.configure':
            if args['mode'] == 'absolute' and not args['max_limit_Pa'] > args['min_limit_Pa']:
                return 'INVALID_ARGUMENT'                                     # maximum limit must be greater than the minimum limit (p.39)
            s.update({'alarm.mode': args['mode'], 'alarm.min_limit_pa': float(args['min_limit_Pa']), 'alarm.max_limit_pa': float(args['max_limit_Pa']),
                      'alarm.delay_s': args['delay_s'], 'alarm.new_setpoint_pa': None if args['new_setpoint_Pa'] is None else float(args['new_setpoint_Pa']),
                      'alarm.reset_enable': {'switch': args['reset_by_switch'], 'command': args['reset_by_command'], 'automatic': args['reset_automatic']},
                      'alarm.active': False, 'alarm.info': []})
            aux.update(alarm_since_s=None, alarm_clear_since_s=None)
        elif cid == 'alarm.reset':
            if not s['alarm.reset_enable']['switch' if source == 'panel' else 'command']:
                return 'PRECONDITION_FAILED'                                  # Reset Alarm Enable bit 0 (switch) / bit 2 (parameter Reset), p.40
            result = {'cleared': s['alarm.active']}
            s['alarm.active'], s['alarm.info'] = False, []
            aux.update(alarm_since_s=None, alarm_clear_since_s=None)
        elif cid == 'device.reset':
            s['alarm.active'], s['alarm.info'] = False, []
            s['valve.opening'] = 0.0
            self._restart(s, aux)
            result = {'restarted': True}
        elif cid == 'device.factory_restore':
            s.update(_p702cv_copy.deepcopy(P702CV_ALARM_DEFAULTS))
            s.update({'alarm.active': False, 'alarm.info': [], 'control.control_mode': P702CV_FACTORY_CONTROL_MODE,
                      'control.default_control_mode': P702CV_FACTORY_CONTROL_MODE, 'control.gain_per_pa_s': self._factory_gain, 'control.ramp_rate_pa_s': None,
                      'sensor.zero_offset_pa': 0.0, 'sensor.calibration_mode': 0})           # assumption: the factory zero is part of the factory settings
            aux.update(alarm_since_s=None, alarm_clear_since_s=None)
            self._mode_changed(s)
            result = {'restored': True}
        elif cid == 'device.set_configuration_mode':
            s['device.configuration_mode'] = args['enabled']
        elif cid == 'device.enter_flash_mode':
            s['device.flash_mode'], s['valve.opening'] = True, 0.0
            result = {'flash_mode': True}
        elif cid == 'sensor.calibrate_zero':                                 # auto-zero (switch 8-12 s, p.23; Calibration Mode 9 / 255, p.34)
            signal = self._signal(s)
            if abs(signal) <= P702CV_AUTOZERO_BAND_PA:
                s['sensor.zero_offset_pa'], s['sensor.calibration_mode'] = float(signal), 0
            else:
                s['sensor.calibration_mode'] = 255                            # error: the zero is kept
            result = {'calibration_mode': s['sensor.calibration_mode'], 'zero_offset_Pa': s['sensor.zero_offset_pa']}
        elif cid == 'sensor.reset_calibration':
            s['sensor.zero_offset_pa'], s['sensor.calibration_mode'] = 0.0, 0
            result = {'zero_offset_Pa': 0.0}
        elif cid == 'sensor.configure_error':
            s.update({'sensor.offset_error_pa': float(args['offset_error_Pa']), 'sensor.gain_error': float(args['gain_error']),
                      'sensor.drift_error_pa_s': float(args['drift_error_Pa_s']), 'sensor.drift_offset_pa': 0.0})   # drift accumulates from now
        elif cid == 'simulation.set_analog_input':
            s['setpoint.analog_input_fraction'] = None if args['fraction'] is None else float(args['fraction'])
            if s['power.enabled'] and not s['device.flash_mode'] and not s['device.critical_error']:
                _p702cv_force_valve(s)
        return result

    def _commit(self, s, aux):
        self._observe(s)
        if not all(v is None or isinstance(v, (bool, str, list, dict)) or _p702cv_number(v) for v in s.values()):
            return False
        self._state, self._aux = s, aux
        self._revision += 1
        return True

    def set_analog_input(self, *, fraction):
        """Trusted host: analog setpoint input signal as a fraction of its range (None: no signal); no time advance."""
        with self._lock:
            if fraction is not None and not (_p702cv_number(fraction) and 0 <= fraction <= 1):
                raise ValueError('Analog input fraction must be within 0..1 or None')
            s, aux = _p702cv_copy.deepcopy(self._state), dict(self._aux)
            self._apply(s, aux, 'simulation.set_analog_input', {'fraction': fraction}, 'host')
            if not self._commit(s, aux):
                raise ValueError('Analog input outside numerical support')
            return {'fraction': fraction}

    def panel(self):
        """The front-panel engine (multifunctional switch and LEDs) of this instance; created on first use (trusted host / tests)."""
        if self._panel is None:
            self._panel = _p702cv_panel()['engine'].PanelUI(_p702cv_panel()['spec'], _P702CVPanelHost(self))
        return self._panel


class P702CVSession:
    """Visibility derives from host registration, never from user arguments."""
    def __init__(self, runtime, principal_id):
        self._runtime = runtime
        self._principal_id = principal_id

    def _visible(self, entry):
        return entry['visibility'] == 'agent' or self._runtime._principals[self._principal_id] in ('debug', 'evaluator')

    def _envelope(self, *, status='succeeded', result=None, observations=None, error=None, request_id=None):
        r = self._runtime
        return {'protocol_version': '1.0.0', 'instance_id': r.instance_id, 'request_id': request_id if isinstance(request_id, str) else None,
                'operation_id': None, 'status': status, 'state_revision': r._revision,
                'clock': {'domain': 'simulation', 'time_s': r._time_s}, 'result': result or {}, 'observations': observations or {}, 'error': error}

    def _error(self, code, request_id=None, side_effects='none'):
        messages = {'UNKNOWN_INSTANCE': 'Instance unavailable', 'UNKNOWN_CAPABILITY': 'Capability unavailable', 'ACCESS_DENIED': 'Requested resource is unavailable to this session', 'INVALID_ARGUMENT': 'Arguments do not match the supported schema', 'PRECONDITION_FAILED': 'Operation preconditions are not satisfied', 'REQUEST_CONFLICT': 'Request identifier was used with another payload', 'UNSUPPORTED': 'Required adapter is not registered', 'DEVICE_ERROR': 'Scene adapter failed; external state is uncertain'}
        return self._envelope(status='unsupported' if code == 'UNSUPPORTED' else 'failed' if code == 'DEVICE_ERROR' else 'rejected', error={'code': code, 'message': messages[code], 'retryable': False, 'side_effects': side_effects}, request_id=request_id)

    def _instance_ok(self, instance_id):
        return instance_id is None or isinstance(instance_id, str) and instance_id == self._runtime.instance_id

    def describe(self, *, asset_type_id=None, instance_id=None):
        r = self._runtime
        with r._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._error('INVALID_ARGUMENT')
            if not self._instance_ok(instance_id) or asset_type_id is not None and asset_type_id != P702CV_ASSET_TYPE_ID:
                return self._error('UNKNOWN_INSTANCE')
            meta = _p702cv_copy.deepcopy(r._metadata)
            meta['capabilities'].update(_p702cv_copy.deepcopy(_p702cv_panel()['caps']))
            meta['state_slots'] = {k: v for k, v in meta['state_slots'].items() if self._visible(v)}
            meta['capabilities'] = {k: v for k, v in meta['capabilities'].items() if self._visible(v)}
            for capability in meta['capabilities'].values():
                if 'state_effects' in capability:
                    capability['state_effects'] = [key for key in capability['state_effects'] if key in meta['state_slots']]
            return meta

    def read(self, *, state_ids=None, instance_id=None):
        r = self._runtime
        with r._lock:
            if not self._instance_ok(instance_id):
                return self._error('UNKNOWN_INSTANCE')
            visible = {k: v for k, v in r._metadata['state_slots'].items() if self._visible(v)}
            ids = list(visible) if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
                return self._error('INVALID_ARGUMENT')
            if any(k not in visible for k in ids):
                return self._error('ACCESS_DENIED')
            observations = {}
            for key in ids:
                value = r._state[key]
                unavailable = value is None
                sensor = key in ('pressure.reading_pa', 'pressure.deviation_pa')
                reason = ('power_off' if not r._state['power.enabled'] else 'program_stopped' if r._state['device.flash_mode'] else 'outside_selected_measurement_range' if r._state['pressure.reading_pa'] is None else 'setpoint_unconfigured') if unavailable and sensor else 'not_configured' if unavailable else None
                observations[key] = {'value': _p702cv_copy.deepcopy(value), 'unit': visible[key]['unit'], 'quality': 'unavailable' if unavailable else 'estimated' if sensor or key == 'valve.opening' else 'valid', 'sample_time_s': r._time_s, 'sample_window_s': None, 'unavailable_reason': reason}
            return self._envelope(observations=observations)

    @staticmethod
    def _validate(schema, value):
        if 'anyOf' in schema:
            return any(P702CVSession._validate(part, value) for part in schema['anyOf'])
        kind = schema['type']
        if kind == 'null':
            return value is None
        if kind == 'number':
            return _p702cv_number(value) and ('minimum' not in schema or value >= schema['minimum']) and ('maximum' not in schema or value <= schema['maximum'])
        if kind == 'boolean':
            return isinstance(value, bool)
        if kind == 'integer':
            return (isinstance(value, int) and not isinstance(value, bool) and ('enum' not in schema or value in schema['enum'])
                    and ('minimum' not in schema or value >= schema['minimum']) and ('maximum' not in schema or value <= schema['maximum']))
        if kind == 'string':
            return isinstance(value, str) and ('enum' not in schema or value in schema['enum'])
        if kind == 'object':
            return isinstance(value, dict) and set(value) == set(schema['properties']) and all(P702CVSession._validate(sub, value[key]) for key, sub in schema['properties'].items())
        return False

    def write(self, *, state_id, value, request_id, instance_id=None):
        r = self._runtime
        with r._lock:
            if not self._instance_ok(instance_id):
                return self._error('UNKNOWN_INSTANCE', request_id)
            spec = r._metadata['state_slots'].get(state_id) if isinstance(state_id, str) else None
            if spec is None or not self._visible(spec) or spec['write_via'] is None:
                return self._error('ACCESS_DENIED', request_id)
            route = spec['write_via']
            return self.invoke(capability_id=route['capability_id'], arguments={route['argument']: value}, request_id=request_id, instance_id=instance_id)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        r = self._runtime
        with r._lock:
            if not self._instance_ok(instance_id):
                return self._error('UNKNOWN_INSTANCE', request_id)
            if isinstance(capability_id, str) and capability_id.startswith('panel.') and capability_id in _p702cv_panel()['caps']:
                return self._invoke_panel(capability_id, arguments, request_id)
            cap = r._metadata['capabilities'].get(capability_id) if isinstance(capability_id, str) else None
            if cap is None or not self._visible(cap):
                return self._error('UNKNOWN_CAPABILITY', request_id)
            if not isinstance(request_id, str) or not request_id or not self._validate(cap['input_schema'], arguments):
                return self._error('INVALID_ARGUMENT', request_id)
            digest = _p702cv_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            key = (self._principal_id, request_id)
            if key in r._requests:
                old_digest, old_result = r._requests[key]
                return _p702cv_copy.deepcopy(old_result) if old_digest == digest else self._error('REQUEST_CONFLICT', request_id)
            s = _p702cv_copy.deepcopy(r._state)
            aux = dict(r._aux)
            result = dict(arguments)
            if capability_id in ('simulation.step', 'simulation.set_boundary'):
                try:
                    result = r.step(**arguments) if capability_id == 'simulation.step' else r.set_boundary(**arguments)
                except (ValueError, OverflowError, ZeroDivisionError):
                    return self._error('INVALID_ARGUMENT', request_id)
            else:
                if capability_id == 'scene.set_valve_position':
                    if r._scene_adapter is None:
                        return self._error('UNSUPPORTED', request_id)
                    s['scene.valve_position'] = arguments['position']
                    try:
                        r._scene_adapter(r.instance_id, {'scene.valve_position': arguments['position']})
                    except Exception:
                        failed = self._error('DEVICE_ERROR', request_id, 'unknown')
                        r._requests[key] = (digest, _p702cv_copy.deepcopy(failed))
                        return failed
                else:
                    outcome = r._apply(s, aux, capability_id, arguments, 'command')
                    if isinstance(outcome, str):
                        return self._error(outcome, request_id)
                    result = outcome
                if not r._commit(s, aux):
                    return self._error('INVALID_ARGUMENT', request_id)
            response = self._envelope(result=result, request_id=request_id)
            r._requests[key] = (digest, _p702cv_copy.deepcopy(response))
            return response

    def _invoke_panel(self, capability_id, arguments, request_id):
        """panel.* commands: a gesture on the multifunctional switch (engine + panel_ui.json), an LED capture, or the evaluator frame."""
        r = self._runtime
        panel = _p702cv_panel()
        definition = panel['caps'][capability_id]
        if not self._visible(definition):
            return self._error('ACCESS_DENIED', request_id)
        if not isinstance(request_id, str) or not request_id or panel['engine'].check_arguments(definition['input_schema'], arguments):
            return self._error('INVALID_ARGUMENT', request_id)
        digest = _p702cv_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
        key = (self._principal_id, request_id)
        if key in r._requests:
            old_digest, old_result = r._requests[key]
            return _p702cv_copy.deepcopy(old_result) if old_digest == digest else self._error('REQUEST_CONFLICT', request_id)
        ui = r.panel()
        try:
            if capability_id == 'panel.frame':
                result = {'frame': ui.frame()}
            elif capability_id == 'panel.capture':
                data, width, height = ui.png_bytes(arguments['display_id'])
                digest_png = _p702cv_hashlib.sha256(data).hexdigest()
                folder = _p702cv_Path(r.panel_capture_dir) if r.panel_capture_dir else _p702cv_Path(_p702cv_tempfile.gettempdir()) / 'chemistry_sim_panel' / r.instance_id
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ('%s_%s.png' % (arguments['display_id'], digest_png[:16]))
                if not path.exists():
                    path.write_bytes(data)
                result = {'display_id': arguments['display_id'], 'time_s': r._time_s,
                          'image': {'path': str(path), 'format': 'png', 'width': width, 'height': height, 'sha256': digest_png}}
            else:
                result = {'accepted': bool(ui.switch(arguments['input_id'], arguments['position']))}
        except panel['engine'].PanelError:
            return self._error('INVALID_ARGUMENT', request_id)
        response = self._envelope(result=result, request_id=request_id)
        r._requests[key] = (digest, _p702cv_copy.deepcopy(response))
        return response


class _P702CVPanelHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py: what the instrument firmware behind the switch and LEDs reads, calls and times by."""
    def __init__(self, runtime):
        self.runtime = runtime

    def panel_read(self):
        r = self.runtime
        slots = r._metadata['state_slots']
        out = {k: _p702cv_copy.deepcopy(v) for k, v in r._state.items() if k in slots and slots[k]['visibility'] == 'agent'}
        out.update({'host.powered': r._state['power.enabled'] and not r._state['device.flash_mode'], 'host.power_epoch': r._aux['power_epoch'],
                    'host.powered_at_s': r._aux['powered_at_s']})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A switch function takes the checked path of a direct command (same preconditions, revision and state); alarm reset checks the switch method."""
        r = self.runtime
        engine = _p702cv_panel()['engine']
        cap = r._metadata['capabilities'].get(capability_id)
        if cap is None or cap['visibility'] != 'agent' or not P702CVSession._validate(cap['input_schema'], arguments):
            raise engine.PanelRejected('INVALID_ARGUMENT')
        with r._lock:
            s, aux = _p702cv_copy.deepcopy(r._state), dict(r._aux)
            outcome = r._apply(s, aux, capability_id, dict(arguments), 'panel')
            if isinstance(outcome, str):
                raise engine.PanelRejected(outcome)
            if not r._commit(s, aux):
                raise engine.PanelRejected('INVALID_ARGUMENT')
            return outcome

    def panel_time(self):
        return self.runtime._time_s


# BEGIN CHEM-182 VICI EUTA APPEND-ONLY RUNTIME
# Independent namespace: no dependencies on other automation instruments.
import copy as _veuta_copy
import json as _veuta_json
import math as _veuta_math
import threading as _veuta_threading

VICI_EUTA_ASSET_TYPE_ID = 'instrument.vici_euta_2sd6mwe'
VICI_EUTA_PORT_IDS = ('port_common', 'port_1', 'port_2', 'port_3', 'port_4', 'port_5', 'port_6')


def _veuta_number(value):
    try:
        return type(value) in (int, float) and _veuta_math.isfinite(value)
    except (ValueError, OverflowError, TypeError):
        return False


def _veuta_obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _veuta_nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def export_vici_euta_contract_metadata():
    """Return type metadata only; host-only events are separated from agent APIs."""
    position = {'type': 'integer', 'minimum': 1, 'maximum': 6, 'x-unit': '1'}
    time = {'type': 'number', 'minimum': 0, 'x-unit': 's'}
    angle = {'type': 'number', 'minimum': 0, 'maximum': 5 * _veuta_math.pi / 3, 'x-unit': 'rad'}
    selected_port = _veuta_nullable({'type': 'string', 'enum': list(VICI_EUTA_PORT_IDS[1:])})
    connections = {'type': 'array', 'minItems': 0, 'maxItems': 1,
                   'items': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                             'items': {'type': 'string', 'enum': list(VICI_EUTA_PORT_IDS)}}}
    flag = {'type': 'boolean'}
    definitions = {
        'valve.target_position': (_veuta_nullable(position), '1', 'setpoint', ['actuator'], 'valve.select_position', 'position'),
        'valve.position': (_veuta_nullable(position), '1', 'derived', ['rotor'], None, None),
        'valve.in_position': (flag, '1', 'derived', ['actuator', 'rotor'], None, None),
        'valve.switching_time_s': ({'type': 'null'}, 's', 'derived', ['actuator'], None, None),
        'route.connected_port_id': (selected_port, '1', 'derived', ['valve_body'], None, None),
        'route.connections': (connections, '1', 'derived', ['valve_body'], None, None),
        'scene.rotor_angle_rad': (angle, 'rad', 'scene', ['rotor'], None, None),
        'scene.sync_status': ({'type': 'string', 'enum': ['unbound', 'synced']}, '1', 'scene', ['rotor'], None, None),
    }
    slots = {}
    for key, (schema, unit, origin, components, cap, arg) in definitions.items():
        slots[key] = {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': 'agent',
                      'access': 'read_write' if cap else 'read_only', 'component_ids': components,
                      'write_via': {'capability_id': cap, 'argument': arg, 'fixed_arguments': {}} if cap else None,
                      'reference_conditions': 'Rotor local Z; theta=(position-1)*pi/3; zero is synthetic initial visual pose' if key == 'scene.rotor_angle_rad' else None,
                      'sampling': {'mode': 'static' if key == 'valve.switching_time_s' else 'instant', 'time_domain': 'none' if key == 'valve.switching_time_s' else 'simulation', 'window': None}}
    select_effects = ['valve.target_position', 'valve.position', 'valve.in_position', 'route.connected_port_id', 'route.connections']
    arrive_effects = ['valve.position', 'valve.in_position', 'route.connected_port_id', 'route.connections', 'scene.rotor_angle_rad', 'scene.sync_status']
    select_result = ['valve.target_position', 'valve.in_position', 'route.connected_port_id']
    arrive_result = ['valve.position', 'valve.in_position', 'route.connected_port_id', 'route.connections', 'scene.rotor_angle_rad', 'scene.sync_status']
    def cap(inputs, outputs, example, effects, components, visibility, description):
        return {'input_schema': _veuta_obj(inputs), 'output_schema': _veuta_obj(outputs),
                'example_arguments': example, 'state_effects': effects, 'component_ids': components,
                'visibility': visibility, 'description_zh': description, 'availability': 'implemented'}
    capabilities = {'valve.select_position': cap({'position': position}, {k: slots[k]['value_schema'] for k in select_result},
                        {'position': 3}, select_effects, ['actuator', 'rotor', 'valve_body'], 'agent',
                        '仅写入6位选择目标，立即撤销到位并断开抽象气路；成功不是切换完成。')}
    host_caps = {
        'host.arrive': cap({'position': position, 'world_time_s': time}, {k: slots[k]['value_schema'] for k in arrive_result},
                         {'position': 3, 'world_time_s': 1.0}, arrive_effects, ['actuator', 'rotor', 'valve_body'], 'debug',
                         '可信宿主显式确认目标到位并提交转子姿态；只建立公共口与选中端口的无向连接。'),
        'host.set_time': cap({'world_time_s': time}, {'world_time_s': time}, {'world_time_s': 1.0}, [], ['actuator'], 'debug',
                           '接收宿主共同世界时钟；不自动推断到位、不生成切换持续时间。')}
    return {'asset_type_id': VICI_EUTA_ASSET_TYPE_ID, 'asset_version': '1.0.2', 'catalog_id': 'CHEM-182',
            'adapter': 'basic40_runtime_automation.ViciEutaValveRuntime', 'state_slots': slots,
            'capabilities': capabilities, 'host_capabilities': host_caps,
            'configuration': {'position_count': 6, 'thermal_standoff_length_m': 0.0508,
                              'fitting_nominal_outer_diameter_m': 0.003175, 'bore_diameter_m': 0.001,
                              'gas_pressure_rating_Pa': 400 * 6894.757293168, 'maximum_temperature_K': 473.15,
                              'drive_voltage_V': 24, 'serial_interfaces': ['RS232', 'RS485'],
                              'switching_time_s': None, 'dead_volume_m3': None, 'residual_fraction': None,
                              'cross_contamination_fraction': None, 'whole_instrument_envelope_m': None,
                              'whole_instrument_mass_kg': None,
                              'unknown_evidence': '未取得本EUTA配置的当前切换时间、死体积、残留、交叉污染及整机实测包络质量；不使用旧EMT时序'},
            'scene_binding': {'callback': 'scene_adapter(instance_id, {"scene.rotor_angle_rad": angle_rad})',
                              'object': 'vici_euta__rotor_joint', 'axis': 'local_z', 'mapping': '(position-1)*pi/3'},
            'limitations': [
                '仅实现6位选择的离散拓扑；没有流量、压降、死体积、冲洗、残留、交叉污染或切换瞬态求解。',
                '到位为可信宿主事件生成的derived状态，不是硬件传感器读数。实际切换时间未知且保持null。',
                '初始无已知阀位与连接；转子零角只是合成场景初始姿态，不代表硬件归零。',
                '转动过程中抽象气路断开是仿真安全规则，未声称厂家的真实过渡流道具有同样行为。',
                '没有宿主到位事件时，即使世界时间前进也不自动完成；没有时长、加速度、扭矩和中间角度模型。',
                'scene_adapter未绑定时仅更新场景目标数值并报告unbound；绑定成功为synced，需另做实际模型求值验证。',
                '场景回调失败时内部状态和时钟不变，外部场景副作用unknown；同请求重放不会再次调用回调。',
                '公共口与6个端口为无向气路连接；实例间气路、库存和守恒由宿主管理。',
                '没有硬件或机器人驱动；宿主事件不会公开到agent describe/invoke。Python对象及文件权限须由宿主另外隔离。',
                '去重仅单实例进程内内存有效；没有跨重启恰好一次保证。']}


class ViciEutaValveRuntime:
    """Trusted host owns an independent six-position valve instance.

    Agent-facing methods are describe/read/invoke/write. host_arrive and
    host_set_time are trusted host hooks, not user-callable capabilities.
    State changes are discrete and need no invented physical switching time.
    """
    def __init__(self, instance_id, *, scene_adapter=None, initial_world_time_s=0.0):
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError('A nonempty instance_id is required')
        if not _veuta_number(initial_world_time_s) or initial_world_time_s < 0:
            raise ValueError('Initial host clock must be finite and nonnegative')
        if scene_adapter is not None and not callable(scene_adapter):
            raise ValueError('scene_adapter must be callable or None')
        self.instance_id = instance_id
        self._scene_adapter = scene_adapter
        self._metadata = export_vici_euta_contract_metadata()
        self._state = {'valve.target_position': None, 'valve.position': None, 'valve.in_position': False,
                       'valve.switching_time_s': None, 'route.connected_port_id': None, 'route.connections': [],
                       'scene.rotor_angle_rad': 0.0, 'scene.sync_status': 'unbound'}
        self._time_s = float(initial_world_time_s)
        self._revision = 0
        self._requests = {}
        self._lock = _veuta_threading.RLock()

    def _envelope(self, *, request_id=None, status='succeeded', result=None, observations=None, error=None):
        return {'protocol_version': '1.0.0', 'instance_id': self.instance_id,
                'request_id': request_id if isinstance(request_id, str) else None,
                'operation_id': None, 'status': status, 'state_revision': self._revision,
                'clock': {'domain': 'simulation', 'time_s': self._time_s},
                'result': result if result is not None else {},
                'observations': observations if observations is not None else {}, 'error': error}

    def _error(self, code, request_id=None, *, side_effects='none'):
        messages = {'UNKNOWN_INSTANCE': 'Instance unavailable', 'UNKNOWN_CAPABILITY': 'Capability unavailable',
                    'INVALID_ARGUMENT': 'Arguments do not match the supported schema',
                    'ACCESS_DENIED': 'State unavailable or not writable',
                    'PRECONDITION_FAILED': 'Operation preconditions are not satisfied',
                    'BUSY': 'A target is pending host arrival confirmation',
                    'REQUEST_CONFLICT': 'Request ID was previously used with another payload',
                    'DEVICE_ERROR': 'Scene callback failed; external state is uncertain'}
        return self._envelope(request_id=request_id, status='failed' if code == 'DEVICE_ERROR' else 'rejected',
                              error={'code': code, 'message': messages[code], 'retryable': False,
                                     'side_effects': side_effects})

    def _instance_ok(self, instance_id):
        return instance_id is None or isinstance(instance_id, str) and instance_id == self.instance_id

    @staticmethod
    def _validate(schema, value):
        if 'anyOf' in schema:
            return any(ViciEutaValveRuntime._validate(s, value) for s in schema['anyOf'])
        kind = schema['type']
        if kind == 'null':
            return value is None
        if kind == 'integer':
            return type(value) is int and ('minimum' not in schema or value >= schema['minimum']) and ('maximum' not in schema or value <= schema['maximum'])
        if kind == 'number':
            return _veuta_number(value) and ('minimum' not in schema or value >= schema['minimum']) and ('maximum' not in schema or value <= schema['maximum'])
        if kind == 'boolean':
            return type(value) is bool
        if kind == 'string':
            return isinstance(value, str) and ('enum' not in schema or value in schema['enum'])
        if kind == 'array':
            return isinstance(value, list) and len(value) >= schema.get('minItems', 0) and len(value) <= schema.get('maxItems', len(value)) and all(ViciEutaValveRuntime._validate(schema['items'], v) for v in value)
        if kind == 'object':
            return isinstance(value, dict) and set(value) == set(schema['properties']) and all(ViciEutaValveRuntime._validate(s, value[k]) for k, s in schema['properties'].items())
        return False

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._error('INVALID_ARGUMENT')
            if not self._instance_ok(instance_id) or asset_type_id is not None and asset_type_id != VICI_EUTA_ASSET_TYPE_ID:
                return self._error('UNKNOWN_INSTANCE')
            result = _veuta_copy.deepcopy(self._metadata)
            result.pop('host_capabilities')
            return result

    def read(self, *, state_ids=None, instance_id=None):
        with self._lock:
            if not self._instance_ok(instance_id):
                return self._error('UNKNOWN_INSTANCE')
            ids = list(self._metadata['state_slots']) if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
                return self._error('INVALID_ARGUMENT')
            if any(key not in self._metadata['state_slots'] for key in ids):
                return self._error('ACCESS_DENIED')
            observations = {}
            for key in ids:
                value = self._state[key]
                reason = 'unknown_configuration_specification' if key == 'valve.switching_time_s' else 'no_confirmed_position_or_connection'
                observations[key] = {'value': _veuta_copy.deepcopy(value), 'unit': self._metadata['state_slots'][key]['unit'],
                                     'quality': 'unavailable' if value is None else 'estimated' if key in ('valve.position', 'valve.in_position') else 'valid',
                                     'sample_time_s': self._time_s, 'sample_window_s': None,
                                     'unavailable_reason': reason if value is None else None}
            return self._envelope(observations=observations)

    def write(self, *, state_id, value, request_id, instance_id=None):
        with self._lock:
            if not self._instance_ok(instance_id):
                return self._error('UNKNOWN_INSTANCE', request_id)
            slot = self._metadata['state_slots'].get(state_id) if isinstance(state_id, str) else None
            if slot is None or slot['write_via'] is None:
                return self._error('ACCESS_DENIED', request_id)
            route = slot['write_via']
            return self.invoke(capability_id=route['capability_id'], arguments={route['argument']: value}, request_id=request_id, instance_id=instance_id)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        return self._invoke(capability_id=capability_id, arguments=arguments, request_id=request_id, instance_id=instance_id, host=False)

    def host_arrive(self, *, position, world_time_s, request_id):
        return self._invoke(capability_id='host.arrive', arguments={'position': position, 'world_time_s': world_time_s}, request_id=request_id, host=True)

    def host_set_time(self, *, world_time_s, request_id):
        return self._invoke(capability_id='host.set_time', arguments={'world_time_s': world_time_s}, request_id=request_id, host=True)

    def debug_snapshot(self):
        """Trusted test/host view, never a discoverable agent capability."""
        with self._lock:
            return {'instance_id': self.instance_id, 'state_revision': self._revision,
                    'world_time_s': self._time_s, 'state': _veuta_copy.deepcopy(self._state)}

    def _invoke(self, *, capability_id, arguments, request_id, host, instance_id=None):
        with self._lock:
            if not self._instance_ok(instance_id):
                return self._error('UNKNOWN_INSTANCE', request_id)
            capabilities = self._metadata['host_capabilities'] if host else self._metadata['capabilities']
            cap = capabilities.get(capability_id) if isinstance(capability_id, str) else None
            if cap is None:
                return self._error('UNKNOWN_CAPABILITY', request_id)
            if not isinstance(request_id, str) or not request_id.strip() or not self._validate(cap['input_schema'], arguments):
                return self._error('INVALID_ARGUMENT', request_id)
            digest = _veuta_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            key = ('host' if host else 'agent', request_id)
            if key in self._requests:
                prior_digest, prior_result = self._requests[key]
                return _veuta_copy.deepcopy(prior_result) if prior_digest == digest else self._error('REQUEST_CONFLICT', request_id)
            state = _veuta_copy.deepcopy(self._state)
            time_s = self._time_s
            if host and arguments['world_time_s'] < self._time_s:
                return self._error('PRECONDITION_FAILED', request_id)
            if capability_id == 'valve.select_position':
                position = arguments['position']
                if state['valve.target_position'] is not None and not state['valve.in_position']:
                    return self._error('BUSY', request_id)
                if state['valve.in_position'] and state['valve.position'] == position:
                    return self._error('PRECONDITION_FAILED', request_id)
                state.update({'valve.target_position': position, 'valve.position': None, 'valve.in_position': False,
                              'route.connected_port_id': None, 'route.connections': []})
                result = {k: state[k] for k in ('valve.target_position', 'valve.in_position', 'route.connected_port_id')}
            elif capability_id == 'host.arrive':
                position = arguments['position']
                if state['valve.target_position'] != position or state['valve.in_position']:
                    return self._error('PRECONDITION_FAILED', request_id)
                angle = (position - 1) * _veuta_math.pi / 3
                if self._scene_adapter is not None:
                    try:
                        self._scene_adapter(self.instance_id, {'scene.rotor_angle_rad': angle})
                    except Exception:
                        failure = self._error('DEVICE_ERROR', request_id, side_effects='unknown')
                        self._requests[key] = (digest, _veuta_copy.deepcopy(failure))
                        return failure
                state.update({'valve.position': position, 'valve.in_position': True,
                              'route.connected_port_id': 'port_%d' % position,
                              'route.connections': [['port_common', 'port_%d' % position]],
                              'scene.rotor_angle_rad': angle,
                              'scene.sync_status': 'synced' if self._scene_adapter is not None else 'unbound'})
                time_s = float(arguments['world_time_s'])
                result = {k: state[k] for k in ('valve.position', 'valve.in_position', 'route.connected_port_id', 'route.connections', 'scene.rotor_angle_rad', 'scene.sync_status')}
            else:
                if arguments['world_time_s'] == self._time_s:
                    return self._error('PRECONDITION_FAILED', request_id)
                time_s = float(arguments['world_time_s'])
                result = {'world_time_s': time_s}
            self._state = state
            self._time_s = time_s
            self._revision += 1
            response = self._envelope(request_id=request_id, result=_veuta_copy.deepcopy(result))
            self._requests[key] = (digest, _veuta_copy.deepcopy(response))
            return response
# END CHEM-182 VICI EUTA APPEND-ONLY RUNTIME


# BEGIN CHEM-179 ZIATH DP5 MIRAGE APPEND-ONLY RUNTIME
"""CHEM-179 synthetic barcode-observation adapter, no optical decoder/hardware.

The trusted host owns scenarios, principals, callbacks, and the common clock.
Only hand ZiathDP5Session to an agent. Python access control is not a sandbox.
"""
import copy as _dp5_copy
import csv as _dp5_csv
import io as _dp5_io
import json as _dp5_json
import math as _dp5_math
import threading as _dp5_threading

ZIATH_DP5_TYPE = 'instrument.ziath_dp5_mirage_2023'
ZIATH_DP5_FORMATS = (24, 48, 96, 240)
ZIATH_DP5_SCAN_S = 2.0


def _dp5_number(value):
    try:
        return type(value) in (int, float) and _dp5_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _dp5_obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _dp5_num(unit, low=0, high=None):
    value = {'type': 'number', 'minimum': low, 'x-unit': unit}
    if high is not None:
        value['maximum'] = high
    return value


def _dp5_nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def _dp5_slot(schema, unit, origin, visibility='agent', via=None):
    return {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': visibility,
            'access': 'read_write' if via else 'read_only', 'reference_conditions': None,
            'write_via': {'capability_id': via[0], 'parameter': via[1], 'fixed_arguments': {}} if via else None,
            'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}


_DP5_STRING = {'type': 'string'}
_DP5_LIST = {'type': 'array', 'items': _DP5_STRING}
_DP5_FORMAT = {'type': 'integer', 'enum': list(ZIATH_DP5_FORMATS), 'x-unit': '1'}
_DP5_EXPORT = {'type': 'string', 'enum': ['json', 'csv']}
_DP5_SCAN_STATUS = {'type': 'string', 'enum': ['idle', 'running', 'succeeded', 'cancelled']}
_DP5_ROW = _dp5_obj({'well_id': _DP5_STRING, 'tube_id': _dp5_nullable(_DP5_STRING),
    'status': {'type': 'string', 'enum': ['read', 'missing', 'matched', 'mismatch', 'unexpected']},
    'expected_tube_id': _dp5_nullable(_DP5_STRING)})
_DP5_RESULT = _dp5_obj({'rack_id': _DP5_STRING, 'rack_format': _DP5_FORMAT,
    'mapping': {'type': 'array', 'items': _DP5_ROW}, 'missing_wells': _DP5_LIST,
    'mismatch_wells': _DP5_LIST, 'unexpected_wells': _DP5_LIST, 'duplicate_tube_ids': _DP5_LIST,
    'identity_matching': {'type': 'boolean'}, 'completed_time_s': _dp5_num('s'),
    'quality': {'type': 'string', 'const': 'estimated'}})
_DP5_PRIVATE_PAIRS = {'type': 'array', 'items': _dp5_obj({'well_id': _DP5_STRING, 'tube_id': _dp5_nullable(_DP5_STRING)})}
_DP5_SLOTS = {
    'power.enabled': _dp5_slot({'type': 'boolean'}, '1', 'setpoint', via=('power.set', 'enabled')),
    'configuration.rack_format': _dp5_slot(_DP5_FORMAT, '1', 'setpoint'),
    'configuration.identity_matching': _dp5_slot({'type': 'boolean'}, '1', 'setpoint'),
    'configuration.export_format': _dp5_slot(_DP5_EXPORT, '1', 'setpoint'),
    'rack.loaded': _dp5_slot({'type': 'boolean'}, '1', 'scene'),
    'rack.rack_id': _dp5_slot(_dp5_nullable(_DP5_STRING), '1', 'scene'),
    'rack.well_ids': _dp5_slot(_DP5_LIST, '1', 'scene'),
    'rack.attachment_constraint_id': _dp5_slot(_dp5_nullable(_DP5_STRING), '1', 'scene'),
    'scene.rack_lift_m': _dp5_slot(_dp5_num('m', 0, 0.10), 'm', 'scene', via=('scene.set_rack_lift', 'lift_m')),
    'scene.rack_format': _dp5_slot(_DP5_FORMAT, '1', 'scene'),
    'acquisition.status': _dp5_slot(_DP5_SCAN_STATUS, '1', 'derived'),
    'acquisition.elapsed_s': _dp5_slot(_dp5_num('s', 0, 2), 's', 'derived'),
    'acquisition.progress': _dp5_slot(_dp5_num('1', 0, 1), '1', 'derived'),
    'results.data': _dp5_slot(_dp5_nullable(_DP5_RESULT), '1', 'derived'),
    'truth.visible_barcodes': _dp5_slot(_DP5_PRIVATE_PAIRS, '1', 'process_truth', 'debug'),
    'truth.task_manifest': _dp5_slot(_DP5_PRIVATE_PAIRS, '1', 'process_truth', 'debug'),
}
_DP5_OPERATION = _dp5_obj({'operation_id': _DP5_STRING, 'status': _DP5_SCAN_STATUS,
    'elapsed_s': _dp5_num('s', 0, 2), 'progress': _dp5_num('1', 0, 1),
    'results_available': {'type': 'boolean'}})
_DP5_COMPONENTS = {
    'power.set': ['power_data_panel', 'status_indicator'],
    'configuration.set': ['rack_carriage'],
    'acquisition.start': ['optical_module', 'scan_window'],
    'acquisition.poll': ['optical_module', 'scan_window'],
    'acquisition.cancel': ['optical_module', 'scan_window'],
    'results.export': ['power_data_panel'],
    'scene.set_rack_lift': ['rack_carriage'],
}
_DP5_INVALIDATE = ['acquisition.status', 'acquisition.elapsed_s', 'acquisition.progress', 'results.data']
_DP5_LIMITATIONS = [
    '仅处理可信宿主提供的合成可见条码观测，不读取真实图像，不实现像素识别、遮挡、霜雾或误识率模型。',
    '2.0 s为表中约2 s的固定仿真近似；不是实测扫描时间分布。完成时一次公布整架结果，取消保留时间进度但不发布部分管ID。',
    '24/48/96/240为支持容量；每个孔位ID由宿主明确提供，未知行列排布未内置为厂家参数。空管与漏读不能由null条码区分，均标missing。',
    '身份匹配仅比对宿主任务清单与条码观测；不确认真实样品身份或化学组成，无化学过程。',
    '外部管架抬升0..0.10 m是假设场景行程，不是Mirage自带电机；机械同步依赖host scene_adapter的True确认。',
    'attach/release仅登记宿主约束回调确认的附着事件；无接触求解或机器人抓取，robot与hardware层absent。',
    '请求去重只在当前宿主内存有效；API可见性过滤不是Python、共享文件系统或进程安全隔离。',
    '导出返回内存中的JSON/CSV文本，不写文件、不发送网络；CSV单元格仅承载合成观测数据。',
]


def export_ziath_dp5_contract_metadata():
    slots = _dp5_copy.deepcopy(_DP5_SLOTS)
    for sid, slot in slots.items():
        slot['read_binding'] = 'ZiathDP5Session.read(instance_id=..., state_ids=[%r])' % sid
    slots['scene.rack_lift_m']['reference_conditions'] = 'Rack carriage local +Z relative to seated rack; external lift assumption 0..0.10 m'
    specs = {
        'power.set': ({'enabled': {'type': 'boolean'}}, _dp5_obj({'enabled': {'type': 'boolean'}}), {'enabled': True}, ['power.enabled', 'acquisition.status'], 'device_command', '设置仿真供电与显示；断电取消正在扫描的任务并保留已用时间。'),
        'configuration.set': ({'rack_format': _DP5_FORMAT, 'identity_matching': {'type': 'boolean'}, 'export_format': _DP5_EXPORT}, _dp5_obj({'rack_format': _DP5_FORMAT, 'identity_matching': {'type': 'boolean'}, 'export_format': _DP5_EXPORT}), {'rack_format': 96, 'identity_matching': True, 'export_format': 'json'}, ['configuration.rack_format', 'configuration.identity_matching', 'configuration.export_format', 'scene.rack_format'] + _DP5_INVALIDATE, 'configuration', '设置容量、身份匹配与默认导出格式；清除上次结果。'),
        'acquisition.start': ({}, _DP5_OPERATION, {}, _DP5_INVALIDATE, 'device_command', '启动约2 s的合成整架读取；accepted不含管ID。'),
        'acquisition.poll': ({'operation_id': _DP5_STRING}, _DP5_OPERATION, {'operation_id': 'scan_demo_1'}, [], 'query', '读取本身份发起的操作进度；不推进时间且不返回管ID。'),
        'acquisition.cancel': ({'operation_id': _DP5_STRING}, _DP5_OPERATION, {'operation_id': 'scan_demo_1'}, ['acquisition.status'], 'device_command', '取消本身份的运行操作；保留已经消耗的时间与进度，不发布未完成结果。'),
        'results.export': ({'format': _DP5_EXPORT}, _dp5_obj({'format': _DP5_EXPORT, 'media_type': _DP5_STRING, 'content': _DP5_STRING, 'row_count': {'type': 'integer', 'minimum': 0, 'maximum': 240, 'x-unit': '1'}}), {'format': 'json'}, [], 'query', '将已完成的合成管ID-孔位映射、漏读和不匹配状态导出为内存文本。'),
        'scene.set_rack_lift': ({'lift_m': _dp5_num('m', 0, .10)}, _dp5_obj({'lift_m': _dp5_num('m', 0, .10)}), {'lift_m': .05}, ['scene.rack_lift_m'], 'configuration', '由宿主场景回调移动外部管架；不表示仪器内置致动或机器人操作。'),
    }
    prereq = {
        'power.set': ['scene_adapter已绑定并返回True确认。'],
        'configuration.set': ['已上电；没有正在扫描的操作；scene_adapter返回True确认；格式与已装管架一致。'],
        'acquisition.start': ['已上电；已装管架；无正在扫描任务；管架抬升为0；格式匹配。'],
        'acquisition.poll': ['operation_id属于当前实例与宿主登记的调用者。'],
        'acquisition.cancel': ['operation_id属于当前实例与宿主登记的调用者。'],
        'results.export': ['本次扫描已完成且结果可用。'],
        'scene.set_rack_lift': ['已装管架；没有正在扫描的操作；scene_adapter已绑定并返回True确认。'],
    }
    caps = {}
    for cid, (props, output, example, effects, kind, description) in specs.items():
        scene = cid in ('power.set', 'configuration.set', 'scene.set_rack_lift')
        observed = {'power.set': ['power.enabled'], 'configuration.set': ['configuration.rack_format', 'configuration.identity_matching', 'configuration.export_format'],
            'acquisition.start': ['acquisition.status', 'acquisition.elapsed_s', 'acquisition.progress'],
            'acquisition.poll': ['acquisition.status', 'acquisition.elapsed_s', 'acquisition.progress'],
            'acquisition.cancel': ['acquisition.status', 'acquisition.elapsed_s', 'acquisition.progress'],
            'results.export': ['results.data'], 'scene.set_rack_lift': ['scene.rack_lift_m']}[cid]
        entries = [{'state_id': s, 'timing': 'immediate', 'description_zh': '同步更新此声明状态；拒绝时不改变内部状态。'} for s in effects]
        if cid == 'acquisition.start':
            entries += [{'state_id': s, 'timing': 'on_step', 'description_zh': '仅由宿主共同时间推进，约2 s完成时发布合成结果。'} for s in _DP5_INVALIDATE]
        caps[cid] = {'name_zh': description, 'description_zh': description, 'kind': kind, 'visibility': 'agent', 'availability': 'implemented',
            'component_ids': _DP5_COMPONENTS[cid], 'input_schema': _dp5_obj(_dp5_copy.deepcopy(props)), 'output_schema': _dp5_copy.deepcopy(output),
            'state_effects': entries, 'observes': observed, 'example_arguments': [_dp5_copy.deepcopy(example)], 'preconditions_zh': prereq[cid],
            'binding': 'basic40_runtime_automation.ZiathDP5Session.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
            'implementation': {'process': 'implemented' if cid != 'scene.set_rack_lift' else 'not_applicable', 'scene': 'implemented' if scene else 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'implemented'},
            'model_effects': {'mode': 'host_required' if scene else 'none', 'binding_ids': [], 'description_zh': 'scene_adapter(instance_id,targets)须返回True；缺失返回unsupported；外部异常不提交内部状态但外部副作用可能未知。' if scene else '只改变数据与任务状态，无新的可见机械动作。'},
            'execution_notes': {'mode': 'asynchronous' if cid == 'acquisition.start' else 'synchronous', 'time_domain': 'simulation', 'completion_zh': '宿主累计扫描时间达到2.0 s并发布整架结果。' if cid == 'acquisition.start' else '返回succeeded表示本调用完成。', 'cancellable': cid == 'acquisition.start', 'partial_failure_zh': '取消保留elapsed_s/progress；未完成扫描没有可导出的部分管ID。', 'retry_zh': '同一request_id与参数返回缓存响应，参数冲突拒绝；当前进程内存有效。'},
            'errors': [{'code': code, 'description_zh': meaning, 'side_effects': side} for code, meaning, side in [
                ('INVALID_ARGUMENT', '参数类型、范围或多余字段错误。', 'none'), ('UNKNOWN_INSTANCE', '实例不存在或不在当前身份范围。', 'none'),
                ('UNKNOWN_CAPABILITY', '能力不存在或不对当前身份公开。', 'none'), ('PRECONDITION_FAILED', '当前供电、管架或结果状态不允许操作。', 'none'),
                ('BUSY', '扫描资源正在使用。', 'none'), ('REQUEST_CONFLICT', '同一请求ID被用于不同参数。', 'none'),
                ('UNSUPPORTED', '需要的场景回调尚未绑定。', 'none'), ('DEVICE_ERROR', '外部回调未确认；内部状态未提交，外部可能已部分改变。', 'unknown'),
                ('UNKNOWN_OPERATION', '操作不存在或不属于当前实例和调用者。', 'none')]]}
    return {'asset_type_id': ZIATH_DP5_TYPE, 'asset_version': '1.0.2', 'catalog_id': 'CHEM-179', 'state_slots': slots, 'capabilities': caps,
        'parameters': {'scan_time_s': {'value': 2.0, 'unit': 's', 'evidence': 'catalog_approximation', 'note_zh': '约2 s规格用于固定时长仿真近似。'},
            'supported_rack_capacities': {'value': list(ZIATH_DP5_FORMATS), 'unit': '1', 'evidence': 'catalog'},
            'rack_lift_limit_m': {'value': .10, 'unit': 'm', 'evidence': 'assumed'},
            'barcode_pixel_size_m': {'value': None, 'unit': 'm', 'evidence': 'unknown'}, 'misidentification_rate': {'value': None, 'unit': '1', 'evidence': 'unknown'},
            'occlusion_limit': {'value': None, 'unit': '1', 'evidence': 'unknown'}, 'frost_response': {'value': None, 'unit': '1', 'evidence': 'unknown'}},
        'host_api': {
            'create_instance': {'entrypoint': 'ZiathDP5Runtime.create_instance(instance_id, scene_adapter=None)', 'description_zh': '独立状态、请求缓存和场景回调；初始未上电。'},
            'register_principal': {'entrypoint': 'ZiathDP5Runtime.register_principal(principal_id, visibility, instance_ids=None)', 'description_zh': '身份与可见性只能由宿主登记。'},
            'host_load_rack': {'entrypoint': 'ZiathDP5Runtime.host_load_rack(instance_id, rack_id=..., rack_format=..., well_ids=..., visible_barcodes=..., task_manifest=...)', 'description_zh': '宿主显式传入容量个唯一孔位及其合成可见条码/任务清单；禁止扫描中替换；仅调试会话可读预扫描输入。'},
            'host_step': {'entrypoint': 'ZiathDP5Runtime.host_step(dt_s, world_time_s=...)', 'description_zh': '正有限dt与单调共同时间一致；推进此宿主全部实例，完成后一次发布整架结果。'},
            'host_attach': {'entrypoint': 'ZiathDP5Runtime.host_attach(instance_id, constraint_id=..., attachment_adapter=...)', 'event': 'attach', 'description_zh': '回调(instance_id,{event:attach,rack_id:...,constraint_id:...})须返回True；只登记宿主已实现约束，不实现抓取。'},
            'host_release': {'entrypoint': 'ZiathDP5Runtime.host_release(instance_id, attachment_adapter=...)', 'event': 'release', 'description_zh': '回调须确认释放现存constraint_id；保留管架装载和位置，不假设容器回原处。'}},
        'limitations': list(_DP5_LIMITATIONS), 'output_schema_scope': 'capability output_schema validates response.result; all examples contain synthetic data only'}


class ZiathDP5Runtime:
    """Trusted multi-instance host. No hardware entrypoints are provided."""
    def __init__(self):
        self.time_s = 0.0
        self._instances = {}
        self._principals = {}
        self._requests = {}
        self._operations = {}
        self._next_operation = 1
        self._lock = _dp5_threading.RLock()

    def create_instance(self, instance_id, scene_adapter=None):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or (scene_adapter is not None and not callable(scene_adapter)):
                raise ValueError('Unique instance ID and callable optional adapter required')
            state = {'power.enabled': False, 'configuration.rack_format': 96, 'configuration.identity_matching': True, 'configuration.export_format': 'json',
                'rack.loaded': False, 'rack.rack_id': None, 'rack.well_ids': [], 'rack.attachment_constraint_id': None, 'scene.rack_lift_m': 0.0,
                'scene.rack_format': 96, 'acquisition.status': 'idle', 'acquisition.elapsed_s': 0.0, 'acquisition.progress': 0.0, 'results.data': None,
                'truth.visible_barcodes': [], 'truth.task_manifest': []}
            self._instances[instance_id] = {'state': state, 'revision': 0, 'adapter': scene_adapter, 'rack_format': None, 'active_operation': None}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Host-assigned unique identity with valid visibility required')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(k not in self._instances for k in instance_ids)):
                raise ValueError('Invalid instance scope')
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError('Host registration required')
        return ZiathDP5Session(self, principal_id)

    def _get(self, instance_id, principal_id=None):
        if not isinstance(instance_id, str) or instance_id not in self._instances:
            return None
        if principal_id is not None:
            scope = self._principals[principal_id][1]
            if scope is not None and instance_id not in scope:
                return None
        return self._instances[instance_id]

    @staticmethod
    def _invalidate(item):
        item['state'].update({'acquisition.status': 'idle', 'acquisition.elapsed_s': 0.0, 'acquisition.progress': 0.0, 'results.data': None})
        item['active_operation'] = None

    def host_load_rack(self, instance_id, *, rack_id, rack_format, well_ids, visible_barcodes, task_manifest):
        with self._lock:
            item = self._get(instance_id)
            if item is None or type(rack_format) is not int or rack_format not in ZIATH_DP5_FORMATS or not isinstance(rack_id, str) or not rack_id:
                raise ValueError('Invalid rack identity or format')
            if not isinstance(well_ids, list) or len(well_ids) != rack_format or any(not isinstance(w, str) or not w for w in well_ids) or len(set(well_ids)) != rack_format:
                raise ValueError('Exactly capacity distinct explicit well IDs required')
            for mapping in (visible_barcodes, task_manifest):
                if not isinstance(mapping, dict) or any(k not in well_ids or not isinstance(v, str) or not v for k, v in mapping.items()):
                    raise ValueError('Mappings require known wells and nonempty IDs; omitted observations are missing reads')
            if item['state']['acquisition.status'] == 'running' or item['state']['rack.attachment_constraint_id'] is not None:
                raise ValueError('Cannot replace a scanning or attached rack')
            if rack_format != item['state']['configuration.rack_format']:
                raise ValueError('Configure matching capacity before rack load')
            state = item['state']
            state.update({'rack.loaded': True, 'rack.rack_id': rack_id, 'rack.well_ids': list(well_ids),
                'truth.visible_barcodes': [{'well_id': w, 'tube_id': visible_barcodes.get(w)} for w in well_ids],
                'truth.task_manifest': [{'well_id': w, 'tube_id': task_manifest.get(w)} for w in well_ids]})
            item['rack_format'] = rack_format
            self._invalidate(item)
            item['revision'] += 1

    def host_attach(self, instance_id, *, constraint_id, attachment_adapter):
        with self._lock:
            item = self._get(instance_id)
            if item is None or not isinstance(constraint_id, str) or not constraint_id or not callable(attachment_adapter):
                raise ValueError('Valid instance, constraint ID and host callback required')
            state = item['state']
            if not state['rack.loaded'] or state['rack.attachment_constraint_id'] is not None or state['acquisition.status'] == 'running':
                raise ValueError('Rack must be loaded, unattached, and idle')
            event = {'event': 'attach', 'rack_id': state['rack.rack_id'], 'constraint_id': constraint_id}
            if attachment_adapter(instance_id, _dp5_copy.deepcopy(event)) is not True:
                raise RuntimeError('Attachment not confirmed; external effect may be unknown')
            state['rack.attachment_constraint_id'] = constraint_id
            item['revision'] += 1
            return event

    def host_release(self, instance_id, *, attachment_adapter):
        with self._lock:
            item = self._get(instance_id)
            if item is None or not callable(attachment_adapter) or item['state']['rack.attachment_constraint_id'] is None or item['state']['acquisition.status'] == 'running':
                raise ValueError('Idle attached rack and host callback required')
            event = {'event': 'release', 'rack_id': item['state']['rack.rack_id'], 'constraint_id': item['state']['rack.attachment_constraint_id']}
            if attachment_adapter(instance_id, _dp5_copy.deepcopy(event)) is not True:
                raise RuntimeError('Release not confirmed; external effect may be unknown')
            item['state']['rack.attachment_constraint_id'] = None
            item['revision'] += 1
            return event

    @staticmethod
    def _scan_result(item, completed_s):
        s = item['state']
        observations = {row['well_id']: row['tube_id'] for row in s['truth.visible_barcodes']}
        manifest = {row['well_id']: row['tube_id'] for row in s['truth.task_manifest']}
        match = s['configuration.identity_matching']
        rows, missing, mismatch, unexpected, seen, duplicates = [], [], [], [], set(), set()
        for well in s['rack.well_ids']:
            code, expected = observations[well], manifest[well] if match else None
            status = 'missing' if code is None else 'read'
            if code is None:
                missing.append(well)
            elif match:
                if expected is None:
                    status = 'unexpected'
                    unexpected.append(well)
                elif code == expected:
                    status = 'matched'
                else:
                    status = 'mismatch'
                    mismatch.append(well)
            if code is not None:
                if code in seen:
                    duplicates.add(code)
                seen.add(code)
            rows.append({'well_id': well, 'tube_id': code, 'expected_tube_id': expected, 'status': status})
        return {'rack_id': s['rack.rack_id'], 'rack_format': item['rack_format'], 'mapping': rows, 'missing_wells': missing,
            'mismatch_wells': mismatch, 'unexpected_wells': unexpected, 'duplicate_tube_ids': sorted(duplicates),
            'identity_matching': match, 'completed_time_s': completed_s, 'quality': 'estimated'}

    def host_step(self, dt_s, *, world_time_s):
        with self._lock:
            if not _dp5_number(dt_s) or dt_s <= 0 or not _dp5_number(world_time_s) or world_time_s <= self.time_s or not _dp5_math.isclose(world_time_s - self.time_s, dt_s, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError('Positive finite dt must agree with the common monotonic world clock')
            if not _dp5_number(self.time_s + dt_s):
                raise ValueError('Clock overflow')
            for item in self._instances.values():
                if item['state']['acquisition.status'] != 'running':
                    continue
                op = self._operations[item['active_operation']]
                elapsed = min(ZIATH_DP5_SCAN_S, world_time_s - op['started_s'])
                item['state']['acquisition.elapsed_s'] = elapsed
                item['state']['acquisition.progress'] = elapsed / ZIATH_DP5_SCAN_S
                op['elapsed_s'] = elapsed
                if elapsed >= ZIATH_DP5_SCAN_S:
                    item['state']['acquisition.status'] = 'succeeded'
                    op['status'] = 'succeeded'
                    item['state']['results.data'] = self._scan_result(item, op['started_s'] + ZIATH_DP5_SCAN_S)
                item['revision'] += 1
            self.time_s = float(world_time_s)
            return {'time_s': self.time_s}

    def debug_snapshot(self):
        with self._lock:
            return _dp5_copy.deepcopy({'time_s': self.time_s, 'instances': {k: {field: val for field, val in v.items() if field != 'adapter'} for k, v in self._instances.items()}, 'operations': self._operations})

    def _response(self, instance_id, request_id=None, *, status='succeeded', result=None, operation_id=None, code=None, side_effects='none', message=None):
        item = self._get(instance_id)
        return {'protocol_version': '1.0.0', 'instance_id': instance_id, 'request_id': request_id, 'operation_id': operation_id,
            'status': status, 'state_revision': item['revision'] if item else None, 'clock': {'domain': 'simulation', 'time_s': self.time_s},
            'result': _dp5_copy.deepcopy(result), 'observations': {},
            'error': None if code is None else {'code': code, 'message': message or 'Operation not permitted or could not be confirmed: ' + code,
                'retryable': False, 'side_effects': side_effects, 'known_progress': None}}

    @staticmethod
    def _operation_result(op):
        return {'operation_id': op['id'], 'status': op['status'], 'elapsed_s': op['elapsed_s'],
            'progress': op['elapsed_s'] / ZIATH_DP5_SCAN_S, 'results_available': op['status'] == 'succeeded'}

    def _invoke(self, principal_id, instance_id, cid, args, request_id):
        with self._lock:
            reject = lambda code, status='rejected', side='none', why=None: self._response(instance_id, request_id, status=status, code=code, side_effects=side, message=why)
            item = self._get(instance_id, principal_id)
            if item is None:
                return reject('UNKNOWN_INSTANCE')
            if not isinstance(cid, str) or cid not in _DP5_COMPONENTS:
                return reject('UNKNOWN_CAPABILITY')
            if not isinstance(request_id, str) or not request_id or not isinstance(args, dict):
                return reject('INVALID_ARGUMENT')
            try:
                fingerprint = _dp5_json.dumps([cid, args], sort_keys=True, allow_nan=False, separators=(',', ':'))
            except (TypeError, ValueError, OverflowError):
                return reject('INVALID_ARGUMENT')
            key = (principal_id, instance_id, request_id)
            if key in self._requests:
                old_fingerprint, old_response = self._requests[key]
                return _dp5_copy.deepcopy(old_response) if old_fingerprint == fingerprint else reject('REQUEST_CONFLICT')
            required = {'power.set': ['enabled'], 'configuration.set': ['rack_format', 'identity_matching', 'export_format'],
                'acquisition.start': [], 'acquisition.poll': ['operation_id'], 'acquisition.cancel': ['operation_id'],
                'results.export': ['format'], 'scene.set_rack_lift': ['lift_m']}[cid]
            valid = set(args) == set(required)
            if valid and cid == 'power.set': valid = type(args['enabled']) is bool
            if valid and cid == 'configuration.set': valid = type(args['rack_format']) is int and args['rack_format'] in ZIATH_DP5_FORMATS and type(args['identity_matching']) is bool and args['export_format'] in ('json', 'csv')
            if valid and cid in ('acquisition.poll', 'acquisition.cancel'): valid = isinstance(args['operation_id'], str) and bool(args['operation_id'])
            if valid and cid == 'results.export': valid = isinstance(args['format'], str) and args['format'] in ('json', 'csv')
            if valid and cid == 'scene.set_rack_lift': valid = _dp5_number(args['lift_m']) and 0 <= args['lift_m'] <= .10
            if not valid:
                return reject('INVALID_ARGUMENT')
            s = item['state']
            # 1.0.2: PRECONDITION_FAILED keeps its code (no finer one is declared); the message names the cause
            if cid in ('configuration.set', 'acquisition.start') and not s['power.enabled']:
                return reject('PRECONDITION_FAILED', why='The reader is switched off (power.set enabled=true).')
            if cid in ('configuration.set', 'acquisition.start', 'scene.set_rack_lift') and s['acquisition.status'] == 'running':
                return reject('BUSY')
            if cid == 'configuration.set' and s['rack.loaded'] and args['rack_format'] != item['rack_format']:
                return reject('PRECONDITION_FAILED', why='The loaded rack has %d positions; rack_format %d does not match it (remove the rack first).' % (item['rack_format'], args['rack_format']))
            if cid == 'acquisition.start' and (not s['rack.loaded'] or s['scene.rack_lift_m'] != 0.0 or s['configuration.rack_format'] != item['rack_format']):
                return reject('PRECONDITION_FAILED', why='No rack is on the reader.' if not s['rack.loaded'] else
                              'The rack is not seated (scene.set_rack_lift lift_m=0).' if s['scene.rack_lift_m'] != 0.0 else
                              'The configured rack format %d does not match the loaded %d-position rack (configuration.set).' % (s['configuration.rack_format'], item['rack_format']))
            if cid == 'scene.set_rack_lift' and not s['rack.loaded']:
                return reject('PRECONDITION_FAILED', why='No rack is on the reader.')
            op = None
            if cid in ('acquisition.poll', 'acquisition.cancel'):
                op = self._operations.get(args['operation_id'])
                if op is None or op['instance_id'] != instance_id or op['principal_id'] != principal_id:
                    return reject('UNKNOWN_OPERATION')
            if cid == 'results.export' and s['results.data'] is None:
                return reject('PRECONDITION_FAILED', why='There are no scan results to export (run acquisition.start to completion).')
            if cid in ('power.set', 'configuration.set', 'scene.set_rack_lift'):
                if item['adapter'] is None:
                    return reject('UNSUPPORTED', 'unsupported')
                targets = {'power.set': {'power.enabled': args.get('enabled')}, 'configuration.set': {'scene.rack_format': args.get('rack_format')}, 'scene.set_rack_lift': {'scene.rack_lift_m': args.get('lift_m')}}[cid]
                try:
                    confirmed = item['adapter'](instance_id, _dp5_copy.deepcopy(targets)) is True
                except Exception:
                    confirmed = False
                if not confirmed:
                    response = reject('DEVICE_ERROR', 'failed', 'unknown')
                    self._requests[key] = (fingerprint, _dp5_copy.deepcopy(response))
                    return response
            status, operation_id = 'succeeded', None
            if cid == 'power.set':
                s['power.enabled'] = args['enabled']
                if not args['enabled'] and s['acquisition.status'] == 'running':
                    s['acquisition.status'] = 'cancelled'
                    self._operations[item['active_operation']]['status'] = 'cancelled'
                result = {'enabled': args['enabled']}
            elif cid == 'configuration.set':
                s.update({'configuration.rack_format': args['rack_format'], 'configuration.identity_matching': args['identity_matching'],
                    'configuration.export_format': args['export_format'], 'scene.rack_format': args['rack_format']})
                self._invalidate(item)
                result = _dp5_copy.deepcopy(args)
            elif cid == 'scene.set_rack_lift':
                s['scene.rack_lift_m'] = float(args['lift_m'])
                result = {'lift_m': s['scene.rack_lift_m']}
            elif cid == 'acquisition.start':
                self._invalidate(item)
                operation_id = 'scan_%s_%d' % (instance_id, self._next_operation)
                self._next_operation += 1
                op = {'id': operation_id, 'instance_id': instance_id, 'principal_id': principal_id, 'started_s': self.time_s, 'status': 'running', 'elapsed_s': 0.0}
                self._operations[operation_id] = op
                item['active_operation'] = operation_id
                s['acquisition.status'] = 'running'
                result = self._operation_result(op)
                status = 'accepted'
            elif cid in ('acquisition.poll', 'acquisition.cancel'):
                operation_id = op['id']
                if cid == 'acquisition.cancel' and op['status'] == 'running':
                    op['status'] = 'cancelled'
                    s['acquisition.status'] = 'cancelled'
                result = self._operation_result(op)
            else:
                data, fmt = s['results.data'], args['format']
                if fmt == 'json':
                    content = _dp5_json.dumps(data, ensure_ascii=False, sort_keys=True)
                    media_type = 'application/json'
                else:
                    buffer = _dp5_io.StringIO(newline='')
                    writer = _dp5_csv.DictWriter(buffer, fieldnames=['well_id', 'tube_id', 'status', 'expected_tube_id'])
                    writer.writeheader()
                    writer.writerows(data['mapping'])
                    content, media_type = buffer.getvalue(), 'text/csv'
                result = {'format': fmt, 'media_type': media_type, 'content': content, 'row_count': len(data['mapping'])}
            if cid not in ('acquisition.poll', 'results.export'):
                item['revision'] += 1
            response = self._response(instance_id, request_id, status=status, result=result, operation_id=operation_id)
            self._requests[key] = (fingerprint, _dp5_copy.deepcopy(response))
            return response


class ZiathDP5Session:
    """Agent-safe transport surface when host objects/files are not exposed."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal_id = host, principal_id

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                raise ValueError('Exactly one type or instance identifier required')
            if (asset_type_id is not None and asset_type_id != ZIATH_DP5_TYPE) or (instance_id is not None and self.__host._get(instance_id, self.__principal_id) is None):
                return self.__host._response(instance_id, status='rejected', code='UNKNOWN_INSTANCE')
            metadata = export_ziath_dp5_contract_metadata()
            metadata.pop('host_api')
            if self.__host._principals[self.__principal_id][0] == 'agent':
                metadata['state_slots'] = {k: v for k, v in metadata['state_slots'].items() if v['visibility'] == 'agent'}
            metadata['instance_id'] = instance_id
            if instance_id is not None and self.__host._instances[instance_id]['adapter'] is None:
                for cid in ('power.set', 'configuration.set', 'scene.set_rack_lift'):
                    metadata['capabilities'][cid]['availability'] = 'unsupported'
            return metadata

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            host = self.__host
            item = host._get(instance_id, self.__principal_id)
            if item is None:
                return host._response(instance_id, status='rejected', code='UNKNOWN_INSTANCE')
            visibility = host._principals[self.__principal_id][0]
            allowed = {sid for sid, slot in _DP5_SLOTS.items() if visibility != 'agent' or slot['visibility'] == 'agent'}
            if state_ids is None:
                state_ids = sorted(allowed)
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) or sid not in allowed for sid in state_ids):
                return host._response(instance_id, status='rejected', code='ACCESS_DENIED')
            response = host._response(instance_id)
            for sid in state_ids:
                value = _dp5_copy.deepcopy(item['state'][sid])
                quality = 'unavailable' if value is None else 'estimated' if sid == 'results.data' else 'valid'
                response['observations'][sid] = {'value': value, 'unit': _DP5_SLOTS[sid]['unit'], 'quality': quality,
                    'sample_time_s': host.time_s, 'sample_window_s': None}
                if value is None:
                    response['observations'][sid]['unavailable_reason'] = 'scan_not_completed' if sid == 'results.data' else 'not_configured'
            return response

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        return self.__host._invoke(self.__principal_id, instance_id, capability_id, arguments, request_id)

    def write(self, *, instance_id, state_id, value, request_id):
        slot = _DP5_SLOTS.get(state_id) if isinstance(state_id, str) else None
        if slot is None or slot['write_via'] is None:
            return self.__host._response(instance_id, request_id, status='rejected', code='ACCESS_DENIED')
        via = slot['write_via']
        return self.invoke(instance_id=instance_id, capability_id=via['capability_id'], arguments={via['parameter']: value}, request_id=request_id)

    def poll(self, *, instance_id, operation_id):
        with self.__host._lock:
            item = self.__host._get(instance_id, self.__principal_id)
            op = self.__host._operations.get(operation_id) if isinstance(operation_id, str) else None
            if item is None:
                return self.__host._response(instance_id, status='rejected', code='UNKNOWN_INSTANCE')
            if op is None or op['instance_id'] != instance_id or op['principal_id'] != self.__principal_id:
                return self.__host._response(instance_id, status='rejected', code='UNKNOWN_OPERATION')
            return self.__host._response(instance_id, result=self.__host._operation_result(op), operation_id=operation_id, status=op['status'])

    def cancel(self, *, instance_id, operation_id, request_id):
        return self.invoke(instance_id=instance_id, capability_id='acquisition.cancel', arguments={'operation_id': operation_id}, request_id=request_id)
# END CHEM-179 ZIATH DP5 MIRAGE APPEND-ONLY RUNTIME


# BEGIN CHEM-179 ZIATH DP5 HOST RACK REMOVAL EXTENSION
# Append-only extension: the host registers an already-performed external removal.
def _dp5_host_unload_rack(self, instance_id):
    with self._lock:
        item = self._get(instance_id)
        if item is None or not item['state']['rack.loaded'] or item['state']['acquisition.status'] == 'running' or item['state']['rack.attachment_constraint_id'] is not None:
            raise ValueError('Loaded idle unattached rack required for host removal registration')
        removed_id = item['state']['rack.rack_id']
        item['state'].update({'rack.loaded': False, 'rack.rack_id': None, 'rack.well_ids': [], 'truth.visible_barcodes': [], 'truth.task_manifest': []})
        item['rack_format'] = None
        self._invalidate(item)
        item['revision'] += 1
        return {'rack_id': removed_id, 'event': 'unload_registered'}
ZiathDP5Runtime.host_unload_rack = _dp5_host_unload_rack

_dp5_export_base = export_ziath_dp5_contract_metadata

def export_ziath_dp5_contract_metadata():
    metadata = _dp5_export_base()
    metadata['capabilities']['configuration.set']['description_zh'] = '设置容量、身份匹配和可供宿主UI读取的导出偏好；清除上次结果。results.export仍须显式提供format。'
    metadata['capabilities']['configuration.set']['name_zh'] = metadata['capabilities']['configuration.set']['description_zh']
    metadata['host_api']['host_unload_rack'] = {'entrypoint': 'ZiathDP5Runtime.host_unload_rack(instance_id)', 'description_zh': '可信宿主登记已完成的外部管架移出；要求idle且无附着，清空输入/结果，可随后更换容量。不创建机械位移；实际样品容器存在性由宿主世界管理。'}
    metadata['limitations'].append('configuration.export_format是供宿主UI读取的导出偏好；results.export必须显式选json/csv。host_unload_rack仅登记外部移出，容器存在性和移出动作由宿主世界管理。')
    return metadata
# END CHEM-179 ZIATH DP5 HOST RACK REMOVAL EXTENSION


# BEGIN CHEM-179 ZIATH DP5 HISTORICAL OPERATION RESULT AVAILABILITY
# A completed historical scan can outlive the current rack/result cache.
# Fresh queries must not claim those discarded results can still be exported.
_dp5_invoke_base = ZiathDP5Runtime._invoke

def _dp5_invoke_with_current_results(self, principal_id, instance_id, cid, args, request_id):
    with self._lock:
        key = (principal_id, instance_id, request_id) if isinstance(instance_id, str) and isinstance(request_id, str) else None
        cached_before = key in self._requests if key is not None else False
        response = _dp5_invoke_base(self, principal_id, instance_id, cid, args, request_id)
        if not cached_before and cid in ('acquisition.poll', 'acquisition.cancel') and response['error'] is None:
            item = self._instances[instance_id]
            response['result']['results_available'] = item['active_operation'] == response['operation_id'] and item['state']['results.data'] is not None
            if key in self._requests:
                fingerprint, _ = self._requests[key]
                self._requests[key] = (fingerprint, _dp5_copy.deepcopy(response))
        if response['error'] is not None and response['error']['code'] == 'UNKNOWN_INSTANCE':
            response['state_revision'] = None
        return response
ZiathDP5Runtime._invoke = _dp5_invoke_with_current_results

_dp5_poll_base = ZiathDP5Session.poll

def _dp5_poll_current_results(self, *, instance_id, operation_id):
    host = self._ZiathDP5Session__host
    with host._lock:
        response = _dp5_poll_base(self, instance_id=instance_id, operation_id=operation_id)
        if response['error'] is None:
            item = host._instances[instance_id]
            response['result']['results_available'] = item['active_operation'] == operation_id and item['state']['results.data'] is not None
        elif response['error']['code'] == 'UNKNOWN_INSTANCE':
            response['state_revision'] = None
        return response
ZiathDP5Session.poll = _dp5_poll_current_results

_dp5_metadata_before_history = export_ziath_dp5_contract_metadata

def export_ziath_dp5_contract_metadata():
    metadata = _dp5_metadata_before_history()
    metadata['limitations'].append('只保留当前管架/当前扫描的可导出结果；历史operation_id可查完成或取消状态，新的poll会将已清除结果标results_available=false。同request_id重放返回原响应，使用Session.poll读取最新进度。')
    return metadata
# END CHEM-179 ZIATH DP5 HISTORICAL OPERATION RESULT AVAILABILITY

# BEGIN CHEM-177 INTELLIXCAP S1 APPEND-ONLY RUNTIME
"""Semi-automatic CHEM-177 motion demonstration, not a thread/contact solver.

The trusted host supplies all motion assumptions and confirms external
attachment/release constraints. Give agents a session, never the host object.
API filtering is not a Python-introspection or filesystem security boundary.
"""
import copy as _ixs_copy
import hashlib as _ixs_hashlib
import json as _ixs_json
import math as _ixs_math
import threading as _ixs_threading
import uuid as _ixs_uuid

INTELLIXCAP_S1_TYPE = "instrument.azenta_intellixcap_s1_46_0001"
# Front panel (panel.press / panel.capture / panel.frame): the Power/Selection button, the tube-trigger disc and the six LEDs of the operation
# panel (user manual 393573 Rev. A; panel_ui.json beside the contract), run by scripts/asset_runtime/_panel_ui.py on the capabilities below.
# Device functions behind the panel: the five operation modes (Hold Cap: decap and hold the cap, recap with the held cap; Release Cap: decap and
# leave the cap loose, cap a loose cap; Decap Only; Cap Only; delayed release) and the three cycle-time settings (kept after a reboot).
INTELLIXCAP_S1_ASSET_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "assets/basic40_batch02/t5_automation/azenta_intellixcap_s1"
INTELLIXCAP_S1_MODES = ("hold_cap", "release_cap", "decap_only", "cap_only", "delayed_release")      # LED order on the panel (manual p.18)
INTELLIXCAP_S1_CYCLE_TIMES_S = {1: (1.1, 1.0), 2: (1.1, 0.7), 3: (1.9, 1.8)}   # setting: (decap, recap) s (manual p.22)
INTELLIXCAP_S1_RELEASE_DELAY_S = 1.0     # delayed-release hold before the jaws open (not stated: assumption)


def _ixs_number(value):
    try:
        return type(value) in (int, float) and _ixs_math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _ixs_text(value):
    return isinstance(value, str) and bool(value.strip())


def _ixs_num(unit, minimum=None, maximum=None, nullable=False):
    spec = {"type": "number", "x-unit": unit}
    if minimum is not None:
        spec["minimum"] = minimum
    if maximum is not None:
        spec["maximum"] = maximum
    return {"anyOf": [spec, {"type": "null"}]} if nullable else spec


def _ixs_obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_IXS_STRING_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_IXS_BOOL_NULL = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
_IXS_STATUS = {"type": "string", "enum": ["idle", "clamping", "awaiting_attach", "rotating", "awaiting_thread_result", "awaiting_release", "releasing", "succeeded", "failed", "cancelled"]}
_IXS_LIMITATIONS = [
    "仅实现半自动单管状态机与合成运动；完整无人流程需要宿主样品转运。robot 和 hardware 层 absent。",
    "所有行程、转角和阶段时长必须由可信宿主给出显式仿真假设与证据；没有厂家运动缺省值。",
    "典型周期小于3秒随耗材变化；该规格不是此仿真每次周期的保证，也不用作默认运动或超时参数。",
    "11–40 mm仅为盖径兼容范围；15/50 mL是已列兼容管容量类别，不能据容量推导管形或螺距。",
    "夹持和旋转仅提供位姿状态；扭矩、夹力、螺纹摩擦、转速和实际行程未知且不求解。",
    "attach/release事件需宿主实际建立/解除外部约束后确认；本运行层只登记constraint_id，不创建物理约束。",
    "运动结束后必须等待宿主螺纹结果；结果属于宿主断言，不是本资产自行检测到的真实开封盖成功。",
    "取消保留关节当前位置及已确认附着；不得假定盖已释放、样品状态回滚或自动复位。",
    "read/poll不推进时间；host_step推进同一运行宿主中全部实例。实例数据互相隔离。",
    "去重只在当前宿主内存中有效；API可见性过滤不构成Python反射或共享文件系统隔离。",
]


_IXS_PANEL = {}


def _ixs_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _IXS_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if "_chemsim_panel_ui" not in sys.modules:
                spec = importlib.util.spec_from_file_location("_chemsim_panel_ui", INTELLIXCAP_S1_ASSET_DIR.parents[3] / "scripts/asset_runtime/_panel_ui.py")
                sys.modules["_chemsim_panel_ui"] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules["_chemsim_panel_ui"])
            engine = sys.modules["_chemsim_panel_ui"]
        caps = set(export_intellixcap_s1_contract_metadata(panel=False)["capabilities"])
        spec = engine.load_spec(INTELLIXCAP_S1_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _IXS_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _IXS_PANEL


def _ixs_direction(mode, holding, last):
    """Direction of the next cycle in an operation mode (manual p.18): Hold Cap recaps with the held cap; Release Cap alternates."""
    if mode in ("decap_only", "delayed_release"):
        return "decap"
    if mode == "cap_only":
        return "recap"
    if mode == "hold_cap":
        return "recap" if holding else "decap"
    return "recap" if last == "decap" else "decap"


def export_intellixcap_s1_contract_metadata(panel=True):
    """Complete slot/capability fragments using artifact runtime and stable IDs.  panel_capabilities: the front-panel commands (panel.*)."""
    definitions = {
        "power.enabled": ({"type": "boolean"}, "1", "setpoint", "controller", "仿真供电开关；空闲才可修改", "power.set", "enabled"),
        "cycle.mode": ({"type": "string", "enum": ["decap", "recap"]}, "1", "setpoint", "controller", "下一周期的开盖或封盖模式", "cycle.set_mode", "mode"),
        "sample.consumable_id": (_IXS_STRING_NULL, "1", "setpoint", "sample_interface", "宿主登记的兼容耗材类型ID", "consumable.select", "consumable_id"),
        "sample.tube_id": (_IXS_STRING_NULL, "1", "scene", "sample_interface", "宿主放入的单管实例引用；不含样品成分", None, None),
        "sample.cap_id": (_IXS_STRING_NULL, "1", "scene", "sample_interface", "宿主放入的螺纹盖实例引用", None, None),
        "attachment.constraint_id": (_IXS_STRING_NULL, "1", "scene", "gripper", "宿主确认已存在的夹爪—管盖约束ID", None, None),
        "simulation.motion_configured": ({"type": "boolean"}, "1", "derived", "controller", "宿主是否明确配置了运动演示参数和证据", None, None),
        "rotation_rad": (_ixs_num("rad", nullable=True), "rad", "scene", "spindle", "绕主轴本地+Z的演示角；正向定义为开盖，非厂家方向证据", None, None),
        "jaw_gap_m": (_ixs_num("m", 0, nullable=True), "m", "scene", "gripper", "相对主轴的两个夹爪内表面总间距；不表示接触夹紧力", None, None),
        "cycle.status": (_IXS_STATUS, "1", "derived", "controller", "异步状态机阶段；等待宿主时不继续自动执行", None, None),
        "cycle.motion_completed": ({"type": "boolean"}, "1", "derived", "controller", "旋转演示已完成；不能单独证明螺纹开封盖成功", None, None),
        "cycle.host_confirmed": (_IXS_BOOL_NULL, "1", "derived", "controller", "宿主显式确认的螺纹结果，未确认是null", None, None),
        "truth.thread_state": ({"anyOf": [{"type": "string", "enum": ["open", "closed"]}, {"type": "null"}]}, "1", "process_truth", "sample_interface", "宿主确认的管盖开闭真值；运行层不自行计算", None, None),
        "cycle.operation_mode": ({"type": "string", "enum": list(INTELLIXCAP_S1_MODES)}, "1", "setpoint", "controller", "面板操作模式（Hold Cap/Release Cap/Decap Only/Cap Only/延迟释放）；决定下一周期开盖或封盖及是否持盖", "cycle.set_operation_mode", "mode"),
        "cycle.time_setting": ({"type": "integer", "enum": [1, 2, 3], "x-unit": "1"}, "1", "setpoint", "controller", "周期时间设置1（默认）/2（较短）/3（较长）；按手册比例缩放旋转时长，断电保留", "cycle.set_time_setting", "setting"),
        "cycle.holding_cap": ({"type": "boolean"}, "1", "derived", "gripper", "Hold Cap模式开盖后夹爪持有管盖，等待下一次封盖", None, None),
    }
    slots = {}
    for sid, (schema, unit, origin, component, meaning, cid, argument) in definitions.items():
        slots[sid] = {"component_ids": [component], "value_schema": _ixs_copy.deepcopy(schema), "unit": unit,
            "reference_conditions": "Spindle local +Z; symmetric gripper local X inner-face spacing" if sid in ("rotation_rad", "jaw_gap_m") else None,
            "meaning_zh": meaning, "origin": origin, "access": "read_write" if cid else "read_only", "visibility": "debug" if origin == "process_truth" else "agent",
            "read_binding": {"backend": "host", "artifact_id": "runtime", "accessor": "IntelliXcapS1Session.read(instance_id=..., state_ids=['%s'])" % sid},
            "write_via": {"capability_id": cid, "argument": argument, "fixed_arguments": {}} if cid else None,
            "sampling": {"mode": "instant", "time_domain": "simulation", "window_description": None}, "source_ids": ["runtime", "assumptions"]}
    progress_ids = ["cycle.status", "cycle.motion_completed", "cycle.host_confirmed", "rotation_rad", "jaw_gap_m", "attachment.constraint_id"]
    capdefs = {
        "power.set": ("空闲时设置仿真供电", {"enabled": {"type": "boolean"}}, ["power.enabled"], {"enabled": True}, ["controller"]),
        "cycle.set_mode": ("空闲时设置开盖/封盖模式", {"mode": {"type": "string", "enum": ["decap", "recap"]}}, ["cycle.mode"], {"mode": "decap"}, ["controller"]),
        "consumable.select": ("选择有宿主兼容证据的耗材类型", {"consumable_id": {"type": "string"}}, ["sample.consumable_id"], {"consumable_id": "synthetic_15ml_type"}, ["sample_interface", "controller"]),
        "cycle.start": ("启动夹持及旋转演示；分阶段等待宿主约束与螺纹结果", {}, progress_ids, {}, ["controller", "spindle", "gripper", "sample_interface"]),
        "cycle.read": ("查询当前周期及机械状态，不推进时间", {}, progress_ids, {}, ["controller", "spindle", "gripper"]),
        "torque.set_limit": ("扭矩阈值未公开且无力学控制实现", {}, [], {}, ["controller", "spindle"]),
        "cycle.set_operation_mode": ("面板选择键：设置五种操作模式之一，同时设定下一周期方向（Release Cap/Hold Cap交替；Decap Only与延迟释放开盖；Cap Only封盖）",
                                     {"mode": {"type": "string", "enum": list(INTELLIXCAP_S1_MODES)}}, ["cycle.operation_mode", "cycle.mode"], {"mode": "release_cap"}, ["controller"]),
        "cycle.set_time_setting": ("周期时间设置1/2/3（开盖1.1/1.1/1.9 s、封盖1.0/0.7/1.8 s；对宿主给定的旋转时长按设置1的比例缩放）",
                                   {"setting": {"type": "integer", "enum": [1, 2, 3], "x-unit": "1"}}, ["cycle.time_setting"], {"setting": 2}, ["controller"]),
    }
    caps = {}
    for cid, (function, props, result_ids, example, components) in capdefs.items():
        async_cycle = cid == "cycle.start"
        unsupported = cid == "torque.set_limit"
        query = cid == "cycle.read"
        effects = [] if query or unsupported else [{"state_id": sid, "timing": "immediate", "description_zh": "写入已校验设定或初始化周期状态；撤销上次样品真值确认"} for sid in (result_ids if not async_cycle else ["cycle.status", "cycle.motion_completed", "cycle.host_confirmed", "truth.thread_state"])]
        if async_cycle:
            effects += [{"state_id": sid, "timing": "on_step", "description_zh": "宿主公共时钟推进合成关节运动或状态阶段"} for sid in ["rotation_rad", "jaw_gap_m", "cycle.status", "cycle.motion_completed"]]
            effects += [{"state_id": sid, "timing": "external", "description_zh": "宿主确认外部附着、释放或螺纹结果后更新"} for sid in ["attachment.constraint_id", "cycle.host_confirmed", "truth.thread_state"]]
        if cid == "cycle.set_mode":
            effects.append({"state_id": "cycle.operation_mode", "timing": "immediate", "description_zh": "开盖对应Decap Only、封盖对应Cap Only（API方向即面板模式）"})
        if async_cycle:
            effects += [{"state_id": sid, "timing": "external", "description_zh": "Hold Cap开盖成功后持盖、封盖后放开；交替模式的下一方向"} for sid in ["cycle.holding_cap", "cycle.mode"]]
        caps[cid] = {"function_zh": function, "kind": "query" if query else "configuration" if cid in ("consumable.select", "cycle.set_mode", "cycle.set_operation_mode", "cycle.set_time_setting") else "device_command",
            "visibility": "agent", "component_ids": components, "availability": "unsupported" if unsupported else "implemented",
            "input_schema": _ixs_obj(props), "output_schema": _ixs_obj({sid: _ixs_copy.deepcopy(slots[sid]["value_schema"]) for sid in result_ids}),
            "implementation": {"process": "absent", "scene": "declared" if async_cycle else "not_applicable", "robot": "absent", "hardware": "absent", "host": "absent" if unsupported else "implemented"},
            "binding": None if unsupported else {"backend": "host", "artifact_id": "runtime", "entrypoint": "basic40_runtime_automation.IntelliXcapS1Session.invoke", "invocation": "session.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % cid},
            "preconditions": [] if query or unsupported else [{"description_zh": "写设定要求没有活动周期且没有附着；启动还要求供电、已选兼容耗材、匹配的管盖、明确运动配置和张开姿态", "state_ids": ["power.enabled", "sample.consumable_id", "sample.tube_id", "sample.cap_id", "simulation.motion_configured", "attachment.constraint_id", "cycle.status"], "enforced_by": "runtime", "check": "IntelliXcapS1Session._execute checks active operation, attachment, registered compatibility and ready pose before commit"}],
            "state_effects": effects, "observes": result_ids,
            "model_effects": {"mode": "host_required" if async_cycle else "none", "binding_ids": [], "description_zh": "宿主将host_motion_snapshot中的rotation_rad/jaw_gap_m绑定到场景关节；约束另由宿主完成" if async_cycle else "该操作不直接改变几何关节"},
            "execution": {"mode": "asynchronous" if async_cycle else "synchronous", "time_domain": "simulation", "completion_criterion": "所有演示阶段结束且宿主确认螺纹成功和外部释放后succeeded；false结果在释放后failed" if async_cycle else "响应已校验且设定已写入，或返回当前读数；unsupported不执行", "completion_state_ids": result_ids,
                "timeout_s": None, "cancellation": "supported" if async_cycle else "unsupported", "retry": "same_request_id", "resources": ["single_tube_workcell"] if async_cycle else [], "partial_failure_zh": "取消冻结当前姿态并保留现有约束；宿主需处理外部样品和释放，绝不自动回滚。拒绝不改变实例状态。"},
            "errors": [{"code": code, "meaning_zh": meaning, "side_effects": "none"} for code, meaning in [("UNKNOWN_INSTANCE", "实例不可访问"), ("UNKNOWN_CAPABILITY", "能力不存在"), ("INVALID_ARGUMENT", "参数类型、单位范围或字段不符"), ("PRECONDITION_FAILED", "缺少兼容性、样品、运动配置或张开条件"), ("BUSY", "已有活动周期或外部附着未解除"), ("ACCESS_DENIED", "身份或操作作用域不足"), ("REQUEST_CONFLICT", "请求ID被不同参数复用"), ("UNSUPPORTED", "扭矩控制尚未实现")]] + ([{"code": "HOST_REPORTED_FAILURE", "meaning_zh": "宿主报告开封盖失败；运动和外部约束已发生", "side_effects": "partial"}] if async_cycle else []),
            "example_arguments": example, "verification": [], "limitations": list(_IXS_LIMITATIONS), "source_ids": ["catalog", "runtime", "assumptions"]}
    panel_caps = {}
    if panel and (INTELLIXCAP_S1_ASSET_DIR / "panel_ui.json").is_file():
        p = _ixs_panel()
        called = p["engine"].spec_capabilities(p["spec"])
        gesture_effects = list({e["state_id"]: dict(e, description_zh="面板动作调用的能力的效果：" + e["description_zh"]) for c in called
                                for e in caps[c]["state_effects"] if e["timing"] == "immediate"}.values())
        for name, d in p["caps"].items():
            gesture = name not in ("panel.capture", "panel.frame")
            panel_caps[name] = {"function_zh": d["function_zh"], "kind": d["kind"], "visibility": d["visibility"], "component_ids": d["component_ids"], "availability": "implemented",
                "input_schema": d["input_schema"], "output_schema": d["output_schema"],
                "implementation": {"process": "implemented", "scene": "not_applicable", "robot": "absent", "hardware": "absent", "host": "implemented"},
                "binding": {"backend": "host", "artifact_id": "runtime", "entrypoint": "basic40_runtime_automation.IntelliXcapS1Session.invoke", "invocation": "session.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % name},
                "preconditions": [], "state_effects": _ixs_copy.deepcopy(gesture_effects) if gesture else [], "observes": [],
                "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "没有3D模型绑定：LED纹理与按键关节联动属于后续Matterix步骤"},
                "execution": {"mode": "synchronous", "time_domain": "simulation", "completion_criterion": "手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受" if gesture else "同步返回当前画面",
                              "completion_state_ids": [], "timeout_s": None, "cancellation": "unsupported", "retry": "same_request_id", "resources": [], "partial_failure_zh": "一个手势可依次调用多个能力，前面已提交的不回滚。"},
                "errors": _ixs_copy.deepcopy(caps["power.set"]["errors"]), "example_arguments": d["example_arguments"], "verification": [], "limitations": [], "source_ids": ["runtime"]}
    return {"asset_type_id": INTELLIXCAP_S1_TYPE, "asset_version": "1.2.0", "catalog_id": "CHEM-177", "state_slots": slots, "capabilities": caps, "panel_capabilities": panel_caps,
        "parameters": {"cap_diameter_min_m": 0.011, "cap_diameter_max_m": 0.040, "nominal_tube_volume_classes_m3": [15e-6, 50e-6], "typical_cycle_upper_exclusive_s": 3.0, "typical_cycle_note": "depends on consumable; not runtime default", "whole_instrument_dimensions_xyz_m": [0.170, 0.240, 0.300], "whole_instrument_mass_kg": 3.8, "supply_voltage_V": 24.0, "supply_current_A": 2.7,
            "default_rotation_delta_rad": None, "default_jaw_travel_m": None, "default_cycle_duration_s": None, "torque_limit_N_m": None, "clamp_force_N": None, "spindle_speed_rad_s": None, "component_masses_kg": None},
        "host_api": {"host_register_consumable": "Explicit compatible type, cap_diameter_m 0.011..0.040, optional nominal_volume_m3 and compatibility_evidence; no bundled library",
            "host_present_sample": "Explicit tube_id, cap_id, consumable_id and placement evidence; no robot placement",
            "host_configure_motion": "Explicit open_gap_m, closed_gap_m, rotation_delta_rad, initial_rotation_rad, clamp_duration_s, rotation_duration_s, release_duration_s and evidence; all unknown by default",
            "host_events": "Returns attach/release requests with event_id; does not make constraints",
            "host_confirm_attachment": "Confirms host actually created named constraint for outstanding attach event",
            "host_confirm_thread_result": "Explicit succeeded bool and evidence only after rotation demonstration completed",
            "host_confirm_release": "Confirms host actually removed current constraint; after cancellation can release retained constraint without resuming motion",
            "host_motion_snapshot": "instance_id, state_revision, time_s, rotation_rad, jaw_gap_m; authoritative motion values for scene sync",
            "host_step": "Advance one common simulation clock for all instances; no per-instance implicit time"}, "limitations": list(_IXS_LIMITATIONS)}


class IntelliXcapS1Runtime:
    """Trusted multi-instance host. External callers receive authenticated sessions."""
    def __init__(self):
        self.time_s = 0.0
        self._instances, self._consumables, self._principals = {}, {}, {}
        self._operations, self._requests = {}, {}
        self._lock = _ixs_threading.RLock()

    def create_instance(self, instance_id):
        with self._lock:
            if not _ixs_text(instance_id) or instance_id in self._instances:
                raise ValueError("Unique nonempty instance_id required")
            values = {sid: None for sid in export_intellixcap_s1_contract_metadata()["state_slots"]}
            values.update({"power.enabled": False, "cycle.mode": "decap", "simulation.motion_configured": False, "cycle.status": "idle", "cycle.motion_completed": False,
                           "cycle.operation_mode": "decap_only", "cycle.time_setting": 1, "cycle.holding_cap": False})
            self._instances[instance_id] = {"instance_id": instance_id, "values": values, "revision": 0, "motion": None, "sample": None, "active_operation_id": None, "events": [],
                                            "last_action": None, "power_epoch": 0}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not _ixs_text(principal_id) or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Host-assigned principal and visibility required")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(s, str) or s not in self._instances for s in instance_ids)):
                raise ValueError("Invalid instance scope")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))

    def session(self, principal_id):
        with self._lock:
            if not isinstance(principal_id, str) or principal_id not in self._principals:
                raise ValueError("Host authentication required")
            return IntelliXcapS1Session(self, principal_id)

    def _inst(self, instance_id):
        if not isinstance(instance_id, str) or instance_id not in self._instances:
            raise ValueError("Unknown instance")
        return self._instances[instance_id]

    def _idle(self, inst):
        if inst["active_operation_id"] is not None or inst["values"]["attachment.constraint_id"] is not None:
            raise ValueError("Active operation or attachment requires explicit recovery")

    @staticmethod
    def _commit(inst, values):
        if values != inst["values"]:
            inst["values"] = values
            inst["revision"] += 1

    def host_register_consumable(self, consumable_id, *, cap_diameter_m, compatibility_evidence, nominal_volume_m3=None):
        with self._lock:
            if not _ixs_text(consumable_id) or consumable_id in self._consumables or not _ixs_text(compatibility_evidence) or not _ixs_number(cap_diameter_m) or not 0.011 <= cap_diameter_m <= 0.040:
                raise ValueError("Explicit compatible consumable and 11–40 mm cap required")
            if nominal_volume_m3 is not None and (not _ixs_number(nominal_volume_m3) or nominal_volume_m3 <= 0):
                raise ValueError("Optional nominal capacity must be positive SI volume")
            self._consumables[consumable_id] = {"cap_diameter_m": float(cap_diameter_m), "nominal_volume_m3": nominal_volume_m3, "evidence": compatibility_evidence}

    def host_present_sample(self, instance_id, *, tube_id, cap_id, consumable_id, evidence):
        with self._lock:
            inst = self._inst(instance_id)
            self._idle(inst)
            if not all(_ixs_text(x) for x in (tube_id, cap_id, consumable_id, evidence)) or tube_id == cap_id or consumable_id not in self._consumables:
                raise ValueError("Registered consumable and distinct tube/cap references with placement evidence required")
            if any(other is not inst and other["sample"] is not None and (tube_id in (other["sample"]["tube_id"], other["sample"]["cap_id"]) or cap_id in (other["sample"]["tube_id"], other["sample"]["cap_id"])) for other in self._instances.values()):
                raise ValueError("Sample entity is already assigned to another instance")
            inst["sample"] = {"tube_id": tube_id, "cap_id": cap_id, "consumable_id": consumable_id, "evidence": evidence}
            s = _ixs_copy.deepcopy(inst["values"])
            s.update({"sample.tube_id": tube_id, "sample.cap_id": cap_id, "truth.thread_state": None, "cycle.host_confirmed": None})
            self._commit(inst, s)

    def host_configure_motion(self, instance_id, *, open_gap_m, closed_gap_m, rotation_delta_rad, initial_rotation_rad, clamp_duration_s, rotation_duration_s, release_duration_s, evidence):
        with self._lock:
            inst = self._inst(instance_id)
            self._idle(inst)
            numbers = [open_gap_m, closed_gap_m, rotation_delta_rad, initial_rotation_rad, clamp_duration_s, rotation_duration_s, release_duration_s]
            if not all(_ixs_number(v) for v in numbers) or not _ixs_text(evidence) or not open_gap_m > closed_gap_m >= 0 or min(rotation_delta_rad, clamp_duration_s, rotation_duration_s, release_duration_s) <= 0:
                raise ValueError("Finite explicit motion values, positive durations/angle, open>closed>=0 and evidence required")
            if not _ixs_number(initial_rotation_rad + rotation_delta_rad) or not _ixs_number(initial_rotation_rad - rotation_delta_rad):
                raise ValueError("Motion angle would overflow")
            inst["motion"] = {"open_gap_m": float(open_gap_m), "closed_gap_m": float(closed_gap_m), "rotation_delta_rad": float(rotation_delta_rad), "initial_rotation_rad": float(initial_rotation_rad), "clamp_duration_s": float(clamp_duration_s), "rotation_duration_s": float(rotation_duration_s), "release_duration_s": float(release_duration_s), "evidence": evidence}
            s = _ixs_copy.deepcopy(inst["values"])
            s.update({"rotation_rad": float(initial_rotation_rad), "jaw_gap_m": float(open_gap_m), "simulation.motion_configured": True})
            self._commit(inst, s)

    def _event(self, inst, op, kind, time_s):
        event = {"event_id": "ixs_evt_" + _ixs_uuid.uuid4().hex, "kind": kind, "operation_id": op["operation_id"], "instance_id": inst["instance_id"], "time_s": time_s, "tube_id": inst["values"]["sample.tube_id"], "cap_id": inst["values"]["sample.cap_id"], "target_component_id": "gripper", "constraint_id": inst["values"]["attachment.constraint_id"], "acknowledged": False}
        inst["events"].append(event)
        op["pending_event_id"] = event["event_id"]

    def host_events(self, instance_id):
        with self._lock:
            return _ixs_copy.deepcopy(self._inst(instance_id)["events"])

    def _operation_for_host(self, instance_id, operation_id, expected_phase):
        inst = self._inst(instance_id)
        op = self._operations.get(operation_id) if isinstance(operation_id, str) else None
        if op is None or op["instance_id"] != instance_id or op["phase"] not in expected_phase:
            raise ValueError("Operation does not match required host-confirmation phase")
        return inst, op

    def _pending_event(self, inst, op, event_id, kind):
        if not isinstance(event_id, str) or event_id != op["pending_event_id"]:
            raise ValueError("Outstanding event ID must match")
        event = next((e for e in inst["events"] if e["event_id"] == event_id and e["kind"] == kind and not e["acknowledged"]), None)
        if event is None:
            raise ValueError("Outstanding event unavailable")
        return event

    def host_confirm_attachment(self, instance_id, *, operation_id, event_id, constraint_id, evidence):
        with self._lock:
            inst, op = self._operation_for_host(instance_id, operation_id, ["awaiting_attach"])
            event = self._pending_event(inst, op, event_id, "attach")
            if not _ixs_text(constraint_id) or not _ixs_text(evidence) or any(other["values"]["attachment.constraint_id"] == constraint_id for other in self._instances.values()):
                raise ValueError("Unique externally created constraint and evidence required")
            event.update({"acknowledged": True, "confirmed_constraint_id": constraint_id, "evidence": evidence})
            op.update({"phase": "rotating", "phase_start_s": self.time_s, "pending_event_id": None})
            s = _ixs_copy.deepcopy(inst["values"])
            s.update({"attachment.constraint_id": constraint_id, "cycle.status": "rotating"})
            self._commit(inst, s)

    def host_confirm_thread_result(self, instance_id, *, operation_id, succeeded, evidence):
        with self._lock:
            inst, op = self._operation_for_host(instance_id, operation_id, ["awaiting_thread_result"])
            if type(succeeded) is not bool or not _ixs_text(evidence):
                raise ValueError("Explicit host result and evidence required")
            op.update({"phase": "awaiting_release", "thread_evidence": evidence})
            s = _ixs_copy.deepcopy(inst["values"])
            s.update({"cycle.host_confirmed": succeeded, "cycle.status": "awaiting_release", "truth.thread_state": ("open" if op["mode"] == "decap" else "closed") if succeeded else None})
            if succeeded and op["mode"] == "decap" and op.get("operation_mode") == "hold_cap":     # Hold Cap: the gripper keeps the removed cap
                op.update({"phase": "succeeded", "status": "succeeded"})
                inst["active_operation_id"], inst["last_action"] = None, "decap"
                s.update({"cycle.status": "succeeded", "cycle.holding_cap": True, "cycle.mode": "recap"})
                self._commit(inst, s)
                op["result"] = self._progress(inst)
                return
            self._commit(inst, s)
            self._event(inst, op, "release", self.time_s)

    def host_confirm_release(self, instance_id, *, operation_id, event_id, constraint_id, evidence):
        with self._lock:
            inst, op = self._operation_for_host(instance_id, operation_id, ["awaiting_release", "cancelled"])
            event = self._pending_event(inst, op, event_id, "release")
            if not _ixs_text(constraint_id) or constraint_id != inst["values"]["attachment.constraint_id"] or not _ixs_text(evidence):
                raise ValueError("Current external constraint and removal evidence required")
            event.update({"acknowledged": True, "evidence": evidence})
            s = _ixs_copy.deepcopy(inst["values"])
            s["attachment.constraint_id"] = None
            op["pending_event_id"] = None
            if op["phase"] != "cancelled":
                op.update({"phase": "releasing", "phase_start_s": self.time_s})
                s["cycle.status"] = "releasing"
            self._commit(inst, s)
            op["result"] = self._progress(inst)

    def host_step(self, dt_s):
        with self._lock:
            if not _ixs_number(dt_s) or dt_s < 0 or not _ixs_number(self.time_s + dt_s):
                raise ValueError("Finite nonnegative common-clock step required")
            next_time = self.time_s + dt_s
            for inst in self._instances.values():
                op = self._operations.get(inst["active_operation_id"])
                if op is None or op["phase"] not in ("clamping", "rotating", "releasing"):
                    continue
                s, m = _ixs_copy.deepcopy(inst["values"]), inst["motion"]
                phase = op["phase"]
                duration = op.get("durations", {}).get(phase) or m[{"clamping": "clamp_duration_s", "rotating": "rotation_duration_s", "releasing": "release_duration_s"}[phase]]
                fraction = min(1.0, max(0.0, (next_time - op["phase_start_s"]) / duration))
                if phase == "clamping":
                    s["jaw_gap_m"] = m["open_gap_m"] * (1-fraction) + m["closed_gap_m"] * fraction
                elif phase == "rotating":
                    s["rotation_rad"] = op["rotation_start_rad"] + op["signed_rotation_delta_rad"] * fraction
                else:
                    delay = op.get("release_delay_s", 0.0)                          # delayed release: the jaws stay closed first
                    opening = min(1.0, max(0.0, (next_time - op["phase_start_s"] - delay) / (duration - delay)))
                    s["jaw_gap_m"] = m["closed_gap_m"] * (1-opening) + m["open_gap_m"] * opening
                if fraction == 1.0:
                    phase = {"clamping": "awaiting_attach", "rotating": "awaiting_thread_result", "releasing": "succeeded" if s["cycle.host_confirmed"] else "failed"}[phase]
                    op["phase"] = s["cycle.status"] = phase
                    if phase == "awaiting_thread_result":
                        s["cycle.motion_completed"] = True
                    if phase in ("succeeded", "failed"):
                        op["status"] = phase
                        inst["active_operation_id"] = None
                        if phase == "succeeded":
                            inst["last_action"] = op["mode"]
                            s["cycle.holding_cap"] = False
                            s["cycle.mode"] = _ixs_direction(s["cycle.operation_mode"], False, op["mode"])
                    self._commit(inst, s)
                    if phase == "awaiting_attach":
                        self._event(inst, op, "attach", op["phase_start_s"] + duration)
                else:
                    self._commit(inst, s)
                op["result"] = self._progress(inst)
            self.time_s = next_time

    @staticmethod
    def _progress(inst):
        return {sid: _ixs_copy.deepcopy(inst["values"][sid]) for sid in ["cycle.status", "cycle.motion_completed", "cycle.host_confirmed", "rotation_rad", "jaw_gap_m", "attachment.constraint_id"]}

    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame); created on first use."""
        self._inst(instance_id)
        if not hasattr(self, "_panels"):
            self._panels, self.panel_capture_dir = {}, None
        if instance_id not in self._panels:
            self._panels[instance_id] = _ixs_panel()["engine"].PanelUI(_ixs_panel()["spec"], _IntelliXcapPanelHost(self, instance_id))
        return self._panels[instance_id]

    def host_motion_snapshot(self, instance_id):
        with self._lock:
            inst = self._inst(instance_id)
            return {"instance_id": instance_id, "state_revision": inst["revision"], "time_s": self.time_s, "rotation_rad": inst["values"]["rotation_rad"], "jaw_gap_m": inst["values"]["jaw_gap_m"]}

    def debug_snapshot(self):
        with self._lock:
            return _ixs_copy.deepcopy({"time_s": self.time_s, "instances": self._instances, "consumables": self._consumables, "operations": self._operations})


class IntelliXcapS1Session:
    """Agent-facing wrapper; only host-created identity controls access."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal_id = host, principal_id

    def _instance(self, instance_id):
        _, scope = self.__host._principals[self.__principal_id]
        return self.__host._instances.get(instance_id) if isinstance(instance_id, str) and (scope is None or instance_id in scope) else None

    def _visible(self, sid):
        slots = export_intellixcap_s1_contract_metadata()["state_slots"]
        role, _ = self.__host._principals[self.__principal_id]
        return isinstance(sid, str) and sid in slots and (slots[sid]["visibility"] == "agent" or role in ("debug", "evaluator"))

    def _envelope(self, instance_id=None, request_id=None, *, status="succeeded", operation_id=None, result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id if isinstance(instance_id, str) else None, "request_id": request_id if isinstance(request_id, str) else None,
            "operation_id": operation_id, "status": status, "state_revision": inst["revision"] if inst else None,
            "clock": {"domain": "simulation", "time_s": self.__host.time_s}, "result": _ixs_copy.deepcopy(result or {}), "observations": _ixs_copy.deepcopy(observations or {}), "error": error}

    def _error(self, instance_id, code, request_id=None, *, status="rejected"):
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": "Operation unavailable or request does not meet its declared operating/access conditions.", "retryable": False, "side_effects": "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != INTELLIXCAP_S1_TYPE:
                return self._error(None, "INVALID_ARGUMENT")
            _, scope = self.__host._principals[self.__principal_id]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = export_intellixcap_s1_contract_metadata()
            spec.pop("host_api")
            role, _ = self.__host._principals[self.__principal_id]
            spec["capabilities"].update({k: v for k, v in spec.pop("panel_capabilities").items() if v["visibility"] == "agent" or role in ("debug", "evaluator")})
            spec["state_slots"] = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(sid)}
            for cap in spec["capabilities"].values():
                cap["state_effects"] = [e for e in cap["state_effects"] if self._visible(e["state_id"])]
            spec.update({"protocol_version": "1.0.0", "instance_id": instance_id, "adapter": "basic40_runtime_automation.IntelliXcapS1Session"})
            return spec

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = export_intellixcap_s1_contract_metadata()["state_slots"]
            if state_ids is None:
                state_ids = [sid for sid in slots if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(s, str) for s in state_ids) or len(set(state_ids)) != len(state_ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(not self._visible(sid) for sid in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            observations = {}
            for sid in state_ids:
                value = _ixs_copy.deepcopy(inst["values"][sid])
                observations[sid] = {"value": value, "unit": slots[sid]["unit"], "quality": "unavailable" if value is None else "estimated" if sid in ("rotation_rad", "jaw_gap_m") else "valid", "sample_time_s": self.__host.time_s, "sample_window_s": None}
                if value is None:
                    observations[sid]["reason"] = "No explicit host configuration, sample reference, attachment or confirmation is available for this state."
            return self._envelope(instance_id, observations=observations)

    def _cached(self, instance_id, request_id, logical, callback):
        if not _ixs_text(request_id) or len(request_id) > 256:
            return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        try:
            digest = _ixs_hashlib.sha256(_ixs_json.dumps(logical, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        except (TypeError, ValueError, OverflowError, RecursionError):
            return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        key = (self.__principal_id, instance_id, request_id)
        if key in self.__host._requests:
            previous, response = self.__host._requests[key]
            return _ixs_copy.deepcopy(response) if previous == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
        response = callback()
        self.__host._requests[key] = (digest, _ixs_copy.deepcopy(response))
        return response

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            return self._cached(instance_id, request_id, ["invoke", capability_id, arguments], lambda: self._execute(inst, capability_id, arguments, request_id))

    def _invoke_panel(self, inst, cid, args, request_id):
        """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a capture of an LED, or the evaluator frame."""
        iid, panel, host = inst["instance_id"], _ixs_panel(), self.__host
        definition = panel["caps"][cid]
        role, _ = host._principals[self.__principal_id]
        if definition["visibility"] != "agent" and role not in ("debug", "evaluator"):
            return self._error(iid, "ACCESS_DENIED", request_id)
        if panel["engine"].check_arguments(definition["input_schema"], args):
            return self._error(iid, "INVALID_ARGUMENT", request_id)
        ui = host.panel(iid)
        try:
            if cid == "panel.frame":
                return self._envelope(iid, request_id, result={"frame": ui.frame()})
            if cid == "panel.capture":
                import tempfile
                from pathlib import Path
                data, width, height = ui.png_bytes(args["display_id"])
                digest = _ixs_hashlib.sha256(data).hexdigest()
                folder = Path(host.panel_capture_dir) if host.panel_capture_dir else Path(tempfile.gettempdir()) / "chemistry_sim_panel" / iid
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ("%s_%s.png" % (args["display_id"], digest[:16]))
                if not path.exists():
                    path.write_bytes(data)
                return self._envelope(iid, request_id, result={"display_id": args["display_id"], "time_s": host.time_s,
                                                               "image": {"path": str(path), "format": "png", "width": width, "height": height, "sha256": digest}})
            ui.host.caller = self.__principal_id                          # the gesture acts as this session's caller (owns what it starts)
            try:
                accepted = ui.press(args["input_id"], float(args.get("duration_s", 0.1)), held=args.get("held", ()))
            finally:
                ui.host.caller = None
        except panel["engine"].PanelError:
            return self._error(iid, "INVALID_ARGUMENT", request_id)
        return self._envelope(iid, request_id, result={"accepted": bool(accepted)})

    def _execute(self, inst, cid, args, request_id):
        iid = inst["instance_id"]
        fail = lambda code: self._error(iid, code, request_id)
        exported = export_intellixcap_s1_contract_metadata()
        if cid in exported["panel_capabilities"]:
            return self._invoke_panel(inst, cid, args, request_id)
        caps = exported["capabilities"]
        if cid not in caps:
            return fail("UNKNOWN_CAPABILITY")
        props = caps[cid]["input_schema"]["properties"]
        if set(args) != set(props):
            return fail("INVALID_ARGUMENT")
        for name, spec in props.items():
            value = args[name]
            if (spec["type"] == "boolean" and type(value) is not bool) or (spec["type"] == "string" and not _ixs_text(value)) or ("enum" in spec and value not in spec["enum"]):
                return fail("INVALID_ARGUMENT")
        if cid == "torque.set_limit":
            return self._error(iid, "UNSUPPORTED", request_id, status="unsupported")
        if cid == "cycle.read":
            return self._envelope(iid, request_id, result=self.__host._progress(inst))
        s = _ixs_copy.deepcopy(inst["values"])
        active = self.__host._operations.get(inst["active_operation_id"]) if inst["active_operation_id"] is not None else None
        if cid == "power.set" and args["enabled"] is False and active is not None and active["principal_id"] != self.__principal_id \
                and self.__principal_id != "__ixs_panel__" and self.__host._principals[self.__principal_id][0] not in ("debug", "evaluator"):
            return fail("ACCESS_DENIED")                                     # no power-off under another caller's cycle (lead rule 2026-10-19)
        hold_recap = cid == "cycle.start" and s["cycle.holding_cap"] and inst["active_operation_id"] is None and s["cycle.mode"] == "recap"
        if (inst["active_operation_id"] is not None or s["attachment.constraint_id"] is not None) and not hold_recap and cid != "cycle.set_time_setting":
            return fail("BUSY")
        if cid == "power.set":
            if args["enabled"] and not s["power.enabled"]:
                inst["power_epoch"] += 1
            s["power.enabled"] = args["enabled"]
        elif cid == "cycle.set_mode":
            s["cycle.mode"] = args["mode"]
            s["cycle.operation_mode"] = "decap_only" if args["mode"] == "decap" else "cap_only"
        elif cid == "cycle.set_operation_mode":
            if not s["power.enabled"]:
                return fail("PRECONDITION_FAILED")
            s["cycle.operation_mode"] = args["mode"]
            s["cycle.mode"] = _ixs_direction(args["mode"], s["cycle.holding_cap"], inst["last_action"])
        elif cid == "cycle.set_time_setting":
            if not s["power.enabled"] or inst["active_operation_id"] is not None:
                return fail("PRECONDITION_FAILED" if not s["power.enabled"] else "BUSY")
            s["cycle.time_setting"] = args["setting"]
        elif cid == "consumable.select":
            if args["consumable_id"] not in self.__host._consumables:
                return fail("PRECONDITION_FAILED")
            s["sample.consumable_id"] = args["consumable_id"]
        elif cid == "cycle.start":
            m, sample = inst["motion"], inst["sample"]
            if not s["power.enabled"] or m is None or sample is None or sample["consumable_id"] != s["sample.consumable_id"] or (s["jaw_gap_m"] != m["open_gap_m"] and not hold_recap):
                return fail("PRECONDITION_FAILED")
            cap_diameter = self.__host._consumables[sample["consumable_id"]]["cap_diameter_m"]
            if not m["closed_gap_m"] <= cap_diameter < m["open_gap_m"]:
                return fail("PRECONDITION_FAILED")
            delta = m["rotation_delta_rad"] * (1 if s["cycle.mode"] == "decap" else -1)
            if not _ixs_number(s["rotation_rad"] + delta):
                return fail("INVALID_ARGUMENT")
            operation_id = "ixs_op_" + _ixs_uuid.uuid4().hex
            phase = "rotating" if hold_recap else "clamping"                 # Hold Cap recap: the held cap is already in the jaws
            s.update({"cycle.status": phase, "cycle.motion_completed": False, "cycle.host_confirmed": None, "truth.thread_state": None})
            self.__host._commit(inst, s)
            decap_s, recap_s = INTELLIXCAP_S1_CYCLE_TIMES_S[s["cycle.time_setting"]]
            scale = (decap_s / INTELLIXCAP_S1_CYCLE_TIMES_S[1][0]) if s["cycle.mode"] == "decap" else (recap_s / INTELLIXCAP_S1_CYCLE_TIMES_S[1][1])
            delay = INTELLIXCAP_S1_RELEASE_DELAY_S if s["cycle.operation_mode"] == "delayed_release" else 0.0
            op = {"operation_id": operation_id, "instance_id": iid, "principal_id": self.__principal_id, "request_id": request_id, "status": "running", "phase": phase, "phase_start_s": self.__host.time_s, "rotation_start_rad": s["rotation_rad"], "signed_rotation_delta_rad": delta, "mode": s["cycle.mode"], "pending_event_id": None, "result": self.__host._progress(inst),
                  "operation_mode": s["cycle.operation_mode"], "durations": {"rotating": m["rotation_duration_s"] * scale, "releasing": m["release_duration_s"] + delay}, "release_delay_s": delay}
            self.__host._operations[operation_id] = op
            inst["active_operation_id"] = operation_id
            return self._envelope(iid, request_id, status="accepted", operation_id=operation_id, result=op["result"])
        self.__host._commit(inst, s)
        return self._envelope(iid, request_id, result={sid: s[sid] for sid in caps[cid]["observes"]})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = export_intellixcap_s1_contract_metadata()["state_slots"]
            if not self._visible(state_id) or slots[state_id]["access"] != "read_write":
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            via = slots[state_id]["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=via["capability_id"], arguments={via["argument"]: value}, request_id=request_id)

    def _owned_operation(self, operation_id):
        op = self.__host._operations.get(operation_id) if isinstance(operation_id, str) else None
        return op if op is not None and op["principal_id"] == self.__principal_id and self._instance(op["instance_id"]) is not None else None

    def _operation_response(self, op, request_id=None):
        inst = self._instance(op["instance_id"])
        result = op["result"] if op["status"] in ("succeeded", "failed", "cancelled") else self.__host._progress(inst)
        error = None if op["status"] != "failed" else {"code": "HOST_REPORTED_FAILURE", "message": "Host reported thread-operation failure; motion and external effects were not rolled back.", "retryable": False, "side_effects": "partial", "progress": _ixs_copy.deepcopy(result)}
        return self._envelope(op["instance_id"], request_id or op["request_id"], status=op["status"], operation_id=op["operation_id"], result=result, error=error)

    def poll(self, *, operation_id):
        with self.__host._lock:
            op = self._owned_operation(operation_id)
            return self._operation_response(op) if op is not None else self._error(None, "ACCESS_DENIED")

    def cancel(self, *, operation_id, request_id):
        with self.__host._lock:
            op = self._owned_operation(operation_id)
            if op is None:
                return self._error(None, "ACCESS_DENIED", request_id)
            return self._cached(op["instance_id"], request_id, ["cancel", operation_id], lambda: self._cancel(op, request_id))

    def _cancel(self, op, request_id):
        if op["status"] in ("succeeded", "failed", "cancelled"):
            return self._operation_response(op, request_id)
        inst = self._instance(op["instance_id"])
        op.update({"phase": "cancelled", "status": "cancelled"})
        inst["active_operation_id"] = None
        s = _ixs_copy.deepcopy(inst["values"])
        s["cycle.status"] = "cancelled"
        self.__host._commit(inst, s)
        op["result"] = self.__host._progress(inst)
        # Invalidate unfulfilled attach requests: late acknowledgements may not attach.
        if op["pending_event_id"]:
            event = next(e for e in inst["events"] if e["event_id"] == op["pending_event_id"])
            event["cancelled"] = True
        op["pending_event_id"] = None
        if s["attachment.constraint_id"] is not None:
            self.__host._event(inst, op, "release", self.__host.time_s)
        return self._operation_response(op, request_id)


class _IntelliXcapPanelHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py for one instance: what the panel firmware reads, calls and times by."""
    def __init__(self, runtime, instance_id):
        self.runtime, self.instance_id = runtime, instance_id
        runtime._principals.setdefault("__ixs_panel__", ("agent", None))
        self.session = IntelliXcapS1Session(runtime, "__ixs_panel__")    # only for actions no session gesture started (none today)
        self.caller = None                                                # principal of the session whose gesture is being handled

    def panel_read(self):
        inst = self.runtime._instances[self.instance_id]
        slots = export_intellixcap_s1_contract_metadata(panel=False)["state_slots"]
        out = {sid: _ixs_copy.deepcopy(v) for sid, v in inst["values"].items() if slots[sid]["visibility"] == "agent"}
        out.update({"host.powered": inst["values"]["power.enabled"], "host.power_epoch": inst["power_epoch"]})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action takes the checked path of a direct call (same preconditions and revision) as the gesturing session's caller, so a
        cycle started on the trigger belongs to that principal (poll / cancel)."""
        inst = self.runtime._instances[self.instance_id]
        session = self.session if self.caller is None else IntelliXcapS1Session(self.runtime, self.caller)
        response = session._execute(inst, capability_id, dict(arguments), None)
        if response["status"] not in ("succeeded", "accepted"):
            raise _ixs_panel()["engine"].PanelRejected(response["error"]["code"])
        return response["result"]

    def panel_time(self):
        return self.runtime.time_s
# END CHEM-177 INTELLIXCAP S1 APPEND-ONLY RUNTIME

# BEGIN CHEM-176 AZENTA AUTOMATED ROLL HEAT SEALER APPEND-ONLY RUNTIME
import copy as _ahs_copy
import json as _ahs_json
import math as _ahs_math
import threading as _ahs_threading

AZENTA_HEAT_SEALER_TYPE = 'instrument.azenta_automated_roll_heat_sealer_a4s'
AZENTA_HEAT_SEALER_Q_MAX_M = .074


def _ahs_number(value):
    try:
        return type(value) in (int, float) and _ahs_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _ahs_string(value):
    return isinstance(value, str) and bool(value.strip())


def _ahs_obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _ahs_num(unit, lo=0, hi=None, integer=False):
    s = {'type': 'integer' if integer else 'number', 'minimum': lo, 'x-unit': unit}
    if hi is not None:
        s['maximum'] = hi
    return s


def _ahs_nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def _ahs_slot(schema, unit, origin, visibility='agent', via=None):
    return {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': visibility,
        'access': 'read_write' if via else 'read_only', 'reference_conditions': None,
        'write_via': {'capability_id': via[0], 'parameter': via[1], 'fixed_arguments': {}} if via else None,
        'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}


_AHS_STR = {'type': 'string'}
_AHS_BOOL = {'type': 'boolean'}
_AHS_TEMP = _ahs_num('K', 373.15, 468.15)
_AHS_DURATION = _ahs_num('s', .1, 10)
_AHS_Q = _ahs_num('m', 0, AZENTA_HEAT_SEALER_Q_MAX_M)
_AHS_STATUS = {'type': 'string', 'enum': ['idle', 'running', 'succeeded', 'cancelled', 'failed']}
_AHS_SLOTS = {
    'power.enabled': _ahs_slot(_AHS_BOOL, '1', 'setpoint', via=('power.set', 'enabled')),
    'thermal.setpoint_k': _ahs_slot(_ahs_nullable(_AHS_TEMP), 'K', 'setpoint', via=('thermal.set_temperature', 'temperature_K')),
    'measurement.temperature_k': _ahs_slot(_ahs_nullable(_ahs_num('K')), 'K', 'derived'),
    'thermal.target_reached': _ahs_slot(_AHS_BOOL, '1', 'derived'),
    'simulation.thermal_rate_configured': _ahs_slot(_AHS_BOOL, '1', 'derived'),
    'configuration.press_time_s': _ahs_slot(_ahs_nullable(_AHS_DURATION), 's', 'setpoint'),
    'configuration.plate_adapter_id': _ahs_slot(_ahs_nullable(_AHS_STR), '1', 'setpoint'),
    'configuration.film_roll_position_id': _ahs_slot(_ahs_nullable(_AHS_STR), '1', 'setpoint'),
    'plate.loaded': _ahs_slot(_AHS_BOOL, '1', 'scene'),
    'plate.plate_id': _ahs_slot(_ahs_nullable(_AHS_STR), '1', 'scene'),
    'plate.height_m': _ahs_slot(_ahs_nullable(_ahs_num('m', .003, .060)), 'm', 'scene'),
    'plate.attachment_constraint_id': _ahs_slot(_ahs_nullable(_AHS_STR), '1', 'scene'),
    'plate.simulated_sealed': _ahs_slot(_AHS_BOOL, '1', 'derived'),
    'film.roll_id': _ahs_slot(_ahs_nullable(_AHS_STR), '1', 'scene'),
    'film.width_m': _ahs_slot(_ahs_nullable(_ahs_num('m', .075, .085)), 'm', 'scene'),
    'film.remaining_seals': _ahs_slot(_ahs_nullable(_ahs_num('1', 0, 5000, True)), '1', 'derived'),
    'scene.press_displacement_m': _ahs_slot(_AHS_Q, 'm', 'scene', via=('scene.set_press_displacement', 'displacement_m')),
    'sealing.status': _ahs_slot(_AHS_STATUS, '1', 'derived'),
    'sealing.elapsed_s': _ahs_slot(_ahs_num('s', 0, 10), 's', 'derived'),
    'sealing.progress': _ahs_slot(_ahs_num('1', 0, 1), '1', 'derived'),
    'truth.temperature_k': _ahs_slot(_ahs_nullable(_ahs_num('K')), 'K', 'process_truth', 'debug'),
    'truth.thermal_rate_k_s': _ahs_slot(_ahs_nullable(_ahs_num('K/s')), 'K/s', 'process_truth', 'debug'),
}
_AHS_OPERATION = _ahs_obj({'operation_id': _AHS_STR, 'status': _AHS_STATUS, 'elapsed_s': _ahs_num('s', 0, 10),
    'progress': _ahs_num('1', 0, 1), 'simulated_sealed': _AHS_BOOL})
_AHS_READ_IDS = ['measurement.temperature_k', 'thermal.target_reached', 'film.remaining_seals', 'plate.simulated_sealed', 'sealing.status', 'sealing.elapsed_s', 'sealing.progress']
_AHS_COMPONENTS = {'power.set': ['power_data_panel'], 'thermal.set_temperature': ['press_head'],
    'configuration.set': ['plate_adapter', 'roll_support'], 'scene.set_press_displacement': ['press_head', 'guide_rails'],
    'sealing.start': ['press_head', 'film_path', 'external_plate'], 'sealing.poll': ['status_display'],
    'sealing.cancel': ['press_head'], 'sealing.read': ['status_display'], 'sealing.set_pressure': ['press_head']}
_AHS_LIMITATIONS = [
    '温度100–195°C、时间0.1–10s、板高3–60mm、膜宽75–85mm来自CHEM-176三表；SI接口采用K、s、m。',
    '热初始值和热速率没有缺省值；温度推进只采用宿主明确给定且附证据的线性接近设定值模型，无热容、功率、冷却曲线或PID推断。',
    'measurement.temperature_k为理想化过程代理且quality=estimated；target_reached只比较模型目标，不是实测稳定性或厂家互锁。±1K均匀性只作为规格，不作为目标公差。',
    '压合q=0..0.074m、压头底z=.105-q、板顶z=.028+height和接触q=.077-height均是几何假设，非厂家行程或接触力学。',
    '一卷最多5000次是上限；宿主须提供实际剩余次数，默认null。每次已接受封膜消耗1次膜配额；取消或失败不退回膜。未推断膜长度或实际进给距离。',
    'film_roll_position_id与plate_adapter_id引用宿主装载的兼容配置；真实位置坐标和厂家适配器枚举未知，不生成任意假坐标。',
    'simulated_sealed仅表示配置、温度代理、压合接触与持续时间条件满足，不预测粘接强度、泄漏、耐溶剂性或真实密封质量。压力不可任意编程且未建模。',
    'scene_adapter与attach/release需宿主True确认；robot与hardware absent，未实现接触抓取、力控制或实际输送。',
    '去重仅当前宿主内存有效；API身份过滤不是Python、文件系统或进程安全隔离。取消保留已经消耗时间、膜与压头位置。',
]


def export_azenta_heat_sealer_contract_metadata():
    slots = _ahs_copy.deepcopy(_AHS_SLOTS)
    for sid, slot in slots.items():
        slot['read_binding'] = 'AzentaHeatSealerSession.read(instance_id=..., state_ids=[%r])' % sid
    slots['scene.press_displacement_m']['reference_conditions'] = 'q positive down; ahs__press_joint.location.z=-q; 0..0.074m geometric assumption'
    specs = {
        'power.set': ({'enabled': _AHS_BOOL}, _ahs_obj({'enabled': _AHS_BOOL}), {'enabled': True}, ['power.enabled', 'thermal.target_reached', 'measurement.temperature_k', 'sealing.status'], 'device_command', '设置仿真供电；断电取消封膜并保留压头、时间和已消耗膜。'),
        'thermal.set_temperature': ({'temperature_K': _AHS_TEMP}, _ahs_obj({'setpoint_K': _AHS_TEMP}), {'temperature_K': 423.15}, ['thermal.setpoint_k', 'thermal.target_reached'], 'device_command', '写入封膜温度目标；只在宿主推进时才更新已配置的温度过程。'),
        'configuration.set': ({'press_time_s': _AHS_DURATION, 'plate_adapter_id': _AHS_STR, 'film_roll_position_id': _AHS_STR}, _ahs_obj({'press_time_s': _AHS_DURATION, 'plate_adapter_id': _AHS_STR, 'film_roll_position_id': _AHS_STR}), {'press_time_s': 1.0, 'plate_adapter_id': 'host_adapter', 'film_roll_position_id': 'host_roll_position'}, ['configuration.press_time_s', 'configuration.plate_adapter_id', 'configuration.film_roll_position_id'], 'configuration', '选择时间和宿主已装载的板适配器、膜卷位置标识。'),
        'scene.set_press_displacement': ({'displacement_m': _AHS_Q}, _ahs_obj({'displacement_m': _AHS_Q}), {'displacement_m': .017}, ['scene.press_displacement_m'], 'configuration', '宿主确认压合展示关节；装板时不能穿过几何接触平面。'),
        'sealing.start': ({}, _AHS_OPERATION, {}, ['sealing.status', 'sealing.elapsed_s', 'sealing.progress', 'plate.simulated_sealed', 'film.remaining_seals'], 'device_command', '开始计时封膜，立即消耗1次膜配额；accepted不表示密封完成。'),
        'sealing.poll': ({'operation_id': _AHS_STR}, _AHS_OPERATION, {'operation_id': 'seal_demo_1'}, [], 'query', '读取当前身份拥有的任务进度，不推进宿主时钟。'),
        'sealing.cancel': ({'operation_id': _AHS_STR}, _AHS_OPERATION, {'operation_id': 'seal_demo_1'}, ['sealing.status'], 'device_command', '取消运行，保留时间、膜消耗和压合位置。'),
        'sealing.read': ({}, _ahs_obj({k: slots[k]['value_schema'] for k in _AHS_READ_IDS}), {}, [], 'query', '读取估计温度、膜余量和当前封膜状态，不推进时间。'),
        'sealing.set_pressure': ({}, _ahs_obj({}), {}, [], 'device_command', '压力并非任意可编程；没有压力设置实现或虚构参数。'),
    }
    prereq = {'power.set': [], 'thermal.set_temperature': ['已上电且没有正在封膜操作。'],
        'configuration.set': ['已上电；无正在封膜操作；板和膜已由宿主装载，两个配置ID匹配。'],
        'scene.set_press_displacement': ['无正在封膜操作；场景回调绑定并返回True；q不得穿透已装板的接触平面。'],
        'sealing.start': ['已上电；已装板与兼容膜；配置完整匹配；膜余量>0；未封膜；温度代理达到目标；场景回调已确认接触q。'],
        'sealing.poll': ['操作属于当前实例与宿主登记身份。'], 'sealing.cancel': ['操作属于当前实例与宿主登记身份。'],
        'sealing.read': [], 'sealing.set_pressure': []}
    caps = {}
    for cid, (props, out, example, effects, kind, desc) in specs.items():
        entries = [{'state_id': k, 'timing': 'immediate', 'description_zh': '本调用成功后的明确状态更新。'} for k in effects]
        if cid == 'thermal.set_temperature':
            entries += [{'state_id': k, 'timing': 'on_step', 'description_zh': '仅宿主已给出初值、线性热速率及证据时，随共同时间更新。'} for k in ['truth.temperature_k', 'measurement.temperature_k', 'thermal.target_reached']]
        if cid == 'sealing.start':
            entries += [{'state_id': k, 'timing': 'on_step', 'description_zh': '只由宿主共同时间推进；满设定持续时间后改变模拟密封状态。'} for k in ['sealing.status', 'sealing.elapsed_s', 'sealing.progress', 'plate.simulated_sealed']]
        unsupported = cid == 'sealing.set_pressure'
        scene = cid == 'scene.set_press_displacement'
        observes = _AHS_READ_IDS if cid == 'sealing.read' else ['sealing.status', 'sealing.elapsed_s', 'sealing.progress', 'plate.simulated_sealed'] if cid.startswith('sealing.') and not unsupported else [k for k in effects if slots[k]['visibility'] == 'agent']
        caps[cid] = {'name_zh': desc, 'description_zh': desc, 'kind': kind, 'visibility': 'agent', 'availability': 'unsupported' if unsupported else 'implemented',
            'component_ids': _AHS_COMPONENTS[cid], 'input_schema': _ahs_obj(_ahs_copy.deepcopy(props)), 'output_schema': _ahs_copy.deepcopy(out),
            'state_effects': entries, 'observes': observes, 'example_arguments': [_ahs_copy.deepcopy(example)], 'preconditions_zh': prereq[cid],
            'binding': None if unsupported else 'basic40_runtime_automation.AzentaHeatSealerSession.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
            'implementation': {'process': 'absent' if unsupported else 'not_applicable' if scene else 'implemented', 'scene': 'implemented' if scene else 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'absent' if unsupported else 'implemented'},
            'model_effects': {'mode': 'host_required' if scene else 'none', 'binding_ids': [], 'description_zh': 'scene_adapter(instance_id, {scene.press_displacement_m:q})须返回True；没有回调不返回成功。' if scene else '只更新设置、观测与封膜逻辑，压头保留当前姿态，无附加可见动作。'},
            'execution_notes': {'mode': 'asynchronous' if cid == 'sealing.start' else 'synchronous', 'time_domain': 'simulation', 'completion_zh': '宿主累计时间达到设定时间并持续满足模型温度与接触条件。' if cid == 'sealing.start' else '返回succeeded表示本次调用完成。', 'cancellable': cid == 'sealing.start', 'partial_failure_zh': '取消保留压头位置、已用时间及1次膜消耗，未完成板不标密封；外部场景失败可能有未知副作用。', 'retry_zh': '相同request_id与参数使用当前内存缓存；参数冲突拒绝；不保证跨重启。'},
            'errors': [{'code': c, 'description_zh': d, 'side_effects': side} for c, d, side in [
                ('INVALID_ARGUMENT', '类型、范围或额外字段无效。', 'none'), ('UNKNOWN_INSTANCE', '实例未知或不在身份范围。', 'none'),
                ('UNKNOWN_CAPABILITY', '能力未知。', 'none'), ('PRECONDITION_FAILED', '供电、装载、温度、接触或配置条件未满足。', 'none'),
                ('BUSY', '封膜操作占用资源。', 'none'), ('REQUEST_CONFLICT', '请求ID用于不同参数。', 'none'),
                ('UNSUPPORTED', '能力不支持或场景未绑定。', 'none'), ('DEVICE_ERROR', '外部回调未确认，内部未提交。', 'unknown'),
                ('UNKNOWN_OPERATION', '任务不属于当前实例和身份。', 'none')]]}
    return {'asset_type_id': AZENTA_HEAT_SEALER_TYPE, 'asset_version': '1.0.1', 'catalog_id': 'CHEM-176', 'state_slots': slots, 'capabilities': caps,
        'parameters': {k: {'value': v, 'unit': u, 'evidence': e} for k, v, u, e in [
            ('temperature_range_K', [373.15, 468.15], 'K', 'catalog'), ('press_time_range_s', [.1, 10], 's', 'catalog'),
            ('plate_height_range_m', [.003, .060], 'm', 'catalog'), ('film_width_range_m', [.075, .085], 'm', 'catalog'),
            ('block_temperature_uniformity_K', 1.0, 'K', 'catalog'), ('maximum_seals_per_roll', 5000, '1', 'catalog'),
            ('body_mass_without_roll_kg', 27.0, 'kg', 'catalog'), ('body_dimensions_xyz_m', [.230, .507, .276], 'm', 'catalog'),
            ('press_display_travel_m', .074, 'm', 'assumed'), ('thermal_rate_K_s', None, 'K/s', 'unknown'),
            ('press_pressure_Pa', None, 'Pa', 'unknown'), ('seal_strength_Pa', None, 'Pa', 'unknown'),
            ('leak_rate_m3_s', None, 'm^3/s', 'unknown'), ('roll_position_m', None, 'm', 'unknown')]},
        'host_api': {k: {'entrypoint': 'AzentaHeatSealerRuntime.' + k, 'description_zh': v} for k, v in {
            'create_instance': '独立实例与可选scene_adapter；温度和膜余量初始null。', 'register_principal': '可信宿主注册身份与实例范围。',
            'host_load_plate': '显式板ID、板高、适配器ID及兼容证据；要求压头q=0、无旧板。',
            'host_unload_plate': '宿主确认外部移出；要求压头q=0、无附着、无运行任务，不产生机械位移。',
            'host_load_film': '显式膜卷ID、宽、剩余次数、位置ID与兼容证据；上限5000，不默认满卷。',
            'host_set_temperature': '宿主给出温度与证据，得到estimated代理；封膜中偏離目标使任务失败并保留部分效果。',
            'host_configure_thermal_rate': '显式正有限线性热速率与证据，无默认系数；不允许封膜中更改。',
            'host_step': 'host_step(dt_s,world_time_s=...)正有限步长且与共同世界时间一致；read不推进。',
            'host_attach': 'attach事件含plate_id及constraint_id；宿主attachment_adapter返回True才登记，无接触抓取。',
            'host_release': 'release事件含plate_id及constraint_id；宿主True确认后清除约束，保留板和姿态。'}.items()},
        'limitations': list(_AHS_LIMITATIONS), 'output_schema_scope': 'capability output_schema validates response.result; truth.* never appears in agent values'}


class AzentaHeatSealerRuntime:
    """Trusted host. Expose only an authenticated session to an agent."""
    def __init__(self):
        self.time_s, self._next_operation = 0.0, 1
        self._instances, self._principals, self._requests, self._operations = {}, {}, {}, {}
        self._lock = _ahs_threading.RLock()

    def create_instance(self, instance_id, scene_adapter=None):
        with self._lock:
            if not _ahs_string(instance_id) or instance_id in self._instances or (scene_adapter is not None and not callable(scene_adapter)):
                raise ValueError('Unique instance ID and optional callable scene adapter required')
            state = {sid: None for sid in _AHS_SLOTS}
            state.update({'power.enabled': False, 'thermal.target_reached': False, 'simulation.thermal_rate_configured': False,
                'plate.loaded': False, 'plate.simulated_sealed': False, 'scene.press_displacement_m': 0.0,
                'sealing.status': 'idle', 'sealing.elapsed_s': 0.0, 'sealing.progress': 0.0})
            self._instances[instance_id] = {'state': state, 'revision': 0, 'adapter': scene_adapter, 'scene_confirmed': False,
                'active_operation': None, 'plate_adapter_id': None, 'film_position_id': None, 'evidence': {}}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not _ahs_string(principal_id) or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Host-assigned unique identity with valid visibility required')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(k, str) or k not in self._instances for k in instance_ids)):
                raise ValueError('Invalid instance scope')
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals:
            raise ValueError('Host registration required')
        return AzentaHeatSealerSession(self, principal_id)

    def _get(self, iid, principal=None):
        if not isinstance(iid, str) or iid not in self._instances:
            return None
        if principal is not None:
            p = self._principals.get(principal)
            if p is None or (p[1] is not None and iid not in p[1]):
                return None
        return self._instances[iid]

    def _require_idle(self, iid, retracted=False):
        item = self._get(iid)
        if item is None or item['state']['sealing.status'] == 'running' or (retracted and item['state']['scene.press_displacement_m'] != 0):
            raise ValueError('Known idle instance with required press position needed')
        return item

    @staticmethod
    def _refresh(s):
        s['measurement.temperature_k'] = s['truth.temperature_k'] if s['power.enabled'] else None
        s['thermal.target_reached'] = bool(s['power.enabled'] and s['truth.temperature_k'] is not None and s['thermal.setpoint_k'] is not None and _ahs_math.isclose(s['truth.temperature_k'], s['thermal.setpoint_k'], rel_tol=0, abs_tol=1e-9))

    def _stop(self, item, status):
        s = item['state']
        if s['sealing.status'] == 'running':
            s['sealing.status'] = status
            self._operations[item['active_operation']]['status'] = status

    def host_load_plate(self, instance_id, *, plate_id, height_m, plate_adapter_id, compatibility_evidence):
        with self._lock:
            item = self._require_idle(instance_id, True)
            s = item['state']
            if s['plate.loaded'] or not all(_ahs_string(x) for x in (plate_id, plate_adapter_id, compatibility_evidence)) or not _ahs_number(height_m) or not .003 <= height_m <= .060:
                raise ValueError('Empty carrier, explicit compatible IDs and plate height .003..060m required')
            s.update({'plate.loaded': True, 'plate.plate_id': plate_id, 'plate.height_m': float(height_m), 'plate.simulated_sealed': False,
                'sealing.status': 'idle', 'sealing.elapsed_s': 0.0, 'sealing.progress': 0.0})
            item['plate_adapter_id'], item['active_operation'] = plate_adapter_id, None
            item['evidence']['plate'] = compatibility_evidence
            item['revision'] += 1

    def host_unload_plate(self, instance_id):
        with self._lock:
            item = self._require_idle(instance_id, True)
            s = item['state']
            if not s['plate.loaded'] or s['plate.attachment_constraint_id'] is not None:
                raise ValueError('Loaded unattached plate required')
            s.update({'plate.loaded': False, 'plate.plate_id': None, 'plate.height_m': None, 'plate.simulated_sealed': False})
            item['plate_adapter_id'] = None
            item['revision'] += 1

    def host_load_film(self, instance_id, *, roll_id, width_m, remaining_seals, film_roll_position_id, compatibility_evidence):
        with self._lock:
            item = self._require_idle(instance_id, True)
            if not all(_ahs_string(x) for x in (roll_id, film_roll_position_id, compatibility_evidence)) or not _ahs_number(width_m) or not .075 <= width_m <= .085 or type(remaining_seals) is not int or not 0 <= remaining_seals <= 5000:
                raise ValueError('Compatible film IDs, width .075..085m and explicit remaining count 0..5000 required')
            item['state'].update({'film.roll_id': roll_id, 'film.width_m': float(width_m), 'film.remaining_seals': remaining_seals})
            item['film_position_id'] = film_roll_position_id
            item['evidence']['film'] = compatibility_evidence
            item['revision'] += 1

    def host_set_temperature(self, instance_id, *, temperature_K, evidence):
        with self._lock:
            item = self._get(instance_id)
            if item is None or not _ahs_number(temperature_K) or temperature_K <= 0 or not _ahs_string(evidence):
                raise ValueError('Positive finite host temperature and evidence required')
            item['state']['truth.temperature_k'] = float(temperature_K)
            item['evidence']['temperature'] = evidence
            self._refresh(item['state'])
            if not item['state']['thermal.target_reached']:
                self._stop(item, 'failed')
            item['revision'] += 1

    def host_configure_thermal_rate(self, instance_id, *, thermal_rate_K_s, evidence):
        with self._lock:
            item = self._require_idle(instance_id)
            if not _ahs_number(thermal_rate_K_s) or thermal_rate_K_s <= 0 or not _ahs_string(evidence):
                raise ValueError('Positive finite explicit linear thermal rate and evidence required')
            item['state'].update({'truth.thermal_rate_k_s': float(thermal_rate_K_s), 'simulation.thermal_rate_configured': True})
            item['evidence']['thermal_rate'] = evidence
            item['revision'] += 1

    def _attachment(self, instance_id, event_name, constraint_id, callback):
        with self._lock:
            item = self._require_idle(instance_id, True)
            s = item['state']
            if not s['plate.loaded'] or not callable(callback) or (event_name == 'attach' and (not _ahs_string(constraint_id) or s['plate.attachment_constraint_id'] is not None)) or (event_name == 'release' and s['plate.attachment_constraint_id'] is None):
                raise ValueError('Valid loaded plate, constraint and host callback required')
            event = {'event': event_name, 'plate_id': s['plate.plate_id'], 'constraint_id': constraint_id if event_name == 'attach' else s['plate.attachment_constraint_id']}
            try:
                confirmed = callback(instance_id, _ahs_copy.deepcopy(event)) is True
            except Exception:
                confirmed = False
            if not confirmed:
                raise RuntimeError('Host attachment callback did not confirm; external effect may be unknown')
            s['plate.attachment_constraint_id'] = constraint_id if event_name == 'attach' else None
            item['revision'] += 1
            return event

    def host_attach(self, instance_id, *, constraint_id, attachment_adapter):
        return self._attachment(instance_id, 'attach', constraint_id, attachment_adapter)

    def host_release(self, instance_id, *, attachment_adapter):
        return self._attachment(instance_id, 'release', None, attachment_adapter)

    def host_step(self, dt_s, *, world_time_s):
        with self._lock:
            if not _ahs_number(dt_s) or dt_s <= 0 or not _ahs_number(world_time_s) or not _ahs_number(self.time_s + dt_s) or not _ahs_math.isclose(world_time_s, self.time_s + dt_s, rel_tol=0, abs_tol=1e-9) or world_time_s <= self.time_s:
                raise ValueError('Positive finite dt and matching monotonic common world time required')
            for item in self._instances.values():
                s = item['state']
                before = _ahs_copy.deepcopy(s)
                temp, target, rate = s['truth.temperature_k'], s['thermal.setpoint_k'], s['truth.thermal_rate_k_s']
                if s['power.enabled'] and temp is not None and target is not None and rate is not None:
                    # Clamp before multiplication so even a huge finite rate cannot overflow.
                    span = abs(target - temp)
                    change = span if dt_s >= span / rate else dt_s * rate
                    s['truth.temperature_k'] = target if change >= span else temp + _ahs_math.copysign(change, target - temp)
                self._refresh(s)
                if s['sealing.status'] == 'running':
                    op = self._operations[item['active_operation']]
                    if not s['thermal.target_reached']:
                        self._stop(item, 'failed')
                    else:
                        elapsed = min(op['duration_s'], op['elapsed_s'] + dt_s)
                        op['elapsed_s'] = elapsed
                        s['sealing.elapsed_s'], s['sealing.progress'] = elapsed, elapsed / op['duration_s']
                        if elapsed >= op['duration_s']:
                            s['sealing.status'], s['plate.simulated_sealed'] = 'succeeded', True
                            op['status'], op['simulated_sealed'] = 'succeeded', True
                if s != before:
                    item['revision'] += 1
            self.time_s = float(world_time_s)
            return {'time_s': self.time_s}

    def debug_snapshot(self):
        with self._lock:
            return _ahs_copy.deepcopy({'time_s': self.time_s, 'instances': {i: {k: v for k, v in item.items() if k != 'adapter'} for i, item in self._instances.items()}, 'operations': self._operations})

    def _response(self, iid, request_id=None, *, status='succeeded', result=None, operation_id=None, code=None, side_effects='none'):
        item = self._get(iid)
        return {'protocol_version': '1.0.0', 'instance_id': iid, 'request_id': request_id, 'operation_id': operation_id,
            'status': status, 'state_revision': None if item is None or code == 'UNKNOWN_INSTANCE' else item['revision'], 'clock': {'domain': 'simulation', 'time_s': self.time_s},
            'result': _ahs_copy.deepcopy(result), 'observations': {}, 'error': None if code is None else {'code': code,
                'message': 'Operation could not be completed: ' + code, 'retryable': False, 'side_effects': side_effects, 'known_progress': None}}

    @staticmethod
    def _operation_result(op):
        return {'operation_id': op['id'], 'status': op['status'], 'elapsed_s': op['elapsed_s'],
            'progress': op['elapsed_s'] / op['duration_s'], 'simulated_sealed': op['simulated_sealed']}

    def _invoke(self, principal, iid, cid, args, request_id):
        with self._lock:
            reject = lambda code, status='rejected', side='none': self._response(iid, request_id, status=status, code=code, side_effects=side)
            item = self._get(iid, principal)
            if item is None:
                return reject('UNKNOWN_INSTANCE')
            if not isinstance(cid, str) or cid not in _AHS_COMPONENTS:
                return reject('UNKNOWN_CAPABILITY')
            if not _ahs_string(request_id) or not isinstance(args, dict):
                return reject('INVALID_ARGUMENT')
            try:
                fingerprint = _ahs_json.dumps([cid, args], sort_keys=True, allow_nan=False, separators=(',', ':'))
            except (TypeError, ValueError, OverflowError):
                return reject('INVALID_ARGUMENT')
            key = (principal, iid, request_id)
            if key in self._requests:
                old, response = self._requests[key]
                return _ahs_copy.deepcopy(response) if old == fingerprint else reject('REQUEST_CONFLICT')
            def reject(code, status='rejected', side='none'):
                response = self._response(iid, request_id, status=status, code=code, side_effects=side)
                self._requests[key] = (fingerprint, _ahs_copy.deepcopy(response))
                return response
            fields = {'power.set': ['enabled'], 'thermal.set_temperature': ['temperature_K'],
                'configuration.set': ['press_time_s', 'plate_adapter_id', 'film_roll_position_id'], 'scene.set_press_displacement': ['displacement_m'],
                'sealing.start': [], 'sealing.poll': ['operation_id'], 'sealing.cancel': ['operation_id'], 'sealing.read': [], 'sealing.set_pressure': []}[cid]
            valid = set(args) == set(fields)
            if valid and cid == 'power.set': valid = type(args['enabled']) is bool
            if valid and cid == 'thermal.set_temperature': valid = _ahs_number(args['temperature_K']) and 373.15 <= args['temperature_K'] <= 468.15
            if valid and cid == 'configuration.set': valid = _ahs_number(args['press_time_s']) and .1 <= args['press_time_s'] <= 10 and _ahs_string(args['plate_adapter_id']) and _ahs_string(args['film_roll_position_id'])
            if valid and cid == 'scene.set_press_displacement': valid = _ahs_number(args['displacement_m']) and 0 <= args['displacement_m'] <= AZENTA_HEAT_SEALER_Q_MAX_M
            if valid and cid in ('sealing.poll', 'sealing.cancel'): valid = _ahs_string(args['operation_id'])
            if not valid:
                return reject('INVALID_ARGUMENT')
            if cid == 'sealing.set_pressure':
                return reject('UNSUPPORTED', 'unsupported')
            s = item['state']
            if cid in ('thermal.set_temperature', 'configuration.set', 'sealing.start') and not s['power.enabled']:
                return reject('PRECONDITION_FAILED')
            if cid in ('thermal.set_temperature', 'configuration.set', 'scene.set_press_displacement', 'sealing.start') and s['sealing.status'] == 'running':
                return reject('BUSY')
            if cid == 'configuration.set' and (not s['plate.loaded'] or s['film.roll_id'] is None or args['plate_adapter_id'] != item['plate_adapter_id'] or args['film_roll_position_id'] != item['film_position_id']):
                return reject('PRECONDITION_FAILED')
            if cid == 'scene.set_press_displacement' and s['plate.loaded'] and args['displacement_m'] > .077 - s['plate.height_m'] + 1e-12:
                return reject('PRECONDITION_FAILED')
            if cid == 'sealing.start' and (not s['plate.loaded'] or s['plate.simulated_sealed'] or s['film.remaining_seals'] is None or s['film.remaining_seals'] < 1 or s['configuration.press_time_s'] is None or s['configuration.plate_adapter_id'] != item['plate_adapter_id'] or s['configuration.film_roll_position_id'] != item['film_position_id'] or not s['thermal.target_reached'] or not item['scene_confirmed'] or not _ahs_math.isclose(s['scene.press_displacement_m'], .077 - s['plate.height_m'], rel_tol=0, abs_tol=1e-9)):
                return reject('PRECONDITION_FAILED')
            op = None
            if cid in ('sealing.poll', 'sealing.cancel'):
                op = self._operations.get(args['operation_id'])
                if op is None or op['instance_id'] != iid or op['principal'] != principal:
                    return reject('UNKNOWN_OPERATION')
            if cid == 'scene.set_press_displacement':
                if item['adapter'] is None:
                    return reject('UNSUPPORTED', 'unsupported')
                try:
                    confirmed = item['adapter'](iid, {'scene.press_displacement_m': float(args['displacement_m'])}) is True
                except Exception:
                    confirmed = False
                if not confirmed:
                    response = reject('DEVICE_ERROR', 'failed', 'unknown')
                    self._requests[key] = (fingerprint, _ahs_copy.deepcopy(response))
                    return response
            before = _ahs_copy.deepcopy(s)
            status, op_id = 'succeeded', None
            if cid == 'power.set':
                s['power.enabled'] = args['enabled']
                self._refresh(s)
                if not args['enabled']:
                    self._stop(item, 'cancelled')
                result = {'enabled': args['enabled']}
            elif cid == 'thermal.set_temperature':
                s['thermal.setpoint_k'] = float(args['temperature_K'])
                self._refresh(s)
                result = {'setpoint_K': s['thermal.setpoint_k']}
            elif cid == 'configuration.set':
                for k, v in args.items(): s['configuration.' + k] = v
                result = args
            elif cid == 'scene.set_press_displacement':
                s['scene.press_displacement_m'] = float(args['displacement_m'])
                item['scene_confirmed'] = True
                result = {'displacement_m': s['scene.press_displacement_m']}
            elif cid == 'sealing.start':
                op_id = 'seal_%s_%d' % (iid, self._next_operation)
                self._next_operation += 1
                op = {'id': op_id, 'instance_id': iid, 'principal': principal, 'plate_id': s['plate.plate_id'], 'duration_s': s['configuration.press_time_s'],
                    'status': 'running', 'elapsed_s': 0.0, 'simulated_sealed': False}
                self._operations[op_id], item['active_operation'] = op, op_id
                s.update({'sealing.status': 'running', 'sealing.elapsed_s': 0.0, 'sealing.progress': 0.0, 'plate.simulated_sealed': False})
                s['film.remaining_seals'] -= 1
                status, result = 'accepted', self._operation_result(op)
            elif cid in ('sealing.poll', 'sealing.cancel'):
                op_id = op['id']
                if cid == 'sealing.cancel' and op['status'] == 'running': self._stop(item, 'cancelled')
                result = self._operation_result(op)
            else:
                result = {k: s[k] for k in _AHS_READ_IDS}
            if s != before or cid == 'scene.set_press_displacement':
                item['revision'] += 1
            response = self._response(iid, request_id, status=status, result=result, operation_id=op_id)
            self._requests[key] = (fingerprint, _ahs_copy.deepcopy(response))
            return response


class AzentaHeatSealerSession:
    """Transport facade; identities and host temperature values are not caller parameters."""
    def __init__(self, host, principal_id):
        if not isinstance(principal_id, str) or principal_id not in host._principals:
            raise ValueError('Host registration required')
        self.__host, self.__principal = host, principal_id

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                raise ValueError('Exactly one type or instance ID required')
            if (asset_type_id is not None and asset_type_id != AZENTA_HEAT_SEALER_TYPE) or (instance_id is not None and self.__host._get(instance_id, self.__principal) is None):
                return self.__host._response(instance_id, status='rejected', code='UNKNOWN_INSTANCE')
            m = export_azenta_heat_sealer_contract_metadata()
            m.pop('host_api')
            if self.__host._principals[self.__principal][0] == 'agent':
                m['state_slots'] = {k: v for k, v in m['state_slots'].items() if v['visibility'] == 'agent'}
                for cap in m['capabilities'].values():
                    cap['state_effects'] = [e for e in cap['state_effects'] if e['state_id'] in m['state_slots']]
            m['instance_id'] = instance_id
            if instance_id is not None and self.__host._instances[instance_id]['adapter'] is None:
                m['capabilities']['scene.set_press_displacement']['availability'] = 'unsupported'
            return m

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            h, item = self.__host, self.__host._get(instance_id, self.__principal)
            if item is None:
                return h._response(instance_id, status='rejected', code='UNKNOWN_INSTANCE')
            allowed = {k for k, v in _AHS_SLOTS.items() if h._principals[self.__principal][0] != 'agent' or v['visibility'] == 'agent'}
            if state_ids is None:
                state_ids = sorted(allowed)
            if not isinstance(state_ids, list) or any(not isinstance(k, str) or k not in allowed for k in state_ids):
                return h._response(instance_id, status='rejected', code='ACCESS_DENIED')
            r = h._response(instance_id)
            for sid in state_ids:
                val = _ahs_copy.deepcopy(item['state'][sid])
                r['observations'][sid] = {'value': val, 'unit': _AHS_SLOTS[sid]['unit'], 'quality': 'unavailable' if val is None else 'estimated' if sid.startswith('measurement.') or sid in ('thermal.target_reached', 'plate.simulated_sealed') else 'valid', 'sample_time_s': h.time_s, 'sample_window_s': None}
                if val is None:
                    r['observations'][sid]['unavailable_reason'] = 'not_configured_or_not_powered'
            return r

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        return self.__host._invoke(self.__principal, instance_id, capability_id, arguments, request_id)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self.__host._get(instance_id, self.__principal) is None:
                return self.__host._response(instance_id, request_id, status='rejected', code='UNKNOWN_INSTANCE')
            slot = _AHS_SLOTS.get(state_id) if isinstance(state_id, str) else None
            if slot is None or slot['write_via'] is None:
                return self.__host._response(instance_id, request_id, status='rejected', code='ACCESS_DENIED')
            via = slot['write_via']
            return self.invoke(instance_id=instance_id, capability_id=via['capability_id'], arguments={via['parameter']: value}, request_id=request_id)

    def poll(self, *, instance_id, operation_id):
        with self.__host._lock:
            h = self.__host
            if h._get(instance_id, self.__principal) is None:
                return h._response(instance_id, status='rejected', code='UNKNOWN_INSTANCE')
            op = h._operations.get(operation_id) if isinstance(operation_id, str) else None
            if op is None or op['instance_id'] != instance_id or op['principal'] != self.__principal:
                return h._response(instance_id, status='rejected', code='UNKNOWN_OPERATION')
            return h._response(instance_id, status=op['status'], result=h._operation_result(op), operation_id=operation_id)

    def cancel(self, *, instance_id, operation_id, request_id):
        return self.invoke(instance_id=instance_id, capability_id='sealing.cancel', arguments={'operation_id': operation_id}, request_id=request_id)
# END CHEM-176 AZENTA AUTOMATED ROLL HEAT SEALER APPEND-ONLY RUNTIME

# CHEM-176 discovery wording: the public schema explains filtering without naming private slots.
_ahs_export_metadata_v1 = export_azenta_heat_sealer_contract_metadata
def export_azenta_heat_sealer_contract_metadata():
    metadata = _ahs_export_metadata_v1()
    metadata['output_schema_scope'] = 'capability output_schema validates response.result; discovery contains no private instance values'
    return metadata

# BEGIN CHEM-175 OPENTRONS FLEX APPEND-ONLY RUNTIME
"""CHEM-175 synthetic joint-state and volume-ledger adapter.

Host inventory registration is a simulation setup operation, not a measurement.
Give agents a Session over a transport boundary; Python name privacy is not a
sandbox and these APIs do not secure a shared host filesystem.
"""
import copy as _flex_copy
import json as _flex_json
import math as _flex_math
import threading as _flex_threading

OPENTRONS_FLEX_TYPE = 'instrument.opentrons_flex_single_5_1000ul'
OPENTRONS_FLEX_MIN_M3 = 5e-9
OPENTRONS_FLEX_MAX_M3 = 1e-6
OPENTRONS_FLEX_WORK_SLOTS = tuple('%s%d' % (row, col) for row in 'ABCD' for col in (1, 2, 3))
OPENTRONS_FLEX_STAGING_SLOTS = tuple(row + '4' for row in 'ABCD')
OPENTRONS_FLEX_ALL_SLOTS = OPENTRONS_FLEX_WORK_SLOTS + OPENTRONS_FLEX_STAGING_SLOTS
_FLEX_LIMITS = {'x_m': (-0.24, 0.24), 'y_m': (-0.21, 0.21), 'z_m': (0.0, 0.22)}
_FLEX_POSITION_NOTE = 'Joint coordinates in m: X right, Y rear, Z up; asset tip port = (x_m,y_m,0.26+z_m). Travel bounds and 1e-9 m target equality tolerance are simulation conventions, not manufacturer tolerances.'


def _flex_number(value):
    try:
        return type(value) in (int, float) and _flex_math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _flex_id(value):
    return isinstance(value, str) and bool(value.strip())


def _flex_obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _flex_num(unit, minimum=None, maximum=None):
    result = {'type': 'number', 'x-unit': unit}
    if minimum is not None:
        result['minimum'] = minimum
    if maximum is not None:
        result['maximum'] = maximum
    return result


_FLEX_POSITION_SCHEMA = _flex_obj({key: _flex_num('m', *limits) for key, limits in _FLEX_LIMITS.items()})
_FLEX_NULL_ID = {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
_FLEX_CONTAINER_SCHEMA = _flex_obj({'container_id': {'type': 'string'}, 'slot_id': {'type': 'string', 'enum': list(OPENTRONS_FLEX_ALL_SLOTS)}, 'capacity_m3': _flex_num('m^3', 0), 'volume_m3': _flex_num('m^3', 0), 'position_m': _FLEX_POSITION_SCHEMA})
_FLEX_TIP_SCHEMA = _flex_obj({'tip_id': {'type': 'string'}, 'slot_id': _FLEX_NULL_ID, 'capacity_m3': _flex_num('m^3', OPENTRONS_FLEX_MIN_M3, OPENTRONS_FLEX_MAX_M3), 'volume_m3': _flex_num('m^3', 0), 'position_m': _FLEX_POSITION_SCHEMA})
_FLEX_TRANSFER_SCHEMA = _flex_obj({'transaction_id': {'type': 'string'}, 'donor_id': {'type': 'string'}, 'receiver_id': {'type': 'string'}, 'volume_m3': _flex_num('m^3', OPENTRONS_FLEX_MIN_M3, OPENTRONS_FLEX_MAX_M3), 'unit': {'type': 'string', 'const': 'm^3'}, 'kind': {'type': 'string', 'enum': ['aspirate', 'dispense']}, 'time_s': _flex_num('s', 0)})
_FLEX_EVENT_SCHEMA = _flex_obj({'event': {'type': 'string', 'enum': ['attach', 'release']}, 'tip_id': {'type': 'string'}, 'slot_id': {'type': 'string', 'enum': list(OPENTRONS_FLEX_ALL_SLOTS)}, 'time_s': _flex_num('s', 0), 'constraint_authority': {'type': 'string', 'const': 'host'}})


def _flex_slot(schema, unit, origin='scene', visibility='agent', write_via=None):
    return {'value_schema': _flex_copy.deepcopy(schema), 'unit': unit, 'origin': origin, 'visibility': visibility,
        'access': 'read_write' if write_via else 'read_only', 'write_via': write_via,
        'reference_conditions': _FLEX_POSITION_NOTE if unit == 'm' else None,
        'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}


_FLEX_SLOTS = {
    'power.enabled': _flex_slot({'type': 'boolean'}, '1', 'setpoint', write_via={'capability_id': 'power.set', 'parameter': 'enabled', 'fixed_arguments': {}}),
    **{'motion.' + k: _flex_slot(_flex_num('m', *v), 'm') for k, v in _FLEX_LIMITS.items()},
    'motion.scene_adapter_registered': _flex_slot({'type': 'boolean'}, '1', 'derived'),
    'tip.attached_id': _flex_slot(_FLEX_NULL_ID, '1'),
    'tip.last_slot_id': _flex_slot(_FLEX_NULL_ID, '1'),
    'tip.events': _flex_slot({'type': 'array', 'items': _FLEX_EVENT_SCHEMA}, '1'),
    'deck.work_slot_ids': _flex_slot({'type': 'array', 'items': {'type': 'string'}, 'minItems': 12, 'maxItems': 12}, '1'),
    'deck.staging_slot_ids': _flex_slot({'type': 'array', 'items': {'type': 'string'}, 'minItems': 4, 'maxItems': 4}, '1'),
    'sample.transfer_count': _flex_slot({'type': 'integer', 'minimum': 0, 'x-unit': '1'}, '1', 'derived'),
    'truth.tip_volume_m3': _flex_slot({'anyOf': [_flex_num('m^3', 0, OPENTRONS_FLEX_MAX_M3), {'type': 'null'}]}, 'm^3', 'process_truth', 'debug'),
    'truth.containers': _flex_slot({'type': 'array', 'items': _FLEX_CONTAINER_SCHEMA}, '1', 'process_truth', 'debug'),
    'truth.tips': _flex_slot({'type': 'array', 'items': _FLEX_TIP_SCHEMA}, '1', 'process_truth', 'debug'),
    'truth.transfers': _flex_slot({'type': 'array', 'items': _FLEX_TRANSFER_SCHEMA}, '1', 'process_truth', 'debug'),
}
_FLEX_PUBLIC = tuple(k for k, v in _FLEX_SLOTS.items() if v['visibility'] == 'agent')
_FLEX_ARGUMENTS = {
    'power.set': _flex_obj({'enabled': {'type': 'boolean'}}),
    'motion.move': _flex_copy.deepcopy(_FLEX_POSITION_SCHEMA),
    'sample.aspirate': _flex_obj({'container_id': {'type': 'string'}, 'volume_m3': _flex_num('m^3', OPENTRONS_FLEX_MIN_M3, OPENTRONS_FLEX_MAX_M3)}),
    'sample.dispense': _flex_obj({'container_id': {'type': 'string'}, 'volume_m3': _flex_num('m^3', OPENTRONS_FLEX_MIN_M3, OPENTRONS_FLEX_MAX_M3)}),
    'tip.attach': _flex_obj({'tip_id': {'type': 'string'}}),
    'tip.release': _flex_obj({'target_slot_id': {'type': 'string', 'enum': list(OPENTRONS_FLEX_ALL_SLOTS)}}),
    'state.read': _flex_obj({}),
    'velocity.set': _flex_obj({}), 'sample.mix': _flex_obj({}), 'liquid_level.detect': _flex_obj({}),
}
_FLEX_UNSUPPORTED = frozenset(('velocity.set', 'sample.mix', 'liquid_level.detect'))
_FLEX_LIMITATIONS = [
    '单通道5–1000 µL移液配置，12个工作位及4个暂存位；未配置夹爪，不实现接触抓取、机械拾头或硬件驱动。',
    '吸排为等体积转移账本；不求解流速、黏度、弯月面、液面、湿润、吹出、残留、压缩性、组分混合、液滴或移液精度。',
    '厂家精度只保存为型号规格；1000 µL系统误差±0.50%、CV 0.15%及5 µL系统误差±5.00%均不是本模型已验证精度。',
    '三轴行程及目标匹配公差是场景工程约定，不是厂家行程、精度或安全距离；速度和碰撞检测未实现。',
    '容器与吸头的容量、初始体积、工作位及目标关节位置由可信宿主登记，属于运行初始化输入，不能倒写为厂家部件规格。',
    '4个暂存位的物件由宿主放置；本配置无夹爪，不提供跨槽搬运。直接吸排仅允许工作位。',
    'attach/release要求宿主针对指定吸头、槽位和当前关节位置的确认；适配器只记录事件和附着身份，物理约束属于宿主。',
    '注册场景回调后motion.move同步三轴；未注册时只改变位置状态。回调抛异常时内部状态不提交，外部场景效果可能未知。',
    'read及state.read不推进时间；可信宿主显式提供共同simulation时钟，不实现每台仪器自动step。',
    '去重仅本进程内按调用身份、实例、request_id缓存；不声称跨重启恰好一次。API可见性过滤不是Python对象或共享文件系统隔离。',
]


def export_opentrons_flex_contract_metadata():
    """Return type-only metadata; caller assembles source and artifact bindings."""
    slots = _flex_copy.deepcopy(_FLEX_SLOTS)
    for sid, slot in slots.items():
        slot['read_binding'] = "OpentronsFlexSession.read(instance_id=..., state_ids=['%s'])" % sid
    effects = {
        'power.set': ['power.enabled'], 'motion.move': ['motion.x_m', 'motion.y_m', 'motion.z_m'],
        'sample.aspirate': ['truth.containers', 'truth.tips', 'truth.tip_volume_m3', 'truth.transfers', 'sample.transfer_count'],
        'sample.dispense': ['truth.containers', 'truth.tips', 'truth.tip_volume_m3', 'truth.transfers', 'sample.transfer_count'],
        'tip.attach': ['tip.attached_id', 'tip.last_slot_id', 'tip.events', 'truth.tip_volume_m3', 'truth.tips'],
        'tip.release': ['tip.attached_id', 'tip.last_slot_id', 'tip.events', 'truth.tip_volume_m3', 'truth.tips'],
        'state.read': [], 'velocity.set': [], 'sample.mix': [], 'liquid_level.detect': []}
    observes = {'power.set': ['power.enabled'], 'motion.move': ['motion.x_m', 'motion.y_m', 'motion.z_m'],
        'tip.attach': ['tip.attached_id', 'tip.last_slot_id'], 'tip.release': ['tip.attached_id', 'tip.last_slot_id'],
        'sample.aspirate': ['sample.transfer_count'], 'sample.dispense': ['sample.transfer_count'],
        'state.read': list(_FLEX_PUBLIC), 'velocity.set': [], 'sample.mix': [], 'liquid_level.detect': []}
    examples = {'power.set': {'enabled': True}, 'motion.move': {'x_m': -0.23, 'y_m': 0.21, 'z_m': 0.0},
        'sample.aspirate': {'container_id': 'host_registered_donor', 'volume_m3': 5e-9},
        'sample.dispense': {'container_id': 'host_registered_receiver', 'volume_m3': 5e-9},
        'tip.attach': {'tip_id': 'host_registered_tip'}, 'tip.release': {'target_slot_id': 'A1'},
        'state.read': {}, 'velocity.set': {}, 'sample.mix': {}, 'liquid_level.detect': {}}
    summaries = {'power.set': '设置仿真供电，不清除样品或改变真实硬件。', 'motion.move': '原子提交三轴关节位置；已注册时调用宿主场景回调。',
        'sample.aspirate': '从当前工作位目标容器向已附着吸头转移指定SI体积。', 'sample.dispense': '从已附着吸头向当前工作位目标容器转移指定SI体积。',
        'tip.attach': '消费宿主确认并记录attach附着事件；不执行接触拾头。', 'tip.release': '消费宿主确认并记录release事件；吸头及剩余样品保留在宿主登记的释放位置。',
        'state.read': '读取代理可见状态，不推进时间。', 'velocity.set': '速度及加速度曲线未实现。', 'sample.mix': '混合及组分输运未实现。', 'liquid_level.detect': '液面与传感器模型未实现。'}
    preconditions = {'power.set': [], 'motion.move': ['已上电；三个有限关节坐标均在仿真约定范围内；仅目标匹配，无轨迹或碰撞互锁'],
        'sample.aspirate': ['已上电并附着宿主登记吸头；容器为工作位且当前关节位置匹配登记目标；供体体积充足且吸头剩余容量充足'],
        'sample.dispense': ['已上电并附着宿主登记吸头；容器为工作位且当前关节位置匹配登记目标；吸头体积充足且受体剩余容量充足'],
        'tip.attach': ['已上电、没有已附着吸头；登记吸头位于工作位且当前位置匹配；宿主确认指定attach事件和同一位置'],
        'tip.release': ['已上电、有已附着吸头；宿主确认同一吸头、释放槽位和当前位置'],
        'state.read': [], 'velocity.set': [], 'sample.mix': [], 'liquid_level.detect': []}
    caps = {}
    for cid, input_schema in _FLEX_ARGUMENTS.items():
        mode = 'host_required' if cid.startswith('tip.') else 'coupled' if cid == 'motion.move' else 'none'
        model_note = '宿主约束已确认后记录attach/release事件；机器人与接触抓取absent。' if mode == 'host_required' else '三轴由运行状态单向同步到已注册宿主场景回调；未注册时只有状态更新。' if mode == 'coupled' else '纯体积账本或状态查询；没有液体、显示屏或其他可见几何变化。'
        caps[cid] = {'availability': 'unsupported' if cid in _FLEX_UNSUPPORTED else 'implemented', 'visibility': 'agent',
            'kind': 'query' if cid == 'state.read' else 'device_command', 'description_zh': summaries[cid],
            'input_schema': _flex_copy.deepcopy(input_schema), 'output_schema': _flex_obj({sid: _flex_copy.deepcopy(slots[sid]['value_schema']) for sid in observes[cid]}),
            'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': '原子提交此状态；拒绝前提不改变状态'} for sid in effects[cid]],
            'observes': observes[cid], 'preconditions_zh': preconditions[cid], 'example_arguments': [examples[cid]],
            'binding': None if cid in _FLEX_UNSUPPORTED else "basic40_runtime_automation.OpentronsFlexSession.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % cid,
            'model_effects': {'mode': mode, 'binding_ids': [], 'description_zh': model_note},
            'implementation': {'process': 'absent' if cid in _FLEX_UNSUPPORTED else 'implemented', 'scene': 'declared' if mode != 'none' else 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'implemented' if cid not in _FLEX_UNSUPPORTED else 'absent'}}
    return {'asset_type_id': OPENTRONS_FLEX_TYPE, 'catalog_id': 'CHEM-175', 'asset_version': '1.0.1',
        'state_slots': slots, 'capabilities': caps, 'parameters': {
            'pipette_min_volume_m3': {'value': OPENTRONS_FLEX_MIN_M3, 'unit': 'm^3', 'evidence': 'catalog'},
            'pipette_max_volume_m3': {'value': OPENTRONS_FLEX_MAX_M3, 'unit': 'm^3', 'evidence': 'catalog'},
            'whole_instrument_width_m': {'value': 0.87, 'unit': 'm', 'evidence': 'catalog'},
            'whole_instrument_depth_m': {'value': 0.69, 'unit': 'm', 'evidence': 'catalog'},
            'whole_instrument_height_m': {'value': 0.84, 'unit': 'm', 'evidence': 'catalog'},
            'whole_instrument_mass_kg': {'value': 88.5, 'unit': 'kg', 'evidence': 'catalog'},
            'systematic_error_fraction_at_1000ul': {'value': 0.005, 'unit': '1', 'evidence': 'catalog', 'note_zh': '±厂家规格，不生成或验证误差'},
            'cv_fraction_at_1000ul': {'value': 0.0015, 'unit': '1', 'evidence': 'catalog', 'note_zh': '厂家规格，不生成或验证误差'},
            'systematic_error_fraction_at_5ul': {'value': 0.05, 'unit': '1', 'evidence': 'catalog', 'note_zh': '±厂家规格，不生成或验证误差'},
            'tip_capacity_m3': {'value': None, 'unit': 'm^3', 'evidence': 'unknown', 'note_zh': '所用吸头型号不详；由宿主逐吸头登记运行容量'},
            'pipette_component_mass_kg': {'value': None, 'unit': 'kg', 'evidence': 'unknown'},
            'gantry_component_mass_kg': {'value': None, 'unit': 'kg', 'evidence': 'unknown'},
            'motion_speed_m_s': {'value': None, 'unit': 'm/s', 'evidence': 'unknown'},
            'volume_flow_rate_m3_s': {'value': None, 'unit': 'm^3/s', 'evidence': 'unknown'},
            **{'motion_' + key + '_bounds': {'value': list(v), 'unit': 'm', 'evidence': 'assumed', 'note_zh': _FLEX_POSITION_NOTE} for key, v in _FLEX_LIMITS.items()}},
        'host_api': {'create_instance': 'OpentronsFlexRuntime.create_instance(instance_id)',
            'register_principal': 'OpentronsFlexRuntime.register_principal(principal_id,visibility=...,instance_ids=...)',
            'host.register_container': 'OpentronsFlexRuntime.host_register_container(instance_id,container_id,slot_id=...,capacity_m3=...,volume_m3=...,position_m={x_m,y_m,z_m},evidence=...)',
            'host.register_tip': 'OpentronsFlexRuntime.host_register_tip(instance_id,tip_id,slot_id=...,capacity_m3=...,position_m={x_m,y_m,z_m},evidence=...)',
            'host.confirm_tip_event': 'OpentronsFlexRuntime.host_confirm_tip_event(instance_id,event=attach|release,tip_id=...,slot_id=...,position_m={x_m,y_m,z_m},evidence=...)',
            'host.register_scene_adapter': 'OpentronsFlexRuntime.host_register_scene_adapter(instance_id,callback); callback(instance_id,{x_m,y_m,z_m})',
            'host.sync_scene': 'OpentronsFlexRuntime.host_sync_scene(instance_id)',
            'host.set_clock': 'OpentronsFlexRuntime.host_set_clock(time_s), monotonic common simulation clock'},
        'limitations': list(_FLEX_LIMITATIONS), 'output_schema_scope': 'output_schema validates response.result; observations and describe are filtered by trusted host identity'}


# read(): why a slot has no value (quality 'unavailable')
_FLEX_EMPTY_REASONS = {
    'tip.attached_id': 'No tip is attached.',
    'tip.last_slot_id': 'No tip has been picked up or dropped yet.',
    'truth.tip_volume_m3': 'No tip is attached.',
    'run.state': 'No protocol run is set up.',
    'protocols.newest_id': 'No protocol is stored on the robot.',
    'usb.drive': 'No USB drive is inserted.',
}


class OpentronsFlexRuntime:
    """Trusted host for isolated instrument inventories, joint states and sessions."""
    def __init__(self):
        self.time_s = 0.0
        self._instances, self._principals, self._requests = {}, {}, {}
        self._lock = _flex_threading.RLock()

    def create_instance(self, instance_id, asset_type_id=OPENTRONS_FLEX_TYPE):
        with self._lock:
            if not _flex_id(instance_id) or instance_id in self._instances or asset_type_id != OPENTRONS_FLEX_TYPE:
                raise ValueError('Unique instance ID and supported asset type required')
            state = {'power.enabled': False, 'motion.x_m': 0.0, 'motion.y_m': 0.0, 'motion.z_m': 0.0,
                'motion.scene_adapter_registered': False, 'tip.attached_id': None, 'tip.last_slot_id': None, 'tip.events': [],
                'deck.work_slot_ids': list(OPENTRONS_FLEX_WORK_SLOTS), 'deck.staging_slot_ids': list(OPENTRONS_FLEX_STAGING_SLOTS),
                'sample.transfer_count': 0, 'truth.tip_volume_m3': None, 'truth.containers': [], 'truth.tips': [], 'truth.transfers': []}
            self._instances[instance_id] = {'state': state, 'revision': 0, 'containers': {}, 'tips': {}, 'evidence': {}, 'tip_confirmation': None, 'adapter': None}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not _flex_id(principal_id) or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Unique host-authenticated principal and valid visibility required')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(i, str) or i not in self._instances for i in instance_ids)):
                raise ValueError('Invalid instance scope')
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))

    def session(self, principal_id):
        return OpentronsFlexSession(self, principal_id)

    def _host_instance(self, instance_id):
        if not isinstance(instance_id, str) or instance_id not in self._instances:
            raise ValueError('Unknown instance')
        return self._instances[instance_id]

    def _instance(self, instance_id, principal):
        if not isinstance(instance_id, str) or principal not in self._principals:
            return None
        scope = self._principals[principal][1]
        return self._instances.get(instance_id) if scope is None or instance_id in scope else None

    @staticmethod
    def _position_valid(position):
        return isinstance(position, dict) and set(position) == set(_FLEX_LIMITS) and all(_flex_number(position[k]) and limits[0] <= position[k] <= limits[1] for k, limits in _FLEX_LIMITS.items())

    @staticmethod
    def _position(item):
        return {key: item['state']['motion.' + key] for key in _FLEX_LIMITS}

    @staticmethod
    def _position_matches(left, right):
        return all(abs(left[key] - right[key]) <= 1e-9 for key in _FLEX_LIMITS)

    @staticmethod
    def _sync_inventory(item):
        state = item['state']
        state['truth.containers'] = _flex_copy.deepcopy(list(item['containers'].values()))
        state['truth.tips'] = _flex_copy.deepcopy(list(item['tips'].values()))
        tip_id = state['tip.attached_id']
        state['truth.tip_volume_m3'] = item['tips'][tip_id]['volume_m3'] if tip_id is not None else None

    def host_register_container(self, instance_id, container_id, *, slot_id, capacity_m3, volume_m3, position_m, evidence):
        with self._lock:
            item = self._host_instance(instance_id)
            if (not _flex_id(container_id) or container_id in item['containers'] or container_id in item['tips'] or not isinstance(slot_id, str) or slot_id not in OPENTRONS_FLEX_ALL_SLOTS
                or not _flex_number(capacity_m3) or capacity_m3 <= 0 or not _flex_number(volume_m3) or not 0 <= volume_m3 <= capacity_m3
                or not self._position_valid(position_m) or not _flex_id(evidence)):
                raise ValueError('Valid unique container, slot, finite capacity/inventory, target position and evidence required')
            item['containers'][container_id] = {'container_id': container_id, 'slot_id': slot_id, 'capacity_m3': float(capacity_m3), 'volume_m3': float(volume_m3), 'position_m': _flex_copy.deepcopy(position_m)}
            item['evidence'][container_id] = evidence
            self._sync_inventory(item)
            item['revision'] += 1

    def host_register_tip(self, instance_id, tip_id, *, slot_id, capacity_m3, position_m, evidence):
        with self._lock:
            item = self._host_instance(instance_id)
            if (not _flex_id(tip_id) or tip_id in item['tips'] or tip_id in item['containers'] or not isinstance(slot_id, str) or slot_id not in OPENTRONS_FLEX_ALL_SLOTS
                or not _flex_number(capacity_m3) or not OPENTRONS_FLEX_MIN_M3 <= capacity_m3 <= OPENTRONS_FLEX_MAX_M3
                or not self._position_valid(position_m) or not _flex_id(evidence)):
                raise ValueError('Valid unique empty tip, slot, capacity, target position and evidence required')
            item['tips'][tip_id] = {'tip_id': tip_id, 'slot_id': slot_id, 'capacity_m3': float(capacity_m3), 'volume_m3': 0.0, 'position_m': _flex_copy.deepcopy(position_m)}
            item['evidence'][tip_id] = evidence
            self._sync_inventory(item)
            item['revision'] += 1

    def host_confirm_tip_event(self, instance_id, *, event, tip_id, slot_id, position_m, evidence):
        """Trusted assertion of an external host attachment/release constraint.

        Confirmation is bound to current position and consumed once by tip API;
        host must supply the actual constraint or external confirmation. No robot
        contact, collision, latch force or constraint creation occurs here.
        """
        with self._lock:
            item = self._host_instance(instance_id)
            if (event not in ('attach', 'release') or not isinstance(tip_id, str) or tip_id not in item['tips']
                or not isinstance(slot_id, str) or slot_id not in OPENTRONS_FLEX_ALL_SLOTS or not self._position_valid(position_m)
                or not self._position_matches(position_m, self._position(item)) or not _flex_id(evidence)):
                raise ValueError('Known tip, slot, current position and host constraint evidence required')
            item['tip_confirmation'] = {'event': event, 'tip_id': tip_id, 'slot_id': slot_id, 'position_m': _flex_copy.deepcopy(position_m), 'evidence': evidence}

    def host_register_scene_adapter(self, instance_id, callback):
        with self._lock:
            item = self._host_instance(instance_id)
            if not callable(callback):
                raise ValueError('Trusted callable scene adapter required')
            item['adapter'] = callback
            item['state']['motion.scene_adapter_registered'] = True
            item['revision'] += 1

    def host_sync_scene(self, instance_id):
        with self._lock:
            item = self._host_instance(instance_id)
            if item['adapter'] is None:
                raise ValueError('No scene adapter registered')
            item['adapter'](instance_id, self._position(item))

    def host_set_clock(self, time_s):
        with self._lock:
            if not _flex_number(time_s) or time_s < self.time_s:
                raise ValueError('Finite nondecreasing shared host time required')
            self.time_s = float(time_s)

    def debug_snapshot(self):
        with self._lock:
            return {'time_s': self.time_s, 'instances': {iid: _flex_copy.deepcopy({k: v for k, v in item.items() if k != 'adapter'}) for iid, item in self._instances.items()}}

    def _response(self, instance_id, request_id=None, *, status='succeeded', result=None, code=None, side_effects='none'):
        item = self._instances.get(instance_id) if isinstance(instance_id, str) else None
        return {'protocol_version': '1.0.0', 'instance_id': instance_id if isinstance(instance_id, str) else None,
            'request_id': request_id if isinstance(request_id, str) else None, 'operation_id': None,
            'status': status, 'state_revision': item['revision'] if item else None,
            'clock': {'domain': 'simulation', 'time_s': self.time_s}, 'result': _flex_copy.deepcopy(result) if result is not None else {}, 'observations': {},
            'error': {'code': code, 'message': {'PRECONDITION_FAILED': 'Required host registration, position, attachment or volume constraint is not satisfied.', 'DEVICE_ERROR': 'Scene adapter did not complete; external scene effects require host inspection.'}.get(code, code), 'retryable': False, 'side_effects': side_effects} if code else None}

    @staticmethod
    def _arguments_valid(cid, args):
        if not isinstance(args, dict) or set(args) != set(_FLEX_ARGUMENTS[cid]['properties']):
            return False
        if cid == 'power.set':
            return type(args['enabled']) is bool
        if cid == 'motion.move':
            return OpentronsFlexRuntime._position_valid(args)
        if cid in ('sample.aspirate', 'sample.dispense'):
            return _flex_id(args['container_id']) and _flex_number(args['volume_m3']) and OPENTRONS_FLEX_MIN_M3 <= args['volume_m3'] <= OPENTRONS_FLEX_MAX_M3
        if cid == 'tip.attach':
            return _flex_id(args['tip_id'])
        if cid == 'tip.release':
            return isinstance(args['target_slot_id'], str) and args['target_slot_id'] in OPENTRONS_FLEX_ALL_SLOTS
        return True

    def _apply(self, item, iid, cid, args, rid):
        state = item['state']
        fail = lambda: self._response(iid, rid, status='rejected', code='PRECONDITION_FAILED')
        if cid == 'state.read':
            return self._response(iid, rid, result={sid: state[sid] for sid in _FLEX_PUBLIC})
        if cid != 'power.set' and not state['power.enabled']:
            return fail()
        if cid == 'power.set':
            state['power.enabled'] = args['enabled']
        elif cid == 'motion.move':
            if item['adapter'] is not None:
                try:
                    item['adapter'](iid, _flex_copy.deepcopy(args))
                except Exception:
                    return self._response(iid, rid, status='failed', code='DEVICE_ERROR', side_effects='unknown')
            for key, value in args.items():
                state['motion.' + key] = float(value)
            # A confirmation cannot authorize a later visit to the same location.
            item['tip_confirmation'] = None
        elif cid.startswith('sample.'):
            container = item['containers'].get(args['container_id'])
            tip_id = state['tip.attached_id']
            if container is None or tip_id is None or container['slot_id'] not in OPENTRONS_FLEX_WORK_SLOTS or not self._position_matches(self._position(item), container['position_m']):
                return fail()
            tip = item['tips'][tip_id]
            donor, receiver = (container, tip) if cid == 'sample.aspirate' else (tip, container)
            volume = float(args['volume_m3'])
            if donor['volume_m3'] < volume or receiver['capacity_m3'] - receiver['volume_m3'] < volume:
                return fail()
            donor['volume_m3'] -= volume
            receiver['volume_m3'] += volume
            sequence = state['sample.transfer_count'] + 1
            state['truth.transfers'].append({'transaction_id': '%s.transfer.%d' % (iid, sequence), 'donor_id': donor.get('container_id', donor.get('tip_id')), 'receiver_id': receiver.get('container_id', receiver.get('tip_id')), 'volume_m3': volume, 'unit': 'm^3', 'kind': cid.split('.')[1], 'time_s': self.time_s})
            state['sample.transfer_count'] = sequence
            self._sync_inventory(item)
        elif cid in ('tip.attach', 'tip.release'):
            event = cid.split('.')[1]
            tip_id = args.get('tip_id') if event == 'attach' else state['tip.attached_id']
            tip = item['tips'].get(tip_id)
            slot = tip['slot_id'] if tip is not None and event == 'attach' else args.get('target_slot_id')
            confirmation = item['tip_confirmation']
            if (tip is None or (event == 'attach' and (state['tip.attached_id'] is not None or slot not in OPENTRONS_FLEX_WORK_SLOTS or not self._position_matches(self._position(item), tip['position_m'])))
                or confirmation is None or any(confirmation[k] != v for k, v in (('event', event), ('tip_id', tip_id), ('slot_id', slot)))
                or not self._position_matches(self._position(item), confirmation['position_m'])):
                return fail()
            state['tip.attached_id'] = tip_id if event == 'attach' else None
            state['tip.last_slot_id'] = slot
            tip['slot_id'] = None if event == 'attach' else slot
            if event == 'release':
                tip['position_m'] = self._position(item)
            state['tip.events'].append({'event': event, 'tip_id': tip_id, 'slot_id': slot, 'time_s': self.time_s, 'constraint_authority': 'host'})
            item['tip_confirmation'] = None
            self._sync_inventory(item)
        item['revision'] += 1
        observes = export_opentrons_flex_contract_metadata()['capabilities'][cid]['observes']
        return self._response(iid, rid, result={sid: state[sid] for sid in observes})

    def _invoke(self, principal, iid, cid, args, rid):
        with self._lock:
            item = self._instance(iid, principal)
            if item is None:
                # Do not expose revision of an existing out-of-scope instance.
                response = self._response(None, rid, status='rejected', code='UNKNOWN_INSTANCE')
                response['instance_id'] = iid if isinstance(iid, str) else None
                return response
            if not _flex_id(rid) or not isinstance(cid, str):
                return self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
            try:
                fingerprint = _flex_json.dumps([cid, args], sort_keys=True, allow_nan=False, separators=(',', ':'))
            except (ValueError, TypeError, OverflowError):
                return self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
            key = (principal, iid, rid)
            if key in self._requests:
                previous, response = self._requests[key]
                return _flex_copy.deepcopy(response) if previous == fingerprint else self._response(iid, rid, status='rejected', code='REQUEST_CONFLICT')
            if cid not in _FLEX_ARGUMENTS:
                response = self._response(iid, rid, status='rejected', code='UNKNOWN_CAPABILITY')
            elif not self._arguments_valid(cid, args):
                response = self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
            elif cid in _FLEX_UNSUPPORTED:
                response = self._response(iid, rid, status='unsupported', code='UNSUPPORTED')
            else:
                response = self._apply(item, iid, cid, args, rid)
            self._requests[key] = (fingerprint, _flex_copy.deepcopy(response))
            return response


class OpentronsFlexSession:
    """Identity-bound public protocol facade; host assigns visibility and scope."""
    def __init__(self, host, principal_id):
        if not isinstance(principal_id, str) or principal_id not in host._principals:
            raise ValueError('Host authentication and registration required')
        self.__host, self.__principal = host, principal_id

    def _visible(self):
        return set(_FLEX_PUBLIC) if self.__host._principals[self.__principal][0] == 'agent' else set(_FLEX_SLOTS)

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            h = self.__host
            if (asset_type_id is None) == (instance_id is None):
                return h._response(None, status='rejected', code='INVALID_ARGUMENT')
            if (asset_type_id is not None and asset_type_id != OPENTRONS_FLEX_TYPE) or (instance_id is not None and h._instance(instance_id, self.__principal) is None):
                return h._response(None, status='rejected', code='UNKNOWN_INSTANCE')
            result = export_opentrons_flex_contract_metadata()
            result.pop('host_api')
            allowed = self._visible()
            result['state_slots'] = {sid: slot for sid, slot in result['state_slots'].items() if sid in allowed}
            for cap in result['capabilities'].values():
                cap['state_effects'] = [entry for entry in cap['state_effects'] if entry['state_id'] in allowed]
                if cap['model_effects']['mode'] == 'coupled' and instance_id is not None and h._instances[instance_id]['adapter'] is None:
                    cap['model_effects'] = {'mode': 'none', 'binding_ids': [], 'description_zh': '当前实例未注册宿主场景回调；仅位置状态变更。'}
                    cap['implementation']['scene'] = 'absent'
            result['instance_id'] = instance_id
            return result

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            h, allowed = self.__host, self._visible()
            item = h._instance(instance_id, self.__principal)
            if item is None:
                return h._response(None, status='rejected', code='UNKNOWN_INSTANCE')
            if state_ids is None:
                state_ids = sorted(allowed)
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(set(state_ids)) != len(state_ids):
                return h._response(instance_id, status='rejected', code='INVALID_ARGUMENT')
            if any(sid not in allowed for sid in state_ids):
                return h._response(instance_id, status='rejected', code='ACCESS_DENIED')
            response = h._response(instance_id)
            for sid in state_ids:
                value = _flex_copy.deepcopy(item['state'][sid])
                response['observations'][sid] = {'value': value, 'unit': _FLEX_SLOTS[sid]['unit'], 'quality': 'unavailable' if value is None else 'estimated' if _FLEX_SLOTS[sid]['origin'] == 'process_truth' else 'valid', 'sample_time_s': h.time_s, 'sample_window_s': None}
                if value is None:
                    response['observations'][sid]['reason'] = _FLEX_EMPTY_REASONS.get(sid, 'No value yet.')
            return response

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        return self.__host._invoke(self.__principal, instance_id, capability_id, arguments, request_id)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self.__host._instance(instance_id, self.__principal) is None:
                return self.__host._response(None, request_id, status='rejected', code='UNKNOWN_INSTANCE')
            slot = _FLEX_SLOTS.get(state_id) if isinstance(state_id, str) else None
            if slot is None or slot['write_via'] is None or state_id not in self._visible():
                return self.__host._response(instance_id, request_id, status='rejected', code='ACCESS_DENIED')
            via = slot['write_via']
            return self.invoke(instance_id=instance_id, capability_id=via['capability_id'], arguments={via['parameter']: value}, request_id=request_id)
# END CHEM-175 OPENTRONS FLEX APPEND-ONLY RUNTIME

# BEGIN CHEM-174 QUANTOS Q2 XPE206DR APPEND-ONLY RUNTIME
import copy as _qt_copy
import json as _qt_json
import math as _qt_math
import threading as _qt_threading
import sys as _qt_sys

QUANTOS_Q2_TYPE = 'instrument.mt_quantos_q2_xpe206dr'


def _qt_number(v):
    try:
        return type(v) in (int, float) and _qt_math.isfinite(v)
    except (OverflowError, TypeError):
        return False


def _qt_text(v):
    return isinstance(v, str) and bool(v.strip())


def _qt_num(unit, minimum=0, maximum=None, nullable=False):
    s = {'type': 'number', 'x-unit': unit}
    if minimum is not None:
        s['minimum'] = minimum
    if maximum is not None:
        s['maximum'] = maximum
    return {'anyOf': [s, {'type': 'null'}]} if nullable else s


def _qt_obj(props):
    return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}


def _qt_slot(schema, unit, origin, via=None, parameter=None, visibility='agent'):
    return {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': visibility,
        'access': 'read_write' if via else 'read_only', 'reference_conditions': None,
        'write_via': {'capability_id': via, 'parameter': parameter, 'fixed_arguments': {}} if via else None,
        'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}


_QT_BOOL = {'type': 'boolean'}
_QT_STR = {'type': 'string', 'minLength': 1}
_QT_NS = {'anyOf': [_QT_STR, {'type': 'null'}]}
_QT_SLOTS = {
    'power.enabled': _qt_slot(_QT_BOOL, '1', 'setpoint', 'power.set', 'enabled'),
    'dosing.target_mass_kg': _qt_slot(_qt_num('kg', 1e-6, .005, True), 'kg', 'setpoint', 'dosing.set_target', 'target_mass_kg'),
    'dosing.tolerance_kg': _qt_slot(_qt_num('kg', nullable=True), 'kg', 'setpoint', 'dosing.set_tolerance', 'tolerance_kg'),
    'head.selected_id': _qt_slot(_QT_NS, '1', 'setpoint', 'head.select', 'head_id'),
    'head.loaded_id': _qt_slot(_QT_NS, '1', 'scene'),
    'container.position_id': _qt_slot(_QT_NS, '1', 'setpoint', 'container.select_position', 'position_id'),
    'container.loaded_id': _qt_slot(_QT_NS, '1', 'scene'),
    'container.attachment_constraint_id': _qt_slot(_QT_NS, '1', 'scene'),
    'scene.door_opening_m': _qt_slot(_qt_num('m', nullable=True), 'm', 'scene', 'door.set_opening', 'opening_m'),
    'balance.tared': _qt_slot(_QT_BOOL, '1', 'derived'),
    'measurement.balance_net_kg': _qt_slot(_qt_num('kg', nullable=True), 'kg', 'derived'),
    'measurement.actual_dosed_kg': _qt_slot(_qt_num('kg', nullable=True), 'kg', 'derived'),
    'measurement.error_kg': _qt_slot(_qt_num('kg', minimum=None, nullable=True), 'kg', 'derived'),
    'measurement.out_of_tolerance': _qt_slot({'anyOf': [_QT_BOOL, {'type': 'null'}]}, '1', 'derived'),
    'dosing.elapsed_s': _qt_slot(_qt_num('s'), 's', 'derived'),
    'dosing.status': _qt_slot({'type': 'string', 'enum': ['idle', 'running', 'succeeded', 'failed', 'cancelled']}, '1', 'derived'),
    'simulation.rate_configured': _qt_slot(_QT_BOOL, '1', 'derived'),
    'truth.source_mass_kg': _qt_slot(_qt_num('kg', nullable=True), 'kg', 'process_truth', visibility='debug'),
    'truth.container_mass_kg': _qt_slot(_qt_num('kg', nullable=True), 'kg', 'process_truth', visibility='debug'),
    'truth.actual_dosed_kg': _qt_slot(_qt_num('kg', nullable=True), 'kg', 'process_truth', visibility='debug'),
}
_QT_READ_IDS = ['dosing.status', 'dosing.target_mass_kg', 'dosing.tolerance_kg', 'measurement.actual_dosed_kg', 'measurement.error_kg', 'measurement.out_of_tolerance', 'dosing.elapsed_s', 'measurement.balance_net_kg']
_QT_MEASURE_IDS = [k for k in _QT_SLOTS if k.startswith('measurement.')]
_QT_LIMITATIONS = [
    '仅实现单个装粉头槽与一个已放置容器；可选30位自动进样器未装配，容器位置选择只匹配宿主登记位置，不驱动转盘。',
    '范围1 mg–5 g仅适用于宿主明确确认的自由流动粉末；无默认物料、库存、允许偏差、速率、天平容量或读数精度。',
    '碳酸钙10 mg典型22 s、RSD 1.15%；50 mg RSD 0.26%；1000 mg典型71 s仅作为清单资料，不计算任意粉末速率、RSD或随机误差。',
    '投料采用显式宿主速率与理想闭环连续停止模型，实际读数为estimated理想质量代理；没有粉体流变、扬尘、残留、天平动态或真实误差模型。',
    '前门为杠杆抬起的上挂玻璃门（说明书p.25-26、p.67-68；宣传册p.10侧视图）：scene.door_opening_m为门上沿铰销沿杠杆圆弧走过的距离（杠杆转角=开度/0.198 m，全开0.5495 m，图纸推算），宿主带证据配置行程并经场景回调确认；左右侧门沿+Y后滑，Doorway 100 %行程0.16 m为图纸估计（说明书未给数值）；不声称原厂门速度或安全互锁认证。',
    'attach/release仅在外部约束回调明确确认后登记；没有机器人规划、接触抓取或硬件驱动。',
    '只有可信宿主管理库存、物质转移账本、身份和时间；API过滤不是Python或共享文件系统隔离。请求去重仅当前内存有效。',
]


def export_quantos_q2_contract_metadata():
    slots = _qt_copy.deepcopy(_QT_SLOTS)
    for sid, slot in slots.items():
        slot['read_binding'] = 'QuantosQ2Session.read(instance_id=..., state_ids=[%r])' % sid
    slots['scene.door_opening_m']['reference_conditions'] = ('front door: q = path of the door top hinge pin along the lever arc; quantos__front_door_lever.rotation_euler.x=-q/%g, '
                                                             'quantos__front_door_hinge follows as mimic (x%g); 0 = closed, full open %.4f m; host-configured upper bound'
                                                             % (QUANTOS_FRONT_DOOR['lever_length_m'], QUANTOS_FRONT_DOOR['mimic'], QUANTOS_FRONT_DOOR['travel_m']))
    specs = {
        'power.set': ({'enabled': _QT_BOOL}, ['power.enabled'] + _QT_MEASURE_IDS + ['balance.tared', 'dosing.status'], ['power.enabled'], {'enabled': True}, ['terminal'], []),
        'dosing.set_target': ({'target_mass_kg': _qt_num('kg', 1e-6, .005)}, ['dosing.target_mass_kg'] + _QT_MEASURE_IDS, ['dosing.target_mass_kg'], {'target_mass_kg': 1e-5}, ['terminal'], ['上电且无运行操作；范围仅自由流动粉末。']),
        'dosing.set_tolerance': ({'tolerance_kg': _qt_num('kg')}, ['dosing.tolerance_kg'] + _QT_MEASURE_IDS, ['dosing.tolerance_kg'], {'tolerance_kg': 1e-6}, ['terminal'], ['上电且无运行操作；允许偏差由使用者显式设置。']),
        'head.select': ({'head_id': _QT_STR}, ['head.selected_id'], ['head.selected_id'], {'head_id': 'host_head'}, ['dosing_head', 'head_slot'], ['上电、空闲；head_id匹配宿主已装载的唯一头。']),
        'container.select_position': ({'position_id': _QT_STR}, ['container.position_id', 'balance.tared'] + _QT_MEASURE_IDS, ['container.position_id'], {'position_id': 'balance_pan'}, ['sample_container', 'balance_pan'], ['上电、空闲；position_id匹配宿主已放置容器的位置，无自动移动。']),
        'door.set_opening': ({'opening_m': _qt_num('m')}, ['scene.door_opening_m', 'balance.tared'] + _QT_MEASURE_IDS, ['scene.door_opening_m'], {'opening_m': 0}, ['draft_door'], ['空闲；有宿主配置的几何行程与场景回调；0<=q<=行程；回调返回True才提交。']),
        'balance.tare': ({}, ['balance.tared'] + _QT_MEASURE_IDS, ['balance.tared', 'measurement.balance_net_kg'], {}, ['balance'], ['上电、空闲、门关；容器位置已选；显式理想观察模型已配置。']),
        'dosing.start': ({}, ['dosing.status', 'dosing.elapsed_s', 'truth.actual_dosed_kg'] + _QT_MEASURE_IDS, _QT_READ_IDS, {}, ['dosing_head', 'balance', 'sample_container'], ['上电、门关、已去皮、目标与偏差已设置；已选宿主兼容自由流动粉末头和容器位置；显式速率与正库存。']),
        'dosing.read': ({}, [], _QT_READ_IDS, {}, ['terminal', 'balance'], []),
        'dosing.poll': ({'operation_id': _QT_STR}, [], _QT_READ_IDS, {'operation_id': 'qt_op_1'}, ['terminal'], ['操作属于当前身份与实例。']),
        'dosing.cancel': ({'operation_id': _QT_STR}, ['dosing.status'], _QT_READ_IDS, {'operation_id': 'qt_op_1'}, ['terminal', 'dosing_head'], ['操作属于当前身份与实例；取消保留已转移粉末与耗时。']),
        'autosampler.advance': ({}, [], [], {}, ['autosampler_option'], ['该批次单头配置未安装可选30位自动进样器。']),
    }
    desc = {'power.set': '设置仿真供电，断电取消任务并保留已投料。', 'dosing.set_target': '设置目标质量。', 'dosing.set_tolerance': '设置绝对允许偏差。', 'head.select': '选择宿主装入的粉末头。', 'container.select_position': '选择宿主已经放置容器的位置。', 'door.set_opening': '设置前门开度（门上沿铰销沿杠杆圆弧的行程，0为关）并等待宿主场景确认。', 'balance.tare': '记录显式理想读数模型的容器质量基线。', 'dosing.start': '启动有源库存守恒的异步仿真投料。', 'dosing.read': '读取投料结果且不推进时间。', 'dosing.poll': '读取当前身份拥有的投料进度。', 'dosing.cancel': '取消投料且保留部分转移。', 'autosampler.advance': '可选自动进样器未装配，不支持转位。'}
    caps = {}
    for cid, (props, effects, observed, example, components, prereq) in specs.items():
        scene, unsupported = cid == 'door.set_opening', cid == 'autosampler.advance'
        entries = [{'state_id': sid, 'timing': 'immediate', 'description_zh': '同步更新本次声明的状态。'} for sid in dict.fromkeys(effects)]
        if cid == 'dosing.start':
            entries += [{'state_id': sid, 'timing': 'on_step', 'description_zh': '宿主共同时间推进后更新守恒物料、理想观测和完成状态。'} for sid in ['truth.source_mass_kg', 'truth.container_mass_kg', 'truth.actual_dosed_kg', 'dosing.elapsed_s', 'dosing.status'] + _QT_MEASURE_IDS]
        caps[cid] = {'name_zh': desc[cid], 'description_zh': desc[cid], 'kind': 'query' if cid in ('dosing.read', 'dosing.poll') else 'configuration' if cid in ('head.select', 'container.select_position') else 'device_command', 'visibility': 'agent', 'availability': 'unsupported' if unsupported else 'implemented',
            'component_ids': components, 'input_schema': _qt_obj(_qt_copy.deepcopy(props)), 'output_schema': _qt_obj({sid: _qt_copy.deepcopy(slots[sid]['value_schema']) for sid in observed}), 'observes': observed,
            'state_effects': entries, 'preconditions_zh': prereq, 'example_arguments': [_qt_copy.deepcopy(example)],
            'binding': None if unsupported else 'basic40_runtime_automation.QuantosQ2Session.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
            'implementation': {'process': 'absent' if unsupported else 'implemented', 'scene': 'implemented' if scene else 'not_applicable', 'host': 'absent' if unsupported else 'implemented', 'hardware': 'absent', 'robot': 'absent'},
            'model_effects': {'mode': 'host_required' if scene else 'none', 'binding_ids': [], 'description_zh': 'scene_adapter(instance_id,{scene.door_opening_m:q})返回True后提交，apply_quantos_q2_scene_patch把前门杠杆转到q/0.198 rad，门板按mimic跟随。' if scene else '逻辑装载、称量和投料读数不驱动额外几何；粒子粉末流和屏幕同步未实现。'},
            'execution_notes': {'mode': 'asynchronous' if cid == 'dosing.start' else 'synchronous', 'time_domain': 'simulation', 'cancellable': cid == 'dosing.start', 'completion_zh': '理想观测达到目标即停止，缺料则failed保留部分转移。' if cid == 'dosing.start' else '返回succeeded表示本次操作完成。', 'partial_failure_zh': '取消、断电和缺料保留已转移的质量和已用时间；外部场景回调失败的副作用为unknown。', 'retry_zh': '当前内存中相同请求ID及参数返回原响应；不同参数拒绝；不保证跨重启。'},
            'errors': [{'code': c, 'description_zh': d, 'side_effects': s} for c, d, s in [('INVALID_ARGUMENT', '参数类型、范围或额外字段无效。', 'none'), ('UNKNOWN_INSTANCE', '实例不存在或不在身份范围。', 'none'), ('UNKNOWN_CAPABILITY', '能力不存在。', 'none'), ('ACCESS_DENIED', '槽不可写或身份不能访问。', 'none'), ('PRECONDITION_FAILED', '供电、装载、门、去皮、配置或库存条件不满足。', 'none'), ('BUSY', '投料占用设备。', 'none'), ('REQUEST_CONFLICT', '请求ID参数冲突。', 'none'), ('UNKNOWN_OPERATION', '操作不属于当前身份与实例。', 'none'), ('UNSUPPORTED', '能力未装配或门场景未绑定。', 'none'), ('DEVICE_ERROR', '场景回调失败或未确认。', 'unknown'), ('SOURCE_DEPLETED', '粉末源在达到目标前耗尽；保留部分投料。', 'partial')]]}
    return {'asset_type_id': QUANTOS_Q2_TYPE, 'asset_version': '1.0.2', 'catalog_id': 'CHEM-174', 'state_slots': slots, 'capabilities': caps,
        'parameters': {k: {'value': v, 'unit': u, 'evidence': e} for k,v,u,e in [('target_range_kg', [1e-6,.005], 'kg', 'catalog'), ('caco3_10mg_typical_time_s',22,'s','catalog'), ('caco3_10mg_rsd',.0115,'1','catalog'), ('caco3_50mg_rsd',.0026,'1','catalog'), ('caco3_1000mg_typical_time_s',71,'s','catalog'), ('optional_autosampler_positions',30,'1','catalog'), ('powder_rate_kg_s',None,'kg/s','unknown'), ('balance_readability_kg',None,'kg','unknown'), ('balance_capacity_kg',None,'kg','unknown'), ('door_manufacturer_travel_m',None,'m','unknown')]},
        'host_api': {k: {'entrypoint': 'QuantosQ2Runtime.'+k, 'description_zh': d} for k,d in {
            'create_instance':'独立实例，可选场景回调；所有未给出的物料和参数保持null。', 'register_principal':'可信宿主固定身份、权限与实例范围。', 'host_configure_geometry':'显式几何门行程和证据；关闭位置必须由回调确认。', 'host_load_head':'门开时装入唯一自由流动粉末头，源库存仅debug。', 'host_load_container':'门开时登记容器、位置和内容质量；不会移动几何。', 'host_configure_dosing':'显式正速率和理想质量观察证据；无厂家默认速率。', 'host_step':'dt_s与world_time_s必须匹配共同世界时钟，守恒转移并保存带供体/受体/量的账本。', 'host_attach':'外部attach事件与唯一约束ID确认，robot absent。', 'host_release':'外部release确认后清除约束，不移出容器。', 'host_unload_container':'门开且无附着无任务时移除容器引用，返回已保留内容质量给宿主。', 'host_unload_head':'门开且空闲时卸头，返回剩余粉末质量给宿主。', 'host_transfer_ledger':'仅可信宿主读取带事务ID的物质转移账本。'}.items()},
        'limitations': list(_QT_LIMITATIONS), 'output_schema_scope': 'capabilities.output_schema validates response.result; no process truth/evidence in agent response'}


# Doors (2026-10-23): the front door is lifted by a lever on each side and guided at the front top edge (brochure 30084648A p.10
# side view, manual p.25-26, p.67-68); the side doors slide back (+Y), travel at Doorway 100 % is a drawing estimate (p.10).
QUANTOS_FRONT_DOOR = {'lever_length_m': 0.198, 'lever_max_rad': 2.775073510670984, 'mimic': -0.51264, 'travel_m': 0.198 * 2.775073510670984,
                      'lever_object': 'quantos__front_door_lever', 'hinge_object': 'quantos__front_door_hinge'}
QUANTOS_SIDE_DOOR = {'travel_m': 0.16, 'objects': ('quantos__side_door_left_slide', 'quantos__side_door_right_slide')}


def _qt_scene_target(scene, name):
    o = scene.objects.get(name)
    if o is None:
        raise ValueError('Quantos door target missing: ' + name)
    if o.animation_data and (o.animation_data.action or o.animation_data.drivers):
        raise ValueError('Door must have a single host authority without animation/driver override')
    if len(o.constraints):
        raise ValueError('Door constraints require explicit authority reconciliation')
    return o


def apply_quantos_q2_scene_patch(scene, patch):
    """Blender scene bridge; caller must separately verify evaluated geometry.  scene.door_opening_m (m, path of the front door's top
    hinge pin along the lever arc): lever angle q / 0.198 m, the door hinge follows as the URDF mimic; door.side_opening_fraction (the
    process state mirrored by the host): both side doors slide fraction x 0.16 m."""
    keys = {'scene.door_opening_m', 'door.side_opening_fraction'}
    if not isinstance(patch, dict) or not patch or not set(patch) <= keys or not all(_qt_number(v) and v >= 0 for v in patch.values()):
        raise ValueError('Only a finite nonnegative door patch is accepted')
    if 'scene.door_opening_m' in patch:
        q = float(patch['scene.door_opening_m'])
        if q > QUANTOS_FRONT_DOOR['travel_m'] + 1e-9:
            raise ValueError('Front door opening beyond the geometric travel %.4f m' % QUANTOS_FRONT_DOOR['travel_m'])
        lever, hinge = (_qt_scene_target(scene, QUANTOS_FRONT_DOOR[k]) for k in ('lever_object', 'hinge_object'))
        beta = q / QUANTOS_FRONT_DOOR['lever_length_m']
        lever.rotation_euler.x = -beta                                       # joint axis -X: positive lever angle lifts the door
        hinge.rotation_euler.x = -QUANTOS_FRONT_DOOR['mimic'] * beta
    if 'door.side_opening_fraction' in patch:
        f = float(patch['door.side_opening_fraction'])
        if f > 1:
            raise ValueError('Side door fraction above 1')
        for name in QUANTOS_SIDE_DOOR['objects']:
            _qt_scene_target(scene, name).location.y = f * QUANTOS_SIDE_DOOR['travel_m']
    return True


def _qt_resolution(s):
    """Numerical resolution of the dosed mass (kg): relative 1e-12 of the target, at least a few ulp of the masses involved (2026-10-22)."""
    big = max(abs(s['dosing.target_mass_kg'] or 0.0), abs(s['truth.container_mass_kg'] or 0.0), abs(s['truth.source_mass_kg'] or 0.0),
              abs(s['truth.actual_dosed_kg'] or 0.0))
    return max(1e-12 * abs(s['dosing.target_mass_kg'] or 0.0), 8.0 * _qt_sys.float_info.epsilon * big)


class QuantosQ2Runtime:
    """Trusted host. Hand an authenticated QuantosQ2Session to the caller."""
    def __init__(self):
        self.time_s, self._counter = 0.0, 0
        self._instances, self._principals, self._requests, self._operations = {}, {}, {}, {}
        self._ledger, self._events, self._lock = [], [], _qt_threading.RLock()

    def create_instance(self, instance_id, scene_adapter=None):
        with self._lock:
            if not _qt_text(instance_id) or instance_id in self._instances or (scene_adapter is not None and not callable(scene_adapter)):
                raise ValueError('Unique ID and optional callable scene adapter required')
            s = {k: None for k in _QT_SLOTS}
            s.update({'power.enabled': False, 'balance.tared': False, 'dosing.elapsed_s': 0.0, 'dosing.status': 'idle', 'simulation.rate_configured': False})
            self._instances[instance_id] = {'state': s, 'revision': 0, 'adapter': scene_adapter, 'door_travel_m': None, 'rate_kg_s': None, 'position_id': None, 'tare_mass_kg': None, 'active_operation': None, 'evidence': {}}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not _qt_text(principal_id) or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Host-assigned unique identity and valid visibility required')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(i,str) or i not in self._instances for i in instance_ids)):
                raise ValueError('Invalid instance scope')
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))

    def session(self, principal_id):
        if not isinstance(principal_id,str) or principal_id not in self._principals:
            raise ValueError('Host authentication required')
        return QuantosQ2Session(self, principal_id)

    def _get(self, iid, principal=None):
        p = self._principals.get(principal) if principal else None
        return self._instances.get(iid) if isinstance(iid,str) and (not principal or (p and (p[1] is None or iid in p[1]))) else None

    def _idle(self, iid, door_open=False):
        i = self._get(iid)
        if i is None or i['active_operation'] is not None or (door_open and not (i['state']['scene.door_opening_m'] is not None and i['state']['scene.door_opening_m'] > 0)):
            raise ValueError('Known idle instance and required open door needed')
        return i

    @staticmethod
    def _refresh(i):
        s = i['state']
        available = s['power.enabled'] and s['balance.tared'] and s['simulation.rate_configured'] and s['container.loaded_id'] is not None
        s['measurement.balance_net_kg'] = max(0.0, s['truth.container_mass_kg'] - i['tare_mass_kg']) if available else None
        actual = s['truth.actual_dosed_kg'] if available else None
        s['measurement.actual_dosed_kg'] = actual
        s['measurement.error_kg'] = actual - s['dosing.target_mass_kg'] if actual is not None and s['dosing.target_mass_kg'] is not None else None
        s['measurement.out_of_tolerance'] = abs(s['measurement.error_kg']) > s['dosing.tolerance_kg'] if s['measurement.error_kg'] is not None and s['dosing.tolerance_kg'] is not None else None

    def host_configure_geometry(self, iid, *, door_travel_m, evidence):
        with self._lock:
            i = self._idle(iid)
            if not _qt_number(door_travel_m) or door_travel_m <= 0 or not _qt_text(evidence) or i['adapter'] is None or i['state']['head.loaded_id'] or i['state']['container.loaded_id']:
                raise ValueError('Empty idle geometry, scene adapter, positive explicit travel and evidence required')
            if i['adapter'](iid, {'scene.door_opening_m': 0.0}) is not True:
                raise RuntimeError('External scene did not confirm initialization; side effects unknown')
            i['door_travel_m'], i['evidence']['geometry'] = float(door_travel_m), evidence
            i['state']['scene.door_opening_m'] = 0.0
            i['revision'] += 1

    def host_load_head(self, iid, *, head_id, source_mass_kg, free_flowing, evidence):
        with self._lock:
            i = self._idle(iid, True)
            if not _qt_text(head_id) or not _qt_text(evidence) or not _qt_number(source_mass_kg) or source_mass_kg < 0 or free_flowing is not True or i['state']['head.loaded_id'] is not None or any(x['state']['head.loaded_id'] == head_id for x in self._instances.values()):
                raise ValueError('Unique compatible free-flowing head, explicit mass and evidence required')
            i['state'].update({'head.loaded_id': head_id, 'truth.source_mass_kg': float(source_mass_kg)})
            i['evidence']['head'] = evidence
            i['revision'] += 1

    def host_load_container(self, iid, *, container_id, position_id, content_mass_kg, evidence):
        with self._lock:
            i = self._idle(iid, True)
            if not all(_qt_text(v) for v in (container_id, position_id, evidence)) or not _qt_number(content_mass_kg) or content_mass_kg < 0 or i['state']['container.loaded_id'] is not None or any(x['state']['container.loaded_id'] == container_id for x in self._instances.values()):
                raise ValueError('Unique container, position, explicit nonnegative content mass and placement evidence required')
            i['state'].update({'container.loaded_id': container_id, 'truth.container_mass_kg': float(content_mass_kg), 'balance.tared': False, 'truth.actual_dosed_kg': None, 'dosing.elapsed_s': 0.0, 'dosing.status': 'idle'})
            i['position_id'], i['evidence']['container'] = position_id, evidence
            self._refresh(i)
            i['revision'] += 1

    def host_configure_dosing(self, iid, *, rate_kg_s, rate_evidence, observation_evidence):
        with self._lock:
            i = self._idle(iid)
            if not _qt_number(rate_kg_s) or rate_kg_s <= 0 or not _qt_text(rate_evidence) or not _qt_text(observation_evidence):
                raise ValueError('Explicit positive rate and ideal-observation assumption evidence required')
            i['rate_kg_s'] = float(rate_kg_s)
            i['evidence'].update({'rate': rate_evidence, 'observation': observation_evidence})
            i['state']['simulation.rate_configured'] = True
            self._refresh(i)
            i['revision'] += 1

    def host_unload_container(self, iid):
        with self._lock:
            i = self._idle(iid, True)
            s = i['state']
            if s['container.loaded_id'] is None or s['container.attachment_constraint_id'] is not None:
                raise ValueError('Loaded unattached container required')
            result = {'container_id': s['container.loaded_id'], 'content_mass_kg': s['truth.container_mass_kg']}
            s.update({'container.loaded_id': None, 'container.position_id': None, 'truth.container_mass_kg': None, 'balance.tared': False})
            i['position_id'], i['tare_mass_kg'] = None, None
            self._refresh(i)
            i['revision'] += 1
            return result

    def host_unload_head(self, iid):
        with self._lock:
            i = self._idle(iid, True)
            s = i['state']
            if s['head.loaded_id'] is None:
                raise ValueError('Loaded head required')
            result = {'head_id': s['head.loaded_id'], 'source_mass_kg': s['truth.source_mass_kg']}
            s.update({'head.loaded_id': None, 'head.selected_id': None, 'truth.source_mass_kg': None})
            i['revision'] += 1
            return result

    def _attachment(self, iid, kind, constraint_id, adapter):
        i = self._idle(iid, True)
        s = i['state']
        if s['container.loaded_id'] is None or not callable(adapter) or (kind == 'attach' and (not _qt_text(constraint_id) or s['container.attachment_constraint_id'] is not None or any(x['state']['container.attachment_constraint_id'] == constraint_id for x in self._instances.values()))) or (kind == 'release' and s['container.attachment_constraint_id'] is None):
            raise ValueError('Loaded container, external adapter and valid unique attachment state required')
        event = {'event': kind, 'instance_id': iid, 'container_id': s['container.loaded_id'], 'constraint_id': constraint_id if kind == 'attach' else s['container.attachment_constraint_id'], 'time_s': self.time_s, 'robot': 'absent'}
        if adapter(iid, _qt_copy.deepcopy(event)) is not True:
            raise RuntimeError('External attachment not confirmed; side effects unknown')
        s['container.attachment_constraint_id'] = constraint_id if kind == 'attach' else None
        i['revision'] += 1
        self._events.append(_qt_copy.deepcopy(event))
        return event

    def host_attach(self, iid, *, constraint_id, attachment_adapter):
        with self._lock:
            return self._attachment(iid, 'attach', constraint_id, attachment_adapter)

    def host_release(self, iid, *, attachment_adapter):
        with self._lock:
            return self._attachment(iid, 'release', None, attachment_adapter)

    @staticmethod
    def _result(i):
        return {k: _qt_copy.deepcopy(i['state'][k]) for k in _QT_READ_IDS}

    def _stop(self, i, status, code=None):
        opid = i['active_operation']
        i['state']['dosing.status'] = status
        i['active_operation'] = None
        if opid:
            op = self._operations[opid]
            op['status'], op['result'] = status, self._result(i)
            if code is not None:
                op['error'] = {'code':code,'message':'Source exhausted before reaching the requested target; transferred powder remains in the receiver.','retryable':False,'side_effects':'partial','progress':self._result(i)}

    def host_step(self, dt_s, *, world_time_s):
        with self._lock:
            if not _qt_number(dt_s) or dt_s <= 0 or not _qt_number(world_time_s) or not _qt_number(self.time_s + dt_s) or not _qt_math.isclose(world_time_s, self.time_s + dt_s, rel_tol=0, abs_tol=1e-9):
                raise ValueError('Finite positive step must match explicit common world time')
            updates = []
            for iid,i in self._instances.items():
                if i['active_operation'] is None:
                    continue
                s, rate = i['state'], i['rate_kg_s']
                # 2026-10-22 (1.2.1): the dose sum of many steps can stop a few ulp short of the target (20 mg at 5e-7 kg/s in 1 s steps ends
                # 1e-20 kg short); that residue is below the resolution of the masses.  The target counts as reached within the numerical
                # resolution res of the dose (relative 1e-12 of the target, or a few ulp of the masses involved), and a step whose transfer is
                # below the resolution moves nothing but still advances the clock (it used to raise and drop the clock step).
                res = _qt_resolution(s)
                remaining = s['dosing.target_mass_kg'] - s['truth.actual_dosed_kg']
                remaining = 0.0 if remaining <= res else remaining
                amount = min(remaining, s['truth.source_mass_kg'], rate * dt_s)
                if amount > 0 and (s['truth.container_mass_kg'] + amount == s['truth.container_mass_kg'] or s['truth.source_mass_kg'] - amount == s['truth.source_mass_kg']):
                    amount = 0.0                                                    # below the resolution of the masses: nothing moves this step
                amount = max(0.0, amount)
                elapsed = min(dt_s, amount / rate)
                if not all(_qt_number(v) for v in [amount, elapsed, s['truth.container_mass_kg'] + amount, s['dosing.elapsed_s'] + elapsed]):
                    raise ValueError('Transfer would overflow; no instance advanced')
                updates.append((iid, i, amount, elapsed))
            for iid,i,amount,elapsed in updates:
                s, opid = i['state'], i['active_operation']
                s['truth.source_mass_kg'] -= amount
                s['truth.container_mass_kg'] += amount
                s['truth.actual_dosed_kg'] += amount
                s['dosing.elapsed_s'] += elapsed
                self._refresh(i)
                if amount > 0:
                    self._ledger.append({'transaction_id': '%s_transfer_%d' % (opid,len(self._ledger)+1), 'operation_id': opid, 'instance_id': iid, 'donor_id': s['head.loaded_id'], 'receiver_id': s['container.loaded_id'], 'mass_kg': amount, 'unit': 'kg', 'time_s': self.time_s + elapsed})
                res = _qt_resolution(s)
                if s['truth.actual_dosed_kg'] >= s['dosing.target_mass_kg'] - res:
                    self._stop(i, 'succeeded')
                elif s['truth.source_mass_kg'] <= res:                             # what is left in the head is below the resolution: empty
                    self._stop(i, 'failed', 'SOURCE_DEPLETED')
                self._operations[opid]['result'] = self._result(i)
                i['revision'] += 1
            self.time_s = float(world_time_s)

    def host_transfer_ledger(self):
        with self._lock:
            return _qt_copy.deepcopy(self._ledger)

    def debug_snapshot(self):
        with self._lock:
            return _qt_copy.deepcopy({'time_s': self.time_s, 'instances': {iid:{k:v for k,v in i.items() if k != 'adapter'} for iid,i in self._instances.items()}, 'operations': self._operations, 'ledger': self._ledger, 'events': self._events})


class QuantosQ2Session:
    def __init__(self, host, principal_id):
        self.__host, self.__principal = host, principal_id

    def _get(self, iid):
        return self.__host._get(iid, self.__principal)

    def _visible(self, sid):
        return isinstance(sid,str) and sid in _QT_SLOTS and (_QT_SLOTS[sid]['visibility'] == 'agent' or self.__host._principals[self.__principal][0] in ('debug','evaluator'))

    def _response(self, iid, rid=None, status='succeeded', result=None, observations=None, operation_id=None, error=None):
        i = self._get(iid)
        return {'protocol_version':'1.0.0','instance_id':iid if isinstance(iid,str) else None,'request_id':rid if isinstance(rid,str) else None,'status':status,'operation_id':operation_id,'state_revision':i['revision'] if i else None,'clock':{'domain':'simulation','time_s':self.__host.time_s},'result':_qt_copy.deepcopy(result or {}),'observations':_qt_copy.deepcopy(observations or {}),'error':error}

    def _error(self, iid, code, rid=None, status='rejected', side='none'):
        return self._response(iid,rid,status,error={'code':code,'message':'Request does not satisfy declared access, input or operating conditions.','retryable':False,'side_effects':side,'progress':None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or (asset_type_id is not None and asset_type_id != QUANTOS_Q2_TYPE):
                return self._error(instance_id,'INVALID_ARGUMENT')
            i = self._get(instance_id) if instance_id is not None else None
            if instance_id is not None and i is None:
                return self._error(instance_id,'UNKNOWN_INSTANCE')
            if self.__host._principals[self.__principal][1] == frozenset():
                return self._error(instance_id,'ACCESS_DENIED')
            data = export_quantos_q2_contract_metadata()
            data.pop('host_api')
            data['state_slots'] = {sid:v for sid,v in data['state_slots'].items() if self._visible(sid)}
            for cap in data['capabilities'].values():
                cap['state_effects'] = [e for e in cap['state_effects'] if self._visible(e['state_id'])]
            if i is not None and i['adapter'] is None:
                data['capabilities']['door.set_opening']['availability'] = 'unsupported'
            data.update({'protocol_version':'1.0.0','instance_id':instance_id,'adapter':'basic40_runtime_automation.QuantosQ2Session'})
            return data

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            i = self._get(instance_id)
            if i is None:
                return self._error(instance_id,'UNKNOWN_INSTANCE')
            if state_ids is None:
                state_ids = [sid for sid in _QT_SLOTS if self._visible(sid)]
            if not isinstance(state_ids,list) or any(not isinstance(s,str) for s in state_ids) or len(set(state_ids)) != len(state_ids):
                return self._error(instance_id,'INVALID_ARGUMENT')
            if any(not self._visible(sid) for sid in state_ids):
                return self._error(instance_id,'ACCESS_DENIED')
            obs = {}
            for sid in state_ids:
                v = i['state'][sid]
                obs[sid] = {'value':_qt_copy.deepcopy(v),'unit':_QT_SLOTS[sid]['unit'],'quality':'unavailable' if v is None else 'estimated' if sid.startswith('measurement.') or sid == 'scene.door_opening_m' else 'valid','sample_time_s':self.__host.time_s,'sample_window_s':None}
                if v is None:
                    obs[sid]['reason'] = 'Explicit configuration, placement, balance tare or current powered observation is unavailable.'
            return self._response(instance_id,observations=obs)

    def _cached(self, iid, rid, logical, callback):
        if not _qt_text(rid) or len(rid)>256:
            return self._error(iid,'INVALID_ARGUMENT',rid)
        try:
            digest = _qt_json.dumps(logical, sort_keys=True, allow_nan=False, separators=(',',':'))
        except (TypeError, ValueError, OverflowError, RecursionError):
            return self._error(iid,'INVALID_ARGUMENT',rid)
        key = (self.__principal,iid,rid)
        if key in self.__host._requests:
            prev, reply = self.__host._requests[key]
            return _qt_copy.deepcopy(reply) if prev == digest else self._error(iid,'REQUEST_CONFLICT',rid)
        response = callback()
        self.__host._requests[key] = (digest,_qt_copy.deepcopy(response))
        return response

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        with self.__host._lock:
            i = self._get(instance_id)
            if i is None:
                return self._error(instance_id,'UNKNOWN_INSTANCE',request_id)
            if not isinstance(capability_id,str) or not isinstance(arguments,dict):
                return self._error(instance_id,'INVALID_ARGUMENT',request_id)
            return self._cached(instance_id,request_id,['invoke',capability_id,arguments],lambda:self._execute(instance_id,i,capability_id,arguments,request_id))

    def _execute(self, iid, i, cid, args, rid):
        caps = export_quantos_q2_contract_metadata()['capabilities']
        fail = lambda code:self._error(iid,code,rid)
        if cid not in caps:
            return fail('UNKNOWN_CAPABILITY')
        cap = caps[cid]
        props = cap['input_schema']['properties']
        if set(args) != set(props):
            return fail('INVALID_ARGUMENT')
        for k,spec in props.items():
            v = args[k]
            if (spec['type']=='number' and (not _qt_number(v) or ('minimum' in spec and v<spec['minimum']) or ('maximum' in spec and v>spec['maximum']))) or (spec['type']=='boolean' and type(v) is not bool) or (spec['type']=='string' and not _qt_text(v)):
                return fail('INVALID_ARGUMENT')
        if cid == 'autosampler.advance':
            return self._error(iid,'UNSUPPORTED',rid,'unsupported')
        if cid == 'dosing.read':
            return self._response(iid,rid,result=self.__host._result(i))
        if cid in ('dosing.poll','dosing.cancel'):
            op = self.__host._operations.get(args['operation_id'])
            if op is None or op['instance_id'] != iid or op['principal'] != self.__principal:
                return fail('UNKNOWN_OPERATION')
            if cid == 'dosing.cancel' and op['status'] == 'running':
                self.__host._stop(i,'cancelled')
                i['revision'] += 1
            return self._response(iid,rid,status=op['status'],operation_id=args['operation_id'],result=op['result'],error=op.get('error'))
        s = i['state']
        if i['active_operation'] is not None and not (cid == 'power.set' and args['enabled'] is False):
            return fail('BUSY')
        if cid not in ('power.set','door.set_opening') and not s['power.enabled']:
            return fail('PRECONDITION_FAILED')
        if cid == 'power.set':
            s['power.enabled'] = args['enabled']
            if not args['enabled']:
                if i['active_operation'] is not None:
                    self.__host._stop(i,'cancelled')
                s['balance.tared'] = False
        elif cid == 'dosing.set_target':
            s['dosing.target_mass_kg'] = float(args['target_mass_kg'])
        elif cid == 'dosing.set_tolerance':
            s['dosing.tolerance_kg'] = float(args['tolerance_kg'])
        elif cid == 'head.select':
            if args['head_id'] != s['head.loaded_id']:
                return fail('PRECONDITION_FAILED')
            s['head.selected_id'] = args['head_id']
        elif cid == 'container.select_position':
            if s['container.loaded_id'] is None or args['position_id'] != i['position_id']:
                return fail('PRECONDITION_FAILED')
            s['container.position_id'], s['balance.tared'] = args['position_id'], False
        elif cid == 'door.set_opening':
            if i['adapter'] is None:
                return self._error(iid,'UNSUPPORTED',rid,'unsupported')
            if i['door_travel_m'] is None:
                return fail('PRECONDITION_FAILED')
            if args['opening_m'] > i['door_travel_m']:
                return fail('INVALID_ARGUMENT')
            try:
                confirmed = i['adapter'](iid, {'scene.door_opening_m':float(args['opening_m'])}) is True
            except Exception:
                confirmed = False
            if not confirmed:
                return self._error(iid,'DEVICE_ERROR',rid,'failed','unknown')
            s['scene.door_opening_m'] = float(args['opening_m'])
            if args['opening_m'] > 0:
                s['balance.tared'] = False
        elif cid == 'balance.tare':
            if s['scene.door_opening_m'] != 0 or s['container.loaded_id'] is None or s['container.position_id'] != i['position_id'] or not s['simulation.rate_configured']:
                return fail('PRECONDITION_FAILED')
            i['tare_mass_kg'], s['balance.tared'] = s['truth.container_mass_kg'], True
        elif cid == 'dosing.start':
            if not s['balance.tared'] or s['scene.door_opening_m'] != 0 or s['dosing.target_mass_kg'] is None or s['dosing.tolerance_kg'] is None or s['head.loaded_id'] is None or s['head.selected_id'] != s['head.loaded_id'] or s['container.loaded_id'] is None or s['container.position_id'] != i['position_id'] or i['rate_kg_s'] is None or s['truth.source_mass_kg'] <= 0 or s['container.attachment_constraint_id'] is not None:
                return fail('PRECONDITION_FAILED')
            # A new cycle requires a tare at the current container mass; it cannot silently reuse a previous dose baseline.
            if i['tare_mass_kg'] != s['truth.container_mass_kg']:
                return fail('PRECONDITION_FAILED')
            s.update({'dosing.status':'running','dosing.elapsed_s':0.0,'truth.actual_dosed_kg':0.0})
            self.__host._counter += 1
            opid = 'qt_op_%d' % self.__host._counter
            i['active_operation'] = opid
            self.__host._refresh(i)
            op = {'instance_id':iid,'principal':self.__principal,'status':'running','result':self.__host._result(i)}
            self.__host._operations[opid] = op
            i['revision'] += 1
            return self._response(iid,rid,'accepted',result=op['result'],operation_id=opid)
        self.__host._refresh(i)
        i['revision'] += 1
        return self._response(iid,rid,result={sid:s[sid] for sid in cap['observes']})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._get(instance_id) is None:
                return self._error(instance_id,'UNKNOWN_INSTANCE',request_id)
            if not self._visible(state_id) or _QT_SLOTS[state_id]['write_via'] is None:
                return self._error(instance_id,'ACCESS_DENIED',request_id)
            via = _QT_SLOTS[state_id]['write_via']
            return self.invoke(instance_id=instance_id,capability_id=via['capability_id'],arguments={via['parameter']:value},request_id=request_id)

    def poll(self, *, instance_id, operation_id):
        with self.__host._lock:
            i = self._get(instance_id)
            if i is None:
                return self._error(instance_id,'UNKNOWN_INSTANCE')
            return self._execute(instance_id,i,'dosing.poll',{'operation_id':operation_id},None)

    def cancel(self, *, instance_id, operation_id, request_id):
        return self.invoke(instance_id=instance_id,capability_id='dosing.cancel',arguments={'operation_id':operation_id},request_id=request_id)
# END CHEM-174 QUANTOS Q2 XPE206DR APPEND-ONLY RUNTIME

# CHEM-176 state IDs are the contract IDs (lower case _k / _k_s).  Until 1.2.1 the runtime kept SI-suffix labels (thermal.setpoint_K, ...)
# and a wrapper here lower-cased describe() and mapped explicit reads/writes, while read() without state_ids still returned the old labels;
# 1.2.2 (2026-10-22) renamed the slots themselves, so describe, read, write and the contract use one set of IDs and the wrapper is gone.

# BEGIN CHEM-176 AZENTA HEAT SEALER FRONT PANEL
# Front panel (panel.touch / panel.switch / panel.capture / panel.frame): the colour touch screen and the rear power switch of the user manual
# (347598 Rev. E; panel_ui.json beside the contract), run by the generic engine scripts/asset_runtime/_panel_ui.py on the capabilities below (no
# second copy of the logic).  Device functions behind the screen (all reachable through the API as well): seal time (sealing.set_time), the
# front-panel seal cycle (sealing.seal_plate: carrier in, press down, dwell, press up, carrier out), the seal count, power saving (idle time,
# hold temperature, wake), protocols (nine factory presets, new / edit / delete / select, security levels with passwords, Administrator page),
# the plate carrier (close door with a cut seal, reopen), roll change and the seal loading procedure, inspection-door checks.
import pathlib as _ahs_pathlib
import tempfile as _ahs_tempfile
import hashlib as _ahs_hashlib

AZENTA_HEAT_SEALER_ASSET_DIR = _ahs_pathlib.Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t5_automation/azenta_heat_sealer'
AZENTA_HEAT_SEALER_PRESETS = (('4ti-0520', 175, 3.0, 'Peel Seal'), ('4ti-0530', 175, 3.0, 'Pierce Seal'), ('4ti-0535', 175, 3.0, 'Foil Seal'),
                              ('4ti-0540', 175, 3.0, 'Clear Seal'), ('4ti-0573', 170, 3.0, 'Clear Weld Seal Mark II'), ('4ti-0580', 170, 3.0, 'Clear Seal 3730'),
                              ('4ti-0585', 175, 3.0, 'DMSO Resistant Peel Seal'), ('4ti-0590', 175, 3.0, 'Thermal Bond'),
                              ('4ti-0598', 175, 3.0, 'Gas Permeable Heat Seal MK2'))           # manual p.41, no password protection
AZENTA_HEAT_SEALER_ADMIN_PASSWORD = '8888'                                        # Administrator page default (manual p.41)
AZENTA_HEAT_SEALER_ROOM_K = 293.15            # power saving 'heater off': the block cools toward an assumed 20 C room (not stated)
AZENTA_HEAT_SEALER_ERRORS = {                  # manual p.64 Error Messages (no Error 8); action for all: contact the service representative
    1: 'Heater temperature rises over 210°C in any condition.', 2: "You touch Seal but heater can't move up",
    3: 'You touch Seal or the Plate Carrier Movement icon but the plate carrier does not close.',
    4: 'You touch Seal or the Plate Carrier Movement icon but the plate carrier does not open.', 5: 'Temperature detect abnormal.',
    6: 'Temperature does not reach the set value.', 7: 'Temperature inaccuracy.', 9: 'Over-current occurred in the plate motor driver.',
    10: 'Over-current occurred in the heater motor driver.', 11: "No detection at heating block's home sensor.", 12: "ADC value doesn't update over 10 sec."}
_AHS_SECURITY = {'type': 'string', 'enum': ['none', 'low', 'high']}
_AHS_NAME = {'type': 'string', 'minLength': 1, 'maxLength': 16}
_AHS_PASSWORD = {'type': 'string', 'maxLength': 4}
_AHS_PROTOCOL = _ahs_obj({'name': _AHS_NAME, 'temperature_K': _AHS_TEMP, 'press_time_s': _AHS_DURATION, 'information': {'type': 'string', 'maxLength': 32},
                          'security': _AHS_SECURITY})
_AHS_PANEL_SLOTS = {
    'device.seal_count': _ahs_slot(_ahs_num('1', 0, None, True), '1', 'derived'),
    'protocol.list': _ahs_slot({'type': 'array', 'items': _AHS_PROTOCOL}, '1', 'setpoint'),
    'protocol.current': _ahs_slot(_ahs_nullable(_AHS_STR), '1', 'setpoint'),
    'protocol.unlocked': _ahs_slot(_AHS_BOOL, '1', 'derived'),
    'power_saving.enabled': _ahs_slot(_AHS_BOOL, '1', 'setpoint'),
    'power_saving.idle_time_s': _ahs_slot(_ahs_num('s', 1800, 43200), 's', 'setpoint'),
    'power_saving.hold_temperature_k': _ahs_slot(_ahs_nullable({'type': 'number', 'enum': [323.15, 373.15], 'x-unit': 'K'}), 'K', 'setpoint'),
    'power_saving.active': _ahs_slot(_AHS_BOOL, '1', 'derived'),
    'carrier.position': _ahs_slot({'type': 'string', 'enum': ['out', 'in', 'loading']}, '1', 'derived'),
    'carrier.cut_seal_present': _ahs_slot(_AHS_BOOL, '1', 'derived'),
    'door.inspection_closed': _ahs_slot(_AHS_BOOL, '1', 'scene'),
    'film.released': _ahs_slot(_AHS_BOOL, '1', 'derived'),
    'device.error_code': _ahs_slot(_ahs_nullable({'type': 'integer', 'enum': sorted(AZENTA_HEAT_SEALER_ERRORS), 'x-unit': '1'}), '1', 'derived'),
}
_AHS_SLOTS.update(_AHS_PANEL_SLOTS)
_AHS_PANEL_DEFAULTS = {'device.seal_count': 0, 'protocol.current': None, 'protocol.unlocked': False, 'power_saving.enabled': True,
                       'power_saving.idle_time_s': 3600.0, 'power_saving.hold_temperature_k': 373.15, 'power_saving.active': False,
                       'carrier.position': 'out', 'carrier.cut_seal_present': False, 'door.inspection_closed': True, 'film.released': False,
                       'device.error_code': None}
_AHS_PANEL_CAPS = {   # id: (input properties, output slots, example, effects, kind, component ids, meaning, preconditions)
    'sealing.set_time': ({'press_time_s': _AHS_DURATION}, ['configuration.press_time_s'], {'press_time_s': 3.0}, ['configuration.press_time_s'], 'configuration',
                         ['status_display'], '设定封膜时间（主屏时间图标，0.1–10 s）；不需要板或膜。', ['已上电；无正在封膜操作；当前协议为红锁时须先protocol.unlock（面板规则）']),
    'sealing.seal_plate': ({}, ['sealing.status', 'film.remaining_seals'], {}, ['sealing.status', 'sealing.elapsed_s', 'sealing.progress', 'plate.simulated_sealed',
                           'film.remaining_seals', 'configuration.plate_adapter_id', 'configuration.film_roll_position_id', 'scene.press_displacement_m', 'carrier.position'],
                           'device_command', ['status_display', 'press_head', 'plate_carriage'],
                           '前面板Seal：载板收入、压头压下、保持封膜时间、压头抬起、载板送出；采用宿主已装的板与膜，消耗1次膜配额；accepted不表示完成。',
                           ['已上电；载板在外；已装未封的板与未释放且有余量的膜；温度到达设定（Seal图标绿色）；封膜时间已设；侧检修门关闭；有场景回调时须确认压头位置']),
    'power_saving.configure': ({'enabled': _AHS_BOOL, 'idle_time_s': _ahs_num('s', 1800, 43200), 'hold_temperature_K': _ahs_nullable({'type': 'number', 'enum': [323.15, 373.15], 'x-unit': 'K'})},
                               ['power_saving.enabled', 'power_saving.idle_time_s', 'power_saving.hold_temperature_k'], {'enabled': True, 'idle_time_s': 3600.0, 'hold_temperature_K': 373.15},
                               ['power_saving.enabled', 'power_saving.idle_time_s', 'power_saving.hold_temperature_k', 'power_saving.active'], 'configuration', ['status_display'],
                               '节能：开关、空闲时间0.5–12 h（0.5 h步长）、节能时加热块温度（关加热/50 °C/100 °C）。', ['已上电；空闲时间为0.5 h的整数倍']),
    'power_saving.wake': ({}, ['power_saving.active'], {}, ['power_saving.active'], 'device_command', ['status_display'],
                          '结束节能、重新计空闲时间（触摸屏幕）。', ['已上电']),
    'carrier.close_door': ({}, ['carrier.position', 'carrier.cut_seal_present'], {}, ['carrier.position', 'carrier.cut_seal_present', 'film.remaining_seals'],
                           'device_command', ['plate_carriage', 'cutter'], '关门：载板收入、门关闭，切下一片膜落在载板上（消耗1次膜配额）。',
                           ['已上电；无封膜任务；载板在外；载板上没有板/适配器']),
    'carrier.open': ({}, ['carrier.position', 'carrier.cut_seal_present'], {}, ['carrier.position', 'carrier.cut_seal_present'], 'device_command', ['plate_carriage'],
                     '开门：载板送出；用户确认已取走切下的膜。', ['已上电；载板在内']),
    'film.release': ({}, ['film.released'], {}, ['film.released'], 'device_command', ['feed_rollers', 'film_path'],
                     'Chg roll：松开膜以便取下膜卷（之后宿主取走膜卷，重启后装新卷）。', ['已上电；无封膜任务；已装膜且未释放']),
    'film.loading_step': ({'step': {'type': 'string', 'enum': ['to_loading_position', 'cut', 'finish']}}, ['carrier.position'], {'step': 'to_loading_position'},
                          ['carrier.position', 'film.released'], 'device_command', ['plate_carriage', 'cutter', 'feed_rollers'],
                          '装膜流程的仪器动作：载板到装膜位置；用户穿好膜后切除多余膜（载板送出）；完成。',
                          ['已上电；to_loading_position：载板上无板；cut：宿主已装膜且载板在装膜位置；finish：已切膜']),
    'protocol.save': ({'original_name': _ahs_nullable(_AHS_NAME), 'name': _AHS_NAME, 'temperature_K': _AHS_TEMP, 'press_time_s': _AHS_DURATION,
                       'information': {'type': 'string', 'maxLength': 32}, 'security': _AHS_SECURITY, 'password': _AHS_PASSWORD, 'current_password': _AHS_PASSWORD},
                      ['protocol.list'], {'original_name': None, 'name': 'MyPlate', 'temperature_K': 443.15, 'press_time_s': 2.5, 'information': 'Peel Seal on PCR',
                                          'security': 'none', 'password': '', 'current_password': ''},
                      ['protocol.list', 'protocol.current'], 'configuration', ['status_display'],
                      '新建（original_name为null）或修改协议：名称、温度、时间、信息、安全级别与密码（≤4位）；修改有锁协议须给当前密码。',
                      ['已上电；名称非空且新名称不重复；温度100–195 °C；时间0.1–10 s；low/high须有1–4位密码']),
    'protocol.select': ({'name': _AHS_NAME, 'password': _AHS_PASSWORD}, ['protocol.current', 'thermal.setpoint_k', 'configuration.press_time_s'], {'name': '4ti-0520', 'password': ''},
                        ['protocol.current', 'protocol.unlocked', 'thermal.setpoint_k', 'configuration.press_time_s', 'thermal.target_reached'], 'configuration', ['status_display'],
                        '选用协议：其温度与时间载入主屏；红锁协议须给密码。', ['已上电；无封膜任务；协议存在；红锁协议密码正确']),
    'protocol.delete': ({'name': _AHS_NAME, 'password': _AHS_PASSWORD}, ['protocol.list'], {'name': 'MyPlate', 'password': ''}, ['protocol.list', 'protocol.current'],
                        'configuration', ['status_display'], '删除协议；有锁协议须给其密码或Administrator密码。', ['已上电；协议存在；密码正确']),
    'protocol.unlock': ({'password': _AHS_PASSWORD}, ['protocol.unlocked'], {'password': '1234'}, ['protocol.unlocked'], 'device_command', ['status_display'],
                        '红锁协议运行中输入密码，允许改参数、换协议或离开主屏（到下次选用协议或断电）。', ['已上电；当前协议为红锁；密码正确']),
    'protocol.check_password': ({'name': _AHS_NAME, 'password': _AHS_PASSWORD}, ['protocol.current'], {'name': '4ti-0520', 'password': ''}, [], 'query', ['status_display'],
                                '检查有锁协议的密码（编辑前的密码输入），不改变状态。', ['已上电；协议存在；无锁协议任何输入都通过；有锁协议须为其密码或Administrator密码']),
    'protocol.admin_set_password': ({'admin_password': _AHS_PASSWORD, 'name': _ahs_nullable(_AHS_NAME), 'new_password': {'type': 'string', 'minLength': 1, 'maxLength': 4}},
                                    ['protocol.list'], {'admin_password': '8888', 'name': None, 'new_password': '4321'}, [], 'configuration', ['status_display'],
                                    'Administrator：修改某协议的密码（name）或Administrator密码（name为null）。', ['已上电；Administrator密码正确；协议存在且有锁']),
}
_AHS_PANEL = {}
_AHS_FAULT_CAPS = {   # evaluator only: id: (input properties, output slots, example, effects, meaning)
    'fault.inject': ({'code': {'type': 'integer', 'enum': sorted(AZENTA_HEAT_SEALER_ERRORS), 'x-unit': '1'}}, ['device.error_code', 'sealing.status'], {'code': 3},
                     ['device.error_code', 'sealing.status', 'sealing.progress'],
                     '评测者：使仪器进入手册p.64的错误状态Error N（加热、压头、载板、温度检测、电机驱动或ADC故障）：正在进行的封膜立即停止（failed，压头与载板停在原处），'
                     '屏幕显示Error N及手册的说明与“联系服务代表”，封膜与压头/载板动作被拒（PRECONDITION_FAILED），断电重启后仍在；用于测试视觉agent对故障画面的处理。'),
    'fault.clear': ({}, ['device.error_code'], {}, ['device.error_code'],
                    '评测者：服务代表排除故障并按集成手册357454的SR（System Reset）复位错误报警；屏幕回到主屏。'),
}
_AHS_FAULT_BLOCKED = ('sealing.start', 'sealing.seal_plate', 'scene.set_press_displacement', 'carrier.close_door', 'carrier.open', 'film.release', 'film.loading_step')


def _ahs_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _AHS_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if '_chemsim_panel_ui' not in sys.modules:
                spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', _ahs_pathlib.Path(__file__).resolve().parent / 'asset_runtime/_panel_ui.py')
                sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
            engine = sys.modules['_chemsim_panel_ui']
        caps = set(_ahs_export_metadata_v2(panel=False)['capabilities'])
        spec = engine.load_spec(AZENTA_HEAT_SEALER_ASSET_DIR / 'panel_ui.json', capabilities=caps)
        _AHS_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _AHS_PANEL


_ahs_export_metadata_v1b = export_azenta_heat_sealer_contract_metadata


def _ahs_export_metadata_v2(panel=True):
    m = _ahs_export_metadata_v1b()
    m['asset_version'] = '1.2.2'   # 1.2.2: slot ids = contract ids (lower case, 2026-10-22)
    errors = m['capabilities']['power.set']['errors'] + [{'code': 'ACCESS_DENIED', 'description_zh': '协议密码或Administrator密码不正确；或另一会话的封膜进行中'
                                                          '（改温度/时间/协议被拒）。', 'side_effects': 'none'}]
    for cid, (props, outs, example, effects, kind, comps, desc, pre) in _AHS_PANEL_CAPS.items():
        entries = [{'state_id': k, 'timing': 'immediate', 'description_zh': '本调用成功后的明确状态更新。'} for k in effects]
        if cid == 'sealing.seal_plate':
            entries += [{'state_id': k, 'timing': 'on_step', 'description_zh': '宿主推进时间达到封膜时间后完成，压头抬起、载板送出、封膜计数加1。'}
                        for k in ('sealing.status', 'sealing.elapsed_s', 'sealing.progress', 'plate.simulated_sealed', 'scene.press_displacement_m',
                                  'carrier.position', 'device.seal_count')]
        m['capabilities'][cid] = {'name_zh': desc, 'description_zh': desc, 'kind': kind, 'visibility': 'agent', 'availability': 'implemented', 'component_ids': comps,
            'input_schema': _ahs_obj(_ahs_copy.deepcopy(props)), 'output_schema': _ahs_obj({k: _ahs_copy.deepcopy(_AHS_SLOTS[k]['value_schema']) for k in outs}),
            'state_effects': entries, 'observes': list(outs), 'example_arguments': [_ahs_copy.deepcopy(example)], 'preconditions_zh': pre,
            'binding': 'basic40_runtime_automation.AzentaHeatSealerSession.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
            'implementation': {'process': 'implemented', 'scene': 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'implemented'},
            'model_effects': {'mode': 'none', 'binding_ids': [], 'description_zh': '只改变运行层状态；屏幕由panel.capture渲染。' if cid != 'sealing.seal_plate' else
                              '有scene_adapter时压头位置经回调确认（压下与抬起）；没有回调时只改变运行层状态。'},
            'execution_notes': {'mode': 'asynchronous' if cid == 'sealing.seal_plate' else 'synchronous', 'time_domain': 'simulation',
                                'completion_zh': '宿主累计时间达到封膜时间。' if cid == 'sealing.seal_plate' else '返回succeeded表示本次调用完成。',
                                'cancellable': cid == 'sealing.seal_plate', 'partial_failure_zh': '被拒的命令不提交内部状态。', 'retry_zh': '相同request_id与参数使用当前内存缓存。'},
            'errors': _ahs_copy.deepcopy(errors)}
    for cid, (props, outs, example, effects, desc) in _AHS_FAULT_CAPS.items():
        m['capabilities'][cid] = {'name_zh': desc, 'description_zh': desc, 'kind': 'simulation', 'visibility': 'evaluator', 'availability': 'implemented',
            'component_ids': ['status_display', 'press_head', 'plate_carriage'], 'input_schema': _ahs_obj(_ahs_copy.deepcopy(props)),
            'output_schema': _ahs_obj({k: _ahs_copy.deepcopy(_AHS_SLOTS[k]['value_schema']) for k in outs}),
            'state_effects': [{'state_id': k, 'timing': 'immediate', 'description_zh': '故障注入或复位的明确状态更新。'} for k in effects], 'observes': list(outs),
            'example_arguments': [_ahs_copy.deepcopy(example)], 'preconditions_zh': ['调用者为evaluator/debug身份'] + (['已有故障时先fault.clear'] if cid == 'fault.inject' else []),
            'binding': 'basic40_runtime_automation.AzentaHeatSealerSession.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
            'implementation': {'process': 'implemented', 'scene': 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'implemented'},
            'model_effects': {'mode': 'none', 'binding_ids': [], 'description_zh': '只改变运行层状态；屏幕由panel.capture渲染。'},
            'execution_notes': {'mode': 'synchronous', 'time_domain': 'simulation', 'completion_zh': '返回succeeded表示本次调用完成。', 'cancellable': False,
                                'partial_failure_zh': '被拒的命令不提交内部状态。', 'retry_zh': '相同request_id与参数使用当前内存缓存。'},
            'errors': _ahs_copy.deepcopy(errors)}
    for cid in ('power.set', 'thermal.set_temperature', 'configuration.set', 'scene.set_press_displacement', 'sealing.start', 'sealing.cancel'):
        extra = ['power_saving.active'] + (['protocol.unlocked', 'carrier.position'] if cid == 'power.set' else [])
        if cid == 'sealing.start':
            m['capabilities'][cid]['state_effects'].append({'state_id': 'device.seal_count', 'timing': 'on_step', 'description_zh': '封膜成功完成时封膜计数加1。'})
        m['capabilities'][cid]['state_effects'] += [{'state_id': k, 'timing': 'immediate', 'description_zh': '任何被接受的命令结束节能并重新计空闲时间；上电重置面板相关状态。'} for k in extra]
    m['parameters'].update({k: {'value': v, 'unit': u, 'evidence': e} for k, v, u, e in [
        ('power_saving_idle_time_range_s', [1800, 43200], 's', 'catalog'), ('power_saving_hold_temperatures_K', [None, 323.15, 373.15], 'K', 'catalog'),
        ('power_saving_room_temperature_K', AZENTA_HEAT_SEALER_ROOM_K, 'K', 'assumed'), ('protocol_password_max_letters', 4, '1', 'catalog'),
        ('administrator_default_password', AZENTA_HEAT_SEALER_ADMIN_PASSWORD, '1', 'catalog')]})
    m['host_api'].update({'host_set_inspection_door': '宿主确认侧检修门开/关；封膜中打开为安全故障（任务失败）。',
                          'host_remove_film': 'Chg roll释放后宿主取下膜卷：film.roll_id等清空。',
                          'host_set_film_remaining': '宿主改写膜余量（例如膜用完）。'})
    m['panel_capabilities'] = {}
    if panel and (AZENTA_HEAT_SEALER_ASSET_DIR / 'panel_ui.json').is_file():
        p = _ahs_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        effects = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called
                        for e in m['capabilities'][c]['state_effects'] if e['timing'] == 'immediate'}.values())
        for name, d in p['caps'].items():
            gesture = name not in ('panel.capture', 'panel.frame')
            m['panel_capabilities'][name] = {'name_zh': d['function_zh'], 'description_zh': d['function_zh'], 'kind': d['kind'], 'visibility': d['visibility'],
                'availability': 'implemented', 'component_ids': d['component_ids'], 'input_schema': d['input_schema'], 'output_schema': d['output_schema'],
                'state_effects': _ahs_copy.deepcopy(effects) if gesture else [], 'observes': [], 'example_arguments': [d['example_arguments']], 'preconditions_zh': [],
                'binding': 'basic40_runtime_automation.AzentaHeatSealerSession.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % name,
                'implementation': {'process': 'implemented', 'scene': 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'implemented'},
                'model_effects': {'mode': 'none', 'binding_ids': [], 'description_zh': '没有3D模型绑定：屏幕纹理与触摸联动属于后续Matterix步骤'},
                'execution_notes': {'mode': 'synchronous', 'time_domain': 'simulation', 'completion_zh': '手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受' if gesture else '同步返回当前画面',
                                    'cancellable': False, 'partial_failure_zh': '一个手势可依次调用多个能力，前面已提交的不回滚。', 'retry_zh': '相同request_id返回缓存。'},
                'errors': _ahs_copy.deepcopy(errors)}
    m['limitations'] = list(m['limitations']) + [
        '前面板（panel.*）：按用户手册347598 Rev.E实现彩色触摸屏主屏（封膜计数、软件版本、当前协议、温度/时间图标与调整窗、Seal、状态、节能、载板）、协议管理（列表、新建/编辑/删除/选用、'
        '安全级别与密码、Administrator页）、载板选项（Close door/Chg roll/Esc）、装膜流程与警告窗；后面板电源开关。每个面板动作都调用本运行层的能力。',
        '面板背后的设备功能已在运行层实现：封膜时间、前面板封膜循环（采用宿主已装的板与膜；有场景回调时压头位置经回调确认）、封膜计数、节能（空闲时间、保持温度、关加热时按假设20 °C室温冷却；'
        '触摸或命令唤醒）、协议与密码、关门切膜、换膜释放与装膜流程的仪器动作；穿膜与取膜卷等手工步骤由宿主完成（host_load_film / host_remove_film）。',
        '节能在宿主步结束时判定（空闲时间达到后从下一步开始降温）；API上的thermal.set_temperature等命令不受红锁限制（红锁只锁前面板）。',
        '面板手势按发出手势的会话身份执行：前面板Seal启动的封膜属于该会话（poll/cancel）；封膜进行中，其他会话的改温度/时间/协议被拒'
        '（ACCESS_DENIED，状态不变，触摸屏显示提示窗，文字为假设）；评估者不受限。后面板电源开关（power.set即此开关）是硬电源开关：任何人都可操作，'
        '封膜如断电般停止（cancelled）。',
        'Error 1–12（手册p.64）只能由评测者fault.inject注入：屏幕显示错误、封膜停止、封膜与压头/载板动作被拒，直到fault.clear（服务排除+SR复位）；'
        '故障原因本身（温度超限、电机过流等）不作物理仿真，故障时温度控制照常（手册未写）。']
    return m


def export_azenta_heat_sealer_contract_metadata():
    return _ahs_export_metadata_v2()


_ahs_create_legacy = AzentaHeatSealerRuntime.create_instance
_ahs_invoke_legacy = AzentaHeatSealerRuntime._invoke
_ahs_step_legacy = AzentaHeatSealerRuntime.host_step
_ahs_describe_legacy_panel = AzentaHeatSealerSession.describe


def _ahs_create_instance(self, instance_id, scene_adapter=None):
    with self._lock:
        _ahs_create_legacy(self, instance_id, scene_adapter=scene_adapter)
        item = self._instances[instance_id]
        item['state'].update(_ahs_copy.deepcopy(_AHS_PANEL_DEFAULTS))
        item['protocols'] = [{'name': n, 'temperature_K': round(t + 273.15, 2), 'press_time_s': d, 'information': i, 'security': 'none', 'password': ''}
                             for n, t, d, i in AZENTA_HEAT_SEALER_PRESETS]
        item.update(admin_password=AZENTA_HEAT_SEALER_ADMIN_PASSWORD, idle_s=0.0, power_epoch=0, panel_cycle=None)
        _ahs_publish_protocols(item)
        if not hasattr(self, '_panels'):
            self._panels, self.panel_capture_dir, self._panel_serial = {}, None, 0
            self._principals.setdefault('__ahs_panel__', ('agent', None))


def _ahs_publish_protocols(item):
    item['state']['protocol.list'] = [{k: p[k] for k in ('name', 'temperature_K', 'press_time_s', 'information', 'security')} for p in item['protocols']]


def _ahs_protocol(item, name):
    return next((p for p in item['protocols'] if p['name'] == name), None)


def _ahs_activity(item):
    """Any accepted command or touch ends power saving and restarts the idle time (manual p.36-37)."""
    item['idle_s'] = 0.0
    item['state']['power_saving.active'] = False
    _AzentaHeatSealerRefresh(item['state'])


def _AzentaHeatSealerRefresh(s):
    AzentaHeatSealerRuntime._refresh(s)


_AHS_RUN_CHANGES = ('thermal.set_temperature', 'configuration.set', 'sealing.set_time', 'protocol.select')


def _ahs_foreign_run(self, principal, item, cid, args):
    """Another caller's seal is running: a change of its temperature, time or protocol is refused (lead rule 2026-10-19); the evaluator / debug
    identities are unrestricted.  The device itself refuses these changes during a seal for everyone (BUSY).  The rear power switch (and
    power.set, which models it) is a hard mains switch: never refused, the seal stops as at a power loss."""
    if cid not in _AHS_RUN_CHANGES or not isinstance(args, dict) or item['state']['sealing.status'] != 'running':
        return False
    if principal == '__ahs_panel__' or self._principals[principal][0] in ('debug', 'evaluator'):
        return False
    op = self._operations.get(item.get('active_operation'))
    return op is not None and op['principal'] != principal


def _ahs_invoke(self, principal, iid, cid, args, request_id):
    with self._lock:
        item = self._get(iid, principal)
        if item is not None and cid in _AHS_FAULT_CAPS:
            return _ahs_invoke_fault(self, principal, iid, item, cid, args, request_id)
        if item is not None and _ahs_foreign_run(self, principal, item, cid, args):
            return _ahs_dedup(self, principal, iid, cid, args, request_id,
                              lambda: self._response(iid, request_id, status='rejected', code='ACCESS_DENIED'))
        if item is not None and cid in _AHS_FAULT_BLOCKED and item['state']['device.error_code'] is not None and isinstance(args, dict):
            return _ahs_dedup(self, principal, iid, cid, args, request_id,           # Error N on the screen: no seal or mechanism motion
                              lambda: self._response(iid, request_id, status='rejected', code='PRECONDITION_FAILED'))
        if item is not None and cid in _AHS_PANEL_CAPS:
            return _ahs_invoke_panel_device(self, principal, iid, item, cid, args, request_id)
        if item is not None and isinstance(cid, str) and cid.startswith('panel.'):
            return _ahs_invoke_panel(self, principal, iid, item, cid, args, request_id)
        response = _ahs_invoke_legacy(self, principal, iid, cid, args, request_id)
        if item is not None and response['status'] in ('succeeded', 'accepted') and cid not in ('sealing.poll', 'sealing.read'):
            if cid == 'power.set' and args.get('enabled') is True and not item.get('was_powered'):
                item['power_epoch'] += 1
                item['state'].update({'protocol.unlocked': False, 'carrier.position': 'out'})
            item['was_powered'] = item['state']['power.enabled']
            _ahs_activity(item)
        return response


def _ahs_dedup(self, principal, iid, cid, args, request_id, run):
    """Request de-duplication and the response envelope of the legacy commands, for the front-panel commands."""
    try:
        fingerprint = _ahs_json.dumps([cid, args], sort_keys=True, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, OverflowError):
        return self._response(iid, request_id, status='rejected', code='INVALID_ARGUMENT')
    if not _ahs_string(request_id) or not isinstance(args, dict):
        return self._response(iid, request_id, status='rejected', code='INVALID_ARGUMENT')
    key = (principal, iid, request_id)
    if key in self._requests:
        old, response = self._requests[key]
        return _ahs_copy.deepcopy(response) if old == fingerprint else self._response(iid, request_id, status='rejected', code='REQUEST_CONFLICT')
    response = run()
    self._requests[key] = (fingerprint, _ahs_copy.deepcopy(response))
    return response


def _ahs_valid(schema, value):
    return _amc_valid(schema, value)


def _ahs_invoke_fault(self, principal, iid, item, cid, args, request_id):
    """Evaluator: put the unit into a documented error state (manual p.64) or clear it (service repair and SR reset, manual 357454)."""
    def run():
        if self._principals[principal][0] not in ('debug', 'evaluator'):
            return self._response(iid, request_id, status='rejected', code='ACCESS_DENIED')
        props, outs = _AHS_FAULT_CAPS[cid][0], _AHS_FAULT_CAPS[cid][1]
        if not _ahs_valid(_ahs_obj(props), args):
            return self._response(iid, request_id, status='rejected', code='INVALID_ARGUMENT')
        s = item['state']
        if cid == 'fault.inject':
            if s['device.error_code'] is not None:
                return self._response(iid, request_id, status='rejected', code='PRECONDITION_FAILED')
            s['device.error_code'] = args['code']
            self._stop(item, 'failed')                                        # the seal stops; press and carrier stay where they are
            item['panel_cycle'] = None
        else:
            s['device.error_code'] = None
        item['revision'] += 1
        return self._response(iid, request_id, result={k: _ahs_copy.deepcopy(s[k]) for k in outs})
    return _ahs_dedup(self, principal, iid, cid, args, request_id, run)


def _ahs_invoke_panel_device(self, principal, iid, item, cid, args, request_id):
    def run():
        reject = lambda code, status='rejected': self._response(iid, request_id, status=status, code=code)   # noqa: E731
        props, outs = _AHS_PANEL_CAPS[cid][0], _AHS_PANEL_CAPS[cid][1]
        if not _ahs_valid(_ahs_obj(props), args):
            return reject('INVALID_ARGUMENT')
        s = item['state']
        if not s['power.enabled']:
            return reject('PRECONDITION_FAILED')
        running = s['sealing.status'] == 'running'
        before = _ahs_copy.deepcopy(s)
        status, op_id = 'succeeded', None
        if cid == 'sealing.set_time':
            if running:
                return reject('BUSY')
            s['configuration.press_time_s'] = float(args['press_time_s'])
        elif cid == 'sealing.seal_plate':
            if running:
                return reject('BUSY')
            if (s['carrier.position'] != 'out' or not s['plate.loaded'] or s['plate.simulated_sealed'] or s['film.roll_id'] is None or s['film.released']
                    or not s['film.remaining_seals'] or s['configuration.press_time_s'] is None or not s['thermal.target_reached'] or not s['door.inspection_closed']):
                return reject('PRECONDITION_FAILED')
            q = .077 - s['plate.height_m']
            if item['adapter'] is not None:
                try:
                    confirmed = item['adapter'](iid, {'scene.press_displacement_m': q}) is True
                except Exception:
                    confirmed = False
                if not confirmed:
                    return self._response(iid, request_id, status='failed', code='DEVICE_ERROR', side_effects='unknown')
            s.update({'configuration.plate_adapter_id': item['plate_adapter_id'], 'configuration.film_roll_position_id': item['film_position_id'],
                      'scene.press_displacement_m': q})
            item['scene_confirmed'] = True
            self._panel_serial = getattr(self, '_panel_serial', 0) + 1
            inner = _ahs_invoke_legacy(self, principal, iid, 'sealing.start', {}, '%s#seal_plate_%d' % (request_id, self._panel_serial))
            if inner['status'] != 'accepted':
                s.update({k: before[k] for k in s})
                item['scene_confirmed'] = False
                return reject(inner['error']['code'])
            item['panel_cycle'] = inner['operation_id']
            s['carrier.position'] = 'in'
            status, op_id = 'accepted', inner['operation_id']
        elif cid == 'power_saving.configure':
            if not float(args['idle_time_s'] / 1800.0).is_integer():
                return reject('INVALID_ARGUMENT')
            s.update({'power_saving.enabled': args['enabled'], 'power_saving.idle_time_s': float(args['idle_time_s']),
                      'power_saving.hold_temperature_k': args['hold_temperature_K']})
        elif cid == 'power_saving.wake':
            pass
        elif cid == 'carrier.close_door':
            if running or s['carrier.position'] != 'out' or s['plate.loaded']:
                return reject('PRECONDITION_FAILED')
            s['carrier.position'] = 'in'
            if s['film.roll_id'] is not None and not s['film.released'] and s['film.remaining_seals']:
                s['carrier.cut_seal_present'] = True
                s['film.remaining_seals'] -= 1
        elif cid == 'carrier.open':
            if s['carrier.position'] != 'in' or running:
                return reject('PRECONDITION_FAILED')
            s.update({'carrier.position': 'out', 'carrier.cut_seal_present': False})
        elif cid == 'film.release':
            if running or s['film.roll_id'] is None or s['film.released']:
                return reject('PRECONDITION_FAILED')
            s['film.released'] = True
        elif cid == 'film.loading_step':
            step = args['step']
            if step == 'to_loading_position' and not s['plate.loaded'] and not running:
                s['carrier.position'] = 'loading'
            elif step == 'cut' and s['carrier.position'] == 'loading' and s['film.roll_id'] is not None:
                s.update({'carrier.position': 'out', 'film.released': False})
            elif step == 'finish' and s['carrier.position'] == 'out' and s['film.roll_id'] is not None:
                pass
            else:
                return reject('PRECONDITION_FAILED')
        elif cid == 'protocol.save':
            old = None if args['original_name'] is None else _ahs_protocol(item, args['original_name'])
            if (args['original_name'] is not None and old is None) or (args['name'] != args['original_name'] and _ahs_protocol(item, args['name']) is not None):
                return reject('PRECONDITION_FAILED')
            if args['security'] != 'none' and not 1 <= len(args['password']) <= 4:
                return reject('INVALID_ARGUMENT')
            if old is not None and old['security'] != 'none' and args['current_password'] not in (old['password'], item['admin_password']):
                return reject('ACCESS_DENIED')
            new = {'name': args['name'], 'temperature_K': float(args['temperature_K']), 'press_time_s': float(args['press_time_s']), 'information': args['information'],
                   'security': args['security'], 'password': args['password'] if args['security'] != 'none' else ''}
            if old is None:
                item['protocols'].append(new)
            else:
                item['protocols'][item['protocols'].index(old)] = new
                if s['protocol.current'] == old['name']:
                    s['protocol.current'] = new['name']
            _ahs_publish_protocols(item)
        elif cid == 'protocol.select':
            p = _ahs_protocol(item, args['name'])
            if running or p is None:
                return reject('PRECONDITION_FAILED' if p is None else 'BUSY')
            if p['security'] == 'high' and args['password'] != p['password']:
                return reject('ACCESS_DENIED')
            s.update({'protocol.current': p['name'], 'protocol.unlocked': False, 'thermal.setpoint_k': p['temperature_K'], 'configuration.press_time_s': p['press_time_s']})
            self._refresh(s)
        elif cid == 'protocol.delete':
            p = _ahs_protocol(item, args['name'])
            if p is None:
                return reject('PRECONDITION_FAILED')
            if p['security'] != 'none' and args['password'] not in (p['password'], item['admin_password']):
                return reject('ACCESS_DENIED')
            item['protocols'].remove(p)
            if s['protocol.current'] == p['name']:
                s['protocol.current'] = None
            _ahs_publish_protocols(item)
        elif cid == 'protocol.check_password':
            p = _ahs_protocol(item, args['name'])
            if p is None:
                return reject('PRECONDITION_FAILED')
            if p['security'] != 'none' and args['password'] not in (p['password'], item['admin_password']):
                return reject('ACCESS_DENIED')
        elif cid == 'protocol.unlock':
            p = _ahs_protocol(item, s['protocol.current']) if s['protocol.current'] else None
            if p is None or p['security'] != 'high':
                return reject('PRECONDITION_FAILED')
            if args['password'] != p['password']:
                return reject('ACCESS_DENIED')
            s['protocol.unlocked'] = True
        elif cid == 'protocol.admin_set_password':
            if args['admin_password'] != item['admin_password']:
                return reject('ACCESS_DENIED')
            if args['name'] is None:
                item['admin_password'] = args['new_password']
            else:
                p = _ahs_protocol(item, args['name'])
                if p is None or p['security'] == 'none':
                    return reject('PRECONDITION_FAILED')
                p['password'] = args['new_password']
        if cid != 'protocol.check_password':
            _ahs_activity(item)
        if s != before or cid in ('protocol.admin_set_password', 'protocol.save'):
            item['revision'] += 1
        return self._response(iid, request_id, status=status, result={k: _ahs_copy.deepcopy(s[k]) for k in outs}, operation_id=op_id)
    return _ahs_dedup(self, principal, iid, cid, args, request_id, run)


def _ahs_invoke_panel(self, principal, iid, item, cid, args, request_id):
    """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
    def run():
        panel = _ahs_panel()
        definition = panel['caps'].get(cid)
        if definition is None:
            return self._response(iid, request_id, status='rejected', code='UNKNOWN_CAPABILITY')
        if definition['visibility'] != 'agent' and self._principals[principal][0] not in ('debug', 'evaluator'):
            return self._response(iid, request_id, status='rejected', code='ACCESS_DENIED')
        if panel['engine'].check_arguments(definition['input_schema'], args):
            return self._response(iid, request_id, status='rejected', code='INVALID_ARGUMENT')
        ui = self.panel(iid)
        try:
            if cid == 'panel.frame':
                return self._response(iid, request_id, result={'frame': ui.frame()})
            if cid == 'panel.capture':
                data, width, height = ui.png_bytes(args['display_id'])
                digest = _ahs_hashlib.sha256(data).hexdigest()
                folder = _ahs_pathlib.Path(self.panel_capture_dir) if self.panel_capture_dir else _ahs_pathlib.Path(_ahs_tempfile.gettempdir()) / 'chemistry_sim_panel' / iid
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ('%s_%s.png' % (args['display_id'], digest[:16]))
                if not path.exists():
                    path.write_bytes(data)
                return self._response(iid, request_id, result={'display_id': args['display_id'], 'time_s': self.time_s,
                                                               'image': {'path': str(path), 'format': 'png', 'width': width, 'height': height, 'sha256': digest}})
            if cid == 'panel.touch':
                to = (args['to_x'], args['to_y']) if 'to_x' in args and 'to_y' in args else None
                accepted = ui.touch(args['display_id'], args['x'], args['y'], float(args.get('duration_s', 0.1)), to=to)
            elif cid == 'panel.switch':
                accepted = ui.switch(args['input_id'], args['position'], held=args.get('held', ()))
            else:
                accepted = ui.press(args['input_id'], float(args.get('duration_s', 0.1)), held=args.get('held', ()))
        except panel['engine'].PanelError:
            return self._response(iid, request_id, status='rejected', code='INVALID_ARGUMENT')
        return self._response(iid, request_id, result={'accepted': bool(accepted)})

    def gesture():
        host = self.panel(iid).host
        host.caller = principal                                               # the gesture acts as this session's caller (owns the seal it starts)
        try:
            return run()
        finally:
            host.caller = None
    return _ahs_dedup(self, principal, iid, cid, args, request_id, gesture if cid in ('panel.touch', 'panel.switch', 'panel.press') else run)


def _ahs_host_step(self, dt_s, *, world_time_s):
    with self._lock:
        saved = {}
        for iid, item in self._instances.items():                                 # power saving: the block heads for the hold temperature
            s = item['state']
            if s['power.enabled'] and s['power_saving.active']:
                saved[iid] = s['thermal.setpoint_k']
                hold = s['power_saving.hold_temperature_k']
                s['thermal.setpoint_k'] = AZENTA_HEAT_SEALER_ROOM_K if hold is None else hold
        statuses = {iid: item['state']['sealing.status'] for iid, item in self._instances.items()}
        try:
            result = _ahs_step_legacy(self, dt_s, world_time_s=world_time_s)
        finally:
            for iid, setpoint in saved.items():
                self._instances[iid]['state']['thermal.setpoint_k'] = setpoint
                self._refresh(self._instances[iid]['state'])
        for iid, item in self._instances.items():
            s = item['state']
            if statuses[iid] == 'running' and s['sealing.status'] == 'succeeded':
                s['device.seal_count'] += 1
            if item.get('panel_cycle') and s['sealing.status'] != 'running':            # the front-panel cycle ends: press up, carrier out
                retract = True
                if item['adapter'] is not None:
                    try:
                        retract = item['adapter'](iid, {'scene.press_displacement_m': 0.0}) is True
                    except Exception:
                        retract = False
                if retract:
                    s['scene.press_displacement_m'] = 0.0
                s['carrier.position'] = 'out'
                item['panel_cycle'] = None
                item['revision'] += 1
            if s['power.enabled'] and s['power_saving.enabled'] and s['sealing.status'] != 'running' and not s['power_saving.active']:
                item['idle_s'] += dt_s
                if item['idle_s'] >= s['power_saving.idle_time_s'] - 1e-9:
                    s['power_saving.active'] = True
                    item['revision'] += 1
        return result


def _ahs_host_set_inspection_door(self, instance_id, *, closed):
    """Trusted host: the side inspection door; opening it during a seal is a safety fault (the run fails, manual p.61)."""
    with self._lock:
        item = self._get(instance_id)
        if item is None or type(closed) is not bool:
            raise ValueError('Known instance and boolean door state required')
        item['state']['door.inspection_closed'] = closed
        if not closed:
            self._stop(item, 'failed')
        item['revision'] += 1


def _ahs_host_remove_film(self, instance_id):
    """Trusted host: the released roll is taken off (after Chg roll)."""
    with self._lock:
        item = self._require_idle(instance_id)
        if not item['state']['film.released']:
            raise ValueError('Release the film (Chg roll) first')
        item['state'].update({'film.roll_id': None, 'film.width_m': None, 'film.remaining_seals': None, 'film.released': False})
        item['film_position_id'] = None
        item['revision'] += 1


def _ahs_host_set_film_remaining(self, instance_id, *, remaining_seals):
    with self._lock:
        item = self._get(instance_id)
        if item is None or item['state']['film.roll_id'] is None or type(remaining_seals) is not int or not 0 <= remaining_seals <= 5000:
            raise ValueError('Loaded film and remaining count 0..5000 required')
        item['state']['film.remaining_seals'] = remaining_seals
        item['revision'] += 1


def _ahs_runtime_panel(self, instance_id):
    """The front-panel engine of an instance (trusted host / tests: render, frame); created on first use."""
    if instance_id not in self._panels:
        self._panels[instance_id] = _ahs_panel()['engine'].PanelUI(_ahs_panel()['spec'], _AzentaPanelHost(self, instance_id))
    return self._panels[instance_id]


def _ahs_describe_panel(self, *, asset_type_id=None, instance_id=None):
    m = _ahs_describe_legacy_panel(self, asset_type_id=asset_type_id, instance_id=instance_id)
    if 'capabilities' in m:
        role = self._AzentaHeatSealerSession__host._principals[self._AzentaHeatSealerSession__principal][0]
        if role not in ('debug', 'evaluator'):
            m['capabilities'] = {k: v for k, v in m['capabilities'].items() if v['visibility'] == 'agent'}
        for k, v in m.pop('panel_capabilities', {}).items():
            if v['visibility'] == 'agent' or role in ('debug', 'evaluator'):
                v['state_effects'] = [dict(e, state_id=e['state_id'].lower()) for e in v['state_effects']]
                m['capabilities'][k] = v
    return m


class _AzentaPanelHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py for one instance: what the touch-screen firmware reads, calls and times by."""
    def __init__(self, runtime, instance_id):
        self.runtime, self.instance_id = runtime, instance_id
        self.caller = None                                                    # principal of the session whose gesture is being handled

    def panel_read(self):
        item = self.runtime._instances[self.instance_id]
        out = {sid: _ahs_copy.deepcopy(v) for sid, v in item['state'].items() if _AHS_SLOTS[sid]['visibility'] == 'agent'}
        out.update({'host.powered': item['state']['power.enabled'], 'host.power_epoch': item['power_epoch']})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action takes the checked path of a direct call (same preconditions and revision) as the gesturing session's caller (its seal
        belongs to that principal); the fixed panel identity only for actions without a session gesture."""
        rt = self.runtime
        principal = self.caller or '__ahs_panel__'
        rt._panel_serial = getattr(rt, '_panel_serial', 0) + 1
        while (principal, self.instance_id, '__panel_%d' % rt._panel_serial) in rt._requests:   # never replay one of the caller's own request ids
            rt._panel_serial += 1
        key = (principal, self.instance_id, '__panel_%d' % rt._panel_serial)
        response = rt._invoke(principal, self.instance_id, capability_id, dict(arguments), key[2])
        rt._requests.pop(key, None)                                           # nor take one from the caller (the gesture itself is de-duplicated)
        if response['status'] not in ('succeeded', 'accepted'):
            raise _ahs_panel()['engine'].PanelRejected(response['error']['code'])
        return response['result']

    def panel_time(self):
        return self.runtime.time_s


AzentaHeatSealerRuntime.create_instance = _ahs_create_instance
AzentaHeatSealerRuntime._invoke = _ahs_invoke
AzentaHeatSealerRuntime.host_step = _ahs_host_step
AzentaHeatSealerRuntime.host_set_inspection_door = _ahs_host_set_inspection_door
AzentaHeatSealerRuntime.host_remove_film = _ahs_host_remove_film
AzentaHeatSealerRuntime.host_set_film_remaining = _ahs_host_set_film_remaining
AzentaHeatSealerRuntime.panel = _ahs_runtime_panel
AzentaHeatSealerSession.describe = _ahs_describe_panel
# END CHEM-176 AZENTA HEAT SEALER FRONT PANEL

# BEGIN CHEM-174 QUANTOS STRICT COMMON-CLOCK PROGRESSION
# Preserve the existing implementation while rejecting a positive dt that is
# below the representable resolution of the current floating-point clock.
_qt_host_step_before_clock_resolution_check = QuantosQ2Runtime.host_step


def _qt_host_step_with_clock_resolution_check(self, dt_s, *, world_time_s):
    with self._lock:
        if _qt_number(dt_s) and _qt_number(world_time_s) and dt_s > 0 and (
                world_time_s <= self.time_s or self.time_s + dt_s <= self.time_s):
            raise ValueError('A positive transfer step must strictly advance the representable common world clock')
        return _qt_host_step_before_clock_resolution_check(self, dt_s, world_time_s=world_time_s)


QuantosQ2Runtime.host_step = _qt_host_step_with_clock_resolution_check
# END CHEM-174 QUANTOS STRICT COMMON-CLOCK PROGRESSION

# BEGIN CHEM-175 OPENTRONS FLEX DECIMAL LEDGER REVIEW PATCH
# Additive patch: retain every previously published instrument block verbatim.
import decimal as _flex_decimal
_flex_apply_before_decimal_review = OpentronsFlexRuntime._apply


def _flex_apply_decimal_review(self, item, iid, cid, args, rid):
    if cid not in ('sample.aspirate', 'sample.dispense'):
        return _flex_apply_before_decimal_review(self, item, iid, cid, args, rid)
    state = item['state']
    fail = lambda: self._response(iid, rid, status='rejected', code='PRECONDITION_FAILED')
    container = item['containers'].get(args['container_id'])
    tip_id = state['tip.attached_id']
    if (not state['power.enabled'] or container is None or tip_id is None
        or container['slot_id'] not in OPENTRONS_FLEX_WORK_SLOTS
        or not self._position_matches(self._position(item), container['position_m'])):
        return fail()
    tip = item['tips'][tip_id]
    donor, receiver = (container, tip) if cid == 'sample.aspirate' else (tip, container)
    # Canonical decimal SI values eliminate cumulative binary roundoff at the
    # specified 5 uL and 1000 uL bounds. Wire observations remain JSON numbers.
    with _flex_decimal.localcontext() as context:
        context.prec = 700
        dec = _flex_decimal.Decimal
        volume = dec(str(args['volume_m3']))
        donor_before = dec(str(donor['volume_m3']))
        receiver_before = dec(str(receiver['volume_m3']))
        receiver_capacity = dec(str(receiver['capacity_m3']))
        if donor_before < volume or receiver_capacity - receiver_before < volume:
            return fail()
        donor_after = float(donor_before - volume)
        receiver_after = float(receiver_before + volume)
    # A huge host inventory can make a microvolume change unrepresentable in
    # the JSON-number state. Reject that case instead of claiming a transfer.
    if donor_after == donor['volume_m3'] or receiver_after == receiver['volume_m3']:
        return fail()
    donor['volume_m3'], receiver['volume_m3'] = donor_after, receiver_after
    sequence = state['sample.transfer_count'] + 1
    state['truth.transfers'].append({'transaction_id': '%s.transfer.%d' % (iid, sequence),
        'donor_id': donor.get('container_id', donor.get('tip_id')),
        'receiver_id': receiver.get('container_id', receiver.get('tip_id')),
        'volume_m3': float(volume), 'unit': 'm^3', 'kind': cid.split('.')[1], 'time_s': self.time_s})
    state['sample.transfer_count'] = sequence
    self._sync_inventory(item)
    item['revision'] += 1
    return self._response(iid, rid, result={'sample.transfer_count': sequence})


OpentronsFlexRuntime._apply = _flex_apply_decimal_review
_FLEX_SLOTS['truth.tips']['value_schema']['items']['properties']['position_m']['description'] = 'Last host-registered pickup or release target in joint coordinates. While attached, the tip follows motion.x_m/y_m/z_m; this stored target is not a live tool pose.'
_flex_metadata_before_decimal_review = export_opentrons_flex_contract_metadata


def export_opentrons_flex_contract_metadata():
    metadata = _flex_metadata_before_decimal_review()
    metadata['limitations'].append('体积转移用十进制SI算术后输出JSON浮点数，避免5 µL累积到1000 µL的边界误拒绝；若宿主登记的极大库存使本次微量变化在输出数值中不可分辨，则拒绝且不提交。')
    metadata['limitations'].append('truth.tips中的position_m保存最后登记的拾取或释放目标；吸头附着后的实时位姿跟随motion.*，不把旧槽位坐标当作实时工具位置。')
    for cid in ('sample.aspirate', 'sample.dispense'):
        metadata['capabilities'][cid]['preconditions_zh'].append('供受体转移后的JSON数值须均可分辨本次变化；不支持极大库存上的不可分辨微量转移')
    return metadata
# END CHEM-175 OPENTRONS FLEX DECIMAL LEDGER REVIEW PATCH

# CHEM-175 review wording: public discovery must not enumerate private slot IDs.
_flex_metadata_before_public_review_wording = export_opentrons_flex_contract_metadata

def export_opentrons_flex_contract_metadata():
    metadata = _flex_metadata_before_public_review_wording()
    metadata['limitations'] = [text.replace('truth.tips中的position_m', '调试用吸头记录中的position_m') for text in metadata['limitations']]
    return metadata

# BEGIN CHEM-178 PRECISEFLEX 400 APPEND-ONLY RUNTIME
import copy as _pf400_copy
import json as _pf400_json
import math as _pf400_math
import threading as _pf400_threading

PRECISEFLEX400_TYPE = 'instrument.brooks_preciseflex_400'


def _pf400_number(value):
    try:
        return type(value) in (int, float) and _pf400_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _pf400_text(value):
    return isinstance(value, str) and bool(value.strip())


def _pf400_num(unit, lower=None, upper=None):
    result = {'type': 'number', 'x-unit': unit}
    if lower is not None:
        result['minimum'] = lower
    if upper is not None:
        result['maximum'] = upper
    return result


def _pf400_obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _pf400_nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def _pf400_matches(value, schema):
    if 'anyOf' in schema:
        return any(_pf400_matches(value, s) for s in schema['anyOf'])
    kind = schema.get('type')
    if kind == 'number':
        if not _pf400_number(value) or ('minimum' in schema and value < schema['minimum']) or ('maximum' in schema and value > schema['maximum']):
            return False
    elif kind == 'integer':
        if type(value) is not int or ('minimum' in schema and value < schema['minimum']):
            return False
    elif kind == 'boolean':
        if type(value) is not bool:
            return False
    elif kind == 'string':
        if not _pf400_text(value):
            return False
    elif kind == 'null':
        return value is None
    elif kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            return False
        if any(not _pf400_matches(value[k], spec) for k, spec in schema['properties'].items()):
            return False
    elif kind == 'array':
        if not isinstance(value, list) or any(not _pf400_matches(v, schema['items']) for v in value):
            return False
    else:
        return False
    return 'enum' not in schema or value in schema['enum']


# Joint ranges: Rev B indexed specification, standard arm with servo gripper.
# Detailed proxy dimensions below are authored assumptions, NOT measured link lengths.
_PF400_JOINT_LIMITS = {'z_m': (0.0, 0.4),
    'shoulder_rad': (-_pf400_math.radians(93), _pf400_math.radians(93)),
    'elbow_rad': (-_pf400_math.radians(168), _pf400_math.radians(168)),
    'wrist_rad': (-_pf400_math.radians(960), _pf400_math.radians(960))}
_PF400_GEOMETRY = {'shoulder_x_m': 0.0, 'shoulder_y_m': 0.0,
    'upper_arm_m': 0.25, 'forearm_m': 0.25, 'tcp_z_home_m': 0.20,
    'tool_offset_local_m': {'x_m': 0.079, 'y_m': 0.0, 'z_m': 0.0},
    'tool_aabb_local_m': {'x_m': [-0.099, 0.021], 'y_m': [-0.07, 0.07], 'z_m': [-0.04, 0.04]},
    'evidence': 'assumption',
    'evidence_zh': '用于本批次代理模型的显式几何假设；0.25+0.25+0.079 m的全伸TCP符合表内0.579 m水平伸距，不代表原厂连杆尺寸。真实连杆尺寸未可靠取得并保持null。'}


def _pf400_joint_schema():
    return _pf400_obj({k: _pf400_num('m' if k == 'z_m' else 'rad', *limits) for k, limits in _PF400_JOINT_LIMITS.items()})


_PF400_POS = _pf400_obj({k: _pf400_num('m') for k in ('x_m', 'y_m', 'z_m')})
_PF400_AABB = _pf400_obj({'min_m': _PF400_POS, 'max_m': _PF400_POS})
_PF400_TEXT = {'type': 'string', 'minLength': 1}
_PF400_BOOL = {'type': 'boolean'}
_PF400_EVENT = _pf400_obj({'event': {'type': 'string', 'enum': ['attach', 'release']},
    'container_id': _PF400_TEXT, 'constraint_id': _PF400_TEXT, 'port_id': _PF400_TEXT,
    'time_s': _pf400_num('s', 0), 'constraint_authority': {'type': 'string', 'enum': ['host']},
    'robot': {'type': 'string', 'enum': ['absent']}})


def preciseflex400_forward_kinematics(joints):
    """Pure FK for the authored, explicitly evidenced geometry; does not move time or scene."""
    if not _pf400_matches(joints, _pf400_joint_schema()):
        raise ValueError('Four finite joints inside their declared SI limits are required')
    q = {k: float(v) for k, v in joints.items()}
    g = _PF400_GEOMETRY
    shoulder = q['shoulder_rad']
    elbow = shoulder + q['elbow_rad']
    yaw = elbow + q['wrist_rad']
    p = {'x_m': g['shoulder_x_m'] + g['upper_arm_m'] * _pf400_math.cos(shoulder) + g['forearm_m'] * _pf400_math.cos(elbow),
         'y_m': g['shoulder_y_m'] + g['upper_arm_m'] * _pf400_math.sin(shoulder) + g['forearm_m'] * _pf400_math.sin(elbow),
         'z_m': g['tcp_z_home_m'] + q['z_m']}
    offset = g['tool_offset_local_m']
    p['x_m'] += offset['x_m'] * _pf400_math.cos(yaw) - offset['y_m'] * _pf400_math.sin(yaw)
    p['y_m'] += offset['x_m'] * _pf400_math.sin(yaw) + offset['y_m'] * _pf400_math.cos(yaw)
    # tcp_z_home_m includes the fixed local tool Z offset; rotation axes are all +Z.
    points = []
    for x in g['tool_aabb_local_m']['x_m']:
        for y in g['tool_aabb_local_m']['y_m']:
            for z in g['tool_aabb_local_m']['z_m']:
                points.append((p['x_m'] + x * _pf400_math.cos(yaw) - y * _pf400_math.sin(yaw),
                               p['y_m'] + x * _pf400_math.sin(yaw) + y * _pf400_math.cos(yaw), p['z_m'] + z))
    bounds = {name: {key: func(v[n] for v in points) for n, key in enumerate(('x_m', 'y_m', 'z_m'))}
              for name, func in (('min_m', min), ('max_m', max))}
    return {'joints': q, 'tcp_position_m': p, 'tool_yaw_rad': yaw, 'end_effector_aabb_m': bounds}


def _pf400_slots():
    def slot(schema, unit, origin, via=None, parameter=None, visibility='agent'):
        return {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': visibility,
                'access': 'read_write' if via else 'read_only', 'reference_conditions': 'Authored base frame; right-handed, +Z upward; SI.' if unit in ('m', 'rad') else None,
                'write_via': {'capability_id': via, 'parameter': parameter, 'fixed_arguments': {}} if via else None,
                'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}
    result = {
        'power.enabled': slot(_PF400_BOOL, '1', 'setpoint', 'power.set', 'enabled'),
        'motion.target_joints': slot(_pf400_nullable(_pf400_joint_schema()), '1', 'setpoint', 'motion.set_joints', 'joints'),
        'motion.status': slot({'type': 'string', 'enum': ['uninitialized', 'ready', 'target_pending', 'fault']}, '1', 'derived'),
        'scene.joints': slot(_pf400_nullable(_pf400_joint_schema()), '1', 'scene'),
        'scene.tcp_position_m': slot(_pf400_nullable(_PF400_POS), 'm', 'scene'),
        'scene.tool_yaw_rad': slot(_pf400_nullable(_pf400_num('rad')), 'rad', 'scene'),
        'scene.end_effector_aabb_m': slot(_pf400_nullable(_PF400_AABB), 'm', 'scene'),
        'scene.sample_time_s': slot(_pf400_nullable(_pf400_num('s', 0)), 's', 'scene'),
        'sample.attached_container_id': slot(_pf400_nullable(_PF400_TEXT), '1', 'scene'),
        'sample.constraint_id': slot(_pf400_nullable(_PF400_TEXT), '1', 'scene'),
        'sample.events': slot({'type': 'array', 'items': _PF400_EVENT}, '1', 'scene'),
        'sample.container_ids': slot({'type': 'array', 'items': _PF400_TEXT}, '1', 'scene'),
        'sample.receiver_port_ids': slot({'type': 'array', 'items': _PF400_TEXT}, '1', 'scene'),
        'host.scene_bound': slot(_PF400_BOOL, '1', 'derived'),
        'truth.registered_payloads': slot({'type': 'array', 'items': _pf400_obj({'container_id': _PF400_TEXT, 'gross_payload_kg': _pf400_nullable(_pf400_num('kg', 0))})}, '1', 'process_truth', visibility='debug'),
    }
    for sid, s in result.items():
        s['read_binding'] = 'PreciseFlex400Session.read(instance_id=..., state_ids=[%r])' % sid
    result['motion.target_joints']['reference_conditions'] = result['scene.joints']['reference_conditions'] = 'Mixed joint object: z_m in m; shoulder_rad, elbow_rad, wrist_rad in rad; positive +Z translation and right-handed +Z rotations.'
    return result


_PF400_LIMITATIONS = [
    '目标关节写入是同步设定，不表示实际运动完成。可信宿主提供共同时间并通过求值场景回调确认关节、TCP与末端包围盒。没有轨迹、动力学或碰撞规避。',
    '典型TCP速度0.5 m/s、最大加速度2 m/s²、重复定位±90 µm只作为整机证据，不推导各关节速度、部件误差、碰撞或运动时长。',
    'attach/release由预注册宿主约束回调执行；容器、端口、位置容差、gross payload及证据由宿主明确登记；没有接触抓取、夹爪力、摩擦或硬件驱动。',
    '清单Rev B原文为with Servo Gripper的0.5 kg负载；不同文档版本的口径仍需按实物配置核验。gross_payload_kg保守记录容器、内容物、额外夹具，以及宿主按配置计入的末端工具总外载；宿主必须明确提供，未给则null且不可attach。本模拟不授予实机载荷使用许可。',
    'J1范围0..0.4 m，J2±93°、J3±168°、J4±960°来自Rev B网页索引；直接PDF失效，图纸尺寸尚未可靠取得。两段0.25 m连杆和0.079 m腕到TCP偏置是明确的代理几何假设，真实部件尺寸仍为null。',
    '目标位姿通过有界关节目标与正运动学表达；尚无逆运动学、任务队列调度、原厂设备握手协议或可执行的TCP速度设定。宿主求值与约束回执仅为本仿真适配接口。',
    '样品拓扑仅搬运完整容器；不改变内容物或推断其组成。容器/端口唯一性与跨仪器约束一致性由外层世界注册表保证。',
    '本模块无气路端口。整机20 kg与0.5 kg额定负载不能代替连杆质量、惯量、夹具重量或材料参数；未知属性保持null。',
    '回调异常后外部副作用未知，motion.status=fault阻止重复附着和移动；只能由可信宿主在外部状态核实后恢复。',
    '身份来自可信宿主；API过滤不是Python或共享文件系统隔离。请求去重仅当前进程内存有效。',
]


def export_preciseflex400_contract_metadata():
    slots = _pf400_slots()
    public = [sid for sid, s in slots.items() if s['visibility'] == 'agent']
    specs = {
        'power.set': ({'enabled': _PF400_BOOL}, ['power.enabled'], ['power.enabled'], {'enabled': True}, ['controller'], '设置运行层供电状态；断电保留关节、待执行目标及已有约束。'),
        'motion.set_joints': ({'joints': _pf400_joint_schema()}, ['motion.target_joints', 'motion.status'], ['motion.target_joints', 'motion.status'], {'joints': {k: 0.0 for k in _PF400_JOINT_LIMITS}}, ['lift', 'upper_arm', 'forearm', 'wrist'], '检查关节限位并同步写目标；宿主场景确认前实际姿态不变。'),
        'motion.forward_kinematics': ({'joints': _pf400_joint_schema()}, [], [], {'joints': {k: 0.0 for k in _PF400_JOINT_LIMITS}}, ['upper_arm', 'forearm', 'wrist', 'gripper'], '查询目标关节对应的TCP、偏航及末端几何包围盒，不写状态或推进时间。'),
        'state.read': ({}, [], public, {}, ['controller'], '读取允许观测的统一状态快照，不推进共同时间。'),
        'sample.attach': ({'container_id': _PF400_TEXT, 'constraint_id': _PF400_TEXT}, ['sample.attached_container_id', 'sample.constraint_id', 'sample.events', 'motion.status'], ['sample.attached_container_id', 'sample.constraint_id', 'sample.events'], {'container_id': 'host_container', 'constraint_id': 'host_constraint'}, ['gripper'], '已登记容器、已登记约束、端口位置与负载检查通过后，调用宿主attach约束并记录明确回执。'),
        'sample.release': ({'receiver_port_id': _PF400_TEXT}, ['sample.attached_container_id', 'sample.constraint_id', 'sample.events', 'motion.status'], ['sample.attached_container_id', 'sample.constraint_id', 'sample.events'], {'receiver_port_id': 'host_receiver'}, ['gripper'], '在已登记接收端口调用宿主release约束，确认后登记容器拓扑与事件。'),
        'robot.contact_grasp': ({}, [], [], {}, ['gripper'], '接触抓取、机器人规划和真实夹爪驱动未实现。'),
        'motion.set_tcp_velocity': ({'velocity_m_s': _pf400_num('m/s', 0)}, [], [], {'velocity_m_s': 0.1}, ['controller'], '无轨迹执行器，典型整机TCP速度不作为可执行的速度设定。'),
    }
    caps = {}
    for cid, (props, effects, observed, example, components, description) in specs.items():
        unsupported = cid in ('robot.contact_grasp', 'motion.set_tcp_velocity')
        attach = cid in ('sample.attach', 'sample.release')
        output = _pf400_obj({sid: _pf400_copy.deepcopy(slots[sid]['value_schema']) for sid in observed})
        if cid == 'motion.forward_kinematics':
            output = _pf400_obj({'joints': _pf400_joint_schema(), 'tcp_position_m': _PF400_POS, 'tool_yaw_rad': _pf400_num('rad'), 'end_effector_aabb_m': _PF400_AABB})
        prereq = []
        if cid == 'motion.set_joints':
            prereq = ['供电、场景回调已注册且当前已确认姿态；不处于fault；目标各关节满足独立有限区间。']
        elif attach:
            prereq = ['供电、motion.status=ready、场景姿态时间等于当前宿主时钟。', '容器、端口及约束由可信宿主注册；当前位置落在显式容差内；attach时gross payload必须已知且≤0.5 kg；回调仅返回True才确认。']
        caps[cid] = {'name_zh': description, 'description_zh': description,
            'kind': 'query' if cid in ('state.read', 'motion.forward_kinematics') else 'robot_skill' if cid == 'robot.contact_grasp' else 'device_command',
            'visibility': 'agent', 'availability': 'unsupported' if unsupported else 'implemented', 'component_ids': components,
            'input_schema': _pf400_obj(_pf400_copy.deepcopy(props)), 'output_schema': output, 'observes': observed,
            'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': '按已验证条件更新；外部回调异常时仅fault状态改变，外部副作用unknown。' if sid == 'motion.status' and attach else '当前调用同步更新该状态。'} for sid in effects],
            'preconditions_zh': prereq, 'example_arguments': [_pf400_copy.deepcopy(example)],
            'binding': None if unsupported else 'basic40_runtime_automation.PreciseFlex400Session.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
            'implementation': {'process': 'absent' if unsupported else 'implemented', 'scene': 'implemented' if attach else 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'absent' if unsupported else 'implemented'},
            'model_effects': {'mode': 'host_required' if attach else 'none', 'binding_ids': [], 'description_zh': '可信宿主完成容器与夹爪外部约束；无接触求解。' if attach else '目标写入、纯FK与状态读取不直接修改几何；host_commit_pose独立确认实际模型求值。'},
            'execution_notes': {'mode': 'synchronous', 'time_domain': 'simulation', 'cancellable': False, 'completion_zh': 'succeeded只确认设定目标已写入；实际姿态由host_commit_pose确认。' if cid == 'motion.set_joints' else '宿主约束回调明确True且事件已登记。' if attach else '返回所声明result；不推进时间。', 'partial_failure_zh': '回调失败外部副作用unknown；置fault并保留现有登记，禁止自动重放。其他拒绝不改变状态。', 'retry_zh': '身份、实例、request_id相同且参数相同返回原响应，参数不同拒绝；无跨重启保证。'},
            'errors': [{'code': c, 'description_zh': d, 'side_effects': side} for c, d, side in [
                ('INVALID_ARGUMENT', '参数类型、范围或额外字段错误。', 'none'), ('UNKNOWN_INSTANCE', '实例不在身份允许范围。', 'none'), ('UNKNOWN_CAPABILITY', '能力不存在。', 'none'), ('ACCESS_DENIED', '状态不可写或不可读。', 'none'), ('PRECONDITION_FAILED', '供电、姿态、拓扑、约束或载荷条件不满足。', 'none'), ('BUSY', '外部回调执行期间拒绝重入写入。', 'none'), ('REQUEST_CONFLICT', '同一请求ID参数不一致。', 'none'), ('UNSUPPORTED', '没有本项执行器。', 'none'), ('DEVICE_ERROR', '外部约束回调未确认；fault已登记。', 'unknown')]]}
    return {'asset_type_id': PRECISEFLEX400_TYPE, 'asset_version': '1.0.1', 'catalog_id': 'CHEM-178', 'state_slots': slots, 'capabilities': caps,
        'kinematics': {'joint_limits': _pf400_copy.deepcopy(_PF400_JOINT_LIMITS), 'joint_limit_evidence_zh': 'PreciseFlex 400 Rev B网页索引；标准臂带servo gripper配置，完整尺寸图未可靠取得。', 'geometry': _pf400_copy.deepcopy(_PF400_GEOMETRY), 'direction_zh': 'z_m沿+Z；三个角关节按局部+Z右手正向（从上看逆时针）；代理零位全伸+X；FK x,y在台面固定基座坐标系。', 'scene_targets': {'z_m': 'pf400__j1_z.location.z', 'shoulder_rad': 'pf400__j2_shoulder.rotation_euler.z', 'elbow_rad': 'pf400__j3_elbow.rotation_euler.z', 'wrist_rad': 'pf400__j4_wrist.rotation_euler.z', 'tcp': 'pf400__tcp.matrix_world'}},
        'parameters': {k: {'value': v, 'unit': u, 'evidence': e} for k, v, u, e in [('z_travel_m', .4, 'm', 'catalog'), ('horizontal_reach_m', .579, 'm', 'catalog'), ('rated_payload_kg', .5, 'kg', 'catalog'), ('whole_machine_mass_kg', 20.0, 'kg', 'catalog'), ('typical_tcp_speed_m_s', .5, 'm/s', 'catalog'), ('maximum_tcp_acceleration_m_s2', 2.0, 'm/s^2', 'catalog'), ('repeatability_m', .00009, 'm', 'catalog'), ('manufacturer_upper_arm_length_m', None, 'm', 'unknown'), ('manufacturer_forearm_length_m', None, 'm', 'unknown'), ('manufacturer_wrist_to_tcp_offset_m', None, 'm', 'unknown'), ('link_mass_kg', None, 'kg', 'unknown'), ('link_inertia_kg_m2', None, 'kg*m^2', 'unknown'), ('gripper_force_N', None, 'N', 'unknown'), ('contact_friction', None, '1', 'unknown'), ('joint_speed_limits_rad_s', None, 'rad/s', 'unknown')]},
        'host_api': {name: {'entrypoint': 'PreciseFlex400Runtime.' + name, 'description_zh': desc} for name, desc in {
            'create_instance': '创建独立实例，未确认的实际姿态、目标与载荷均为空。', 'register_principal': '由可信宿主固定身份与实例范围。', 'host_register_scene_adapter': '注册实际求值场景回调；返回joints、TCP、偏航与末端AABB。', 'host_set_clock': '明确共同世界时间，只接受非递减有限秒值；不会自行运动。', 'host_commit_pose': '给定关节与共同时间，回调求值结果与独立FK一致才提交；已有目标必须匹配。', 'host_register_container': '明确样品容器及源端口、位置、位置容差、总负载和证据；不执行抓取。', 'host_register_receiver_port': '登记外部样品接收端口及明确的位置容差。', 'host_register_constraint': '为已登记容器预注册唯一约束ID及attach/release回调。', 'host_reconcile_fault': '外部状态人工核实后恢复；记录宿主提供的真实附着结果，再重新确认姿态。'}.items()},
        'limitations': list(_PF400_LIMITATIONS), 'output_schema_scope': 'capabilities.output_schema validates response.result; debug payload/evidence never appears in agent response'}


class PreciseFlex400Runtime:
    """Trusted simulation host. Only authenticated Session objects belong in agent tools."""
    def __init__(self):
        self.time_s = 0.0
        self._instances, self._principals, self._requests = {}, {}, {}
        self._lock = _pf400_threading.RLock()
        self._callback_active = False

    def _assert_mutable(self):
        if self._callback_active:
            raise ValueError('Host mutation is forbidden during an external callback')

    def create_instance(self, instance_id):
        with self._lock:
            self._assert_mutable()
            if not _pf400_text(instance_id) or instance_id in self._instances:
                raise ValueError('Unique nonempty instance ID required')
            state = {sid: None for sid in _pf400_slots()}
            state.update({'power.enabled': False, 'motion.status': 'uninitialized', 'sample.events': [], 'sample.container_ids': [], 'sample.receiver_port_ids': [], 'host.scene_bound': False, 'truth.registered_payloads': []})
            self._instances[instance_id] = {'state': state, 'revision': 0, 'scene_adapter': None, 'containers': {}, 'receivers': {}, 'constraints': {}, 'evidence': {}}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            self._assert_mutable()
            if not _pf400_text(principal_id) or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Unique host-assigned identity and valid visibility required')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(i, str) or i not in self._instances for i in instance_ids)):
                raise ValueError('Invalid instance scope')
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals:
            raise ValueError('Host authentication required')
        return PreciseFlex400Session(self, principal_id)

    def _get(self, iid, principal=None):
        p = self._principals.get(principal) if principal is not None else None
        if not isinstance(iid, str) or (principal is not None and (p is None or (p[1] is not None and iid not in p[1]))):
            return None
        return self._instances.get(iid)

    def _required(self, iid):
        i = self._get(iid)
        if i is None:
            raise ValueError('Known instance required')
        return i

    def host_register_scene_adapter(self, instance_id, adapter):
        with self._lock:
            self._assert_mutable()
            i = self._required(instance_id)
            if not callable(adapter) or i['scene_adapter'] is not None:
                raise ValueError('One callable scene authority per instance required')
            i['scene_adapter'] = adapter
            i['state']['host.scene_bound'] = True
            i['revision'] += 1

    def host_set_clock(self, world_time_s):
        with self._lock:
            self._assert_mutable()
            if not _pf400_number(world_time_s) or world_time_s < self.time_s:
                raise ValueError('Nondecreasing finite common world time required')
            self.time_s = float(world_time_s)

    @staticmethod
    def _pose_matches(actual, expected):
        if type(actual) is not dict or set(actual) != set(expected):
            return False
        for k, v in expected.items():
            if isinstance(v, dict):
                if not PreciseFlex400Runtime._pose_matches(actual[k], v):
                    return False
            elif not _pf400_number(actual[k]) or not _pf400_math.isclose(actual[k], v, rel_tol=0, abs_tol=2e-6 if k.endswith('_rad') else 3e-7):
                return False
        return True

    def host_commit_pose(self, instance_id, *, joints, world_time_s):
        with self._lock:
            self._assert_mutable()
            i = self._required(instance_id)
            s = i['state']
            expected = preciseflex400_forward_kinematics(joints)
            if not _pf400_number(world_time_s) or world_time_s < self.time_s or i['scene_adapter'] is None or s['motion.status'] == 'fault':
                raise ValueError('Scene authority, nondecreasing world time and reconciled state required')
            if s['motion.target_joints'] is not None and joints != s['motion.target_joints']:
                raise ValueError('Committed pose must equal the currently requested target')
            self._callback_active = True
            try:
                actual = i['scene_adapter'](instance_id, _pf400_copy.deepcopy(expected))
                confirmed = self._pose_matches(actual, expected)
            except Exception:
                confirmed = False
            finally:
                self._callback_active = False
            if not confirmed:
                s['motion.status'] = 'fault'
                i['revision'] += 1
                raise RuntimeError('Evaluated scene pose mismatch; external side effects unknown; host reconciliation required')
            s.update({'scene.joints': expected['joints'], 'scene.tcp_position_m': expected['tcp_position_m'], 'scene.tool_yaw_rad': expected['tool_yaw_rad'], 'scene.end_effector_aabb_m': expected['end_effector_aabb_m'], 'scene.sample_time_s': float(world_time_s), 'motion.status': 'ready'})
            self.time_s = float(world_time_s)
            i['revision'] += 1

    @staticmethod
    def _port(position_m, tolerance_m, evidence):
        if not _pf400_matches(position_m, _PF400_POS) or not _pf400_number(tolerance_m) or tolerance_m <= 0 or not _pf400_text(evidence):
            raise ValueError('Explicit finite position, positive geometric tolerance and evidence required')
        return {'position_m': _pf400_copy.deepcopy(position_m), 'tolerance_m': float(tolerance_m), 'evidence': evidence}

    def host_register_container(self, instance_id, container_id, *, source_port_id, position_m, tolerance_m, gross_payload_kg, evidence):
        with self._lock:
            self._assert_mutable()
            i = self._required(instance_id)
            port = self._port(position_m, tolerance_m, evidence)
            if not _pf400_text(container_id) or container_id in i['containers'] or not _pf400_text(source_port_id) or (gross_payload_kg is not None and (not _pf400_number(gross_payload_kg) or gross_payload_kg < 0)):
                raise ValueError('Unique container/source port and known nonnegative or explicitly unknown gross payload required')
            i['containers'][container_id] = {**port, 'port_id': source_port_id, 'gross_payload_kg': gross_payload_kg, 'position_confirmed': True}
            i['state']['sample.container_ids'].append(container_id)
            i['state']['truth.registered_payloads'].append({'container_id': container_id, 'gross_payload_kg': gross_payload_kg})
            i['revision'] += 1

    def host_register_receiver_port(self, instance_id, port_id, *, position_m, tolerance_m, evidence):
        with self._lock:
            self._assert_mutable()
            i = self._required(instance_id)
            port = self._port(position_m, tolerance_m, evidence)
            if not _pf400_text(port_id) or port_id in i['receivers']:
                raise ValueError('Unique receiver port required')
            i['receivers'][port_id] = port
            i['state']['sample.receiver_port_ids'].append(port_id)
            i['revision'] += 1

    def host_register_constraint(self, instance_id, constraint_id, *, container_id, adapter, evidence):
        with self._lock:
            self._assert_mutable()
            i = self._required(instance_id)
            if not _pf400_text(constraint_id) or constraint_id in i['constraints'] or not isinstance(container_id, str) or container_id not in i['containers'] or not callable(adapter) or not _pf400_text(evidence):
                raise ValueError('Unique constraint ID, registered container, callable host adapter and evidence required')
            i['constraints'][constraint_id] = {'container_id': container_id, 'adapter': adapter, 'evidence': evidence}

    def host_reconcile_fault(self, instance_id, *, attached_container_id, constraint_id, evidence, container_locations=None):
        with self._lock:
            self._assert_mutable()
            i = self._required(instance_id)
            if i['state']['motion.status'] != 'fault' or not _pf400_text(evidence):
                raise ValueError('Faulted instance and explicit external reconciliation evidence required')
            if attached_container_id is None:
                if constraint_id is not None:
                    raise ValueError('Unattached state requires null constraint')
            elif not isinstance(constraint_id, str) or constraint_id not in i['constraints'] or i['constraints'][constraint_id]['container_id'] != attached_container_id:
                raise ValueError('Reconciled attachment must reference the registered matching constraint')
            if container_locations is None:
                container_locations = []
            if not isinstance(container_locations, list):
                raise ValueError('Explicit container location records must be a list')
            updates = {}
            for item in container_locations:
                if not isinstance(item, dict) or set(item) != {'container_id', 'port_id', 'position_m', 'tolerance_m'} or not isinstance(item['container_id'], str) or item['container_id'] not in i['containers'] or item['container_id'] in updates or not _pf400_text(item['port_id']):
                    raise ValueError('Unique registered containers with explicit port and location required')
                updates[item['container_id']] = {**self._port(item['position_m'], item['tolerance_m'], evidence), 'port_id': item['port_id'], 'position_confirmed': True}
            for cid, update in updates.items():
                i['containers'][cid].update(update)
            i['state'].update({'sample.attached_container_id': attached_container_id, 'sample.constraint_id': constraint_id, 'motion.status': 'uninitialized', 'motion.target_joints': None, 'scene.joints': None, 'scene.tcp_position_m': None, 'scene.tool_yaw_rad': None, 'scene.end_effector_aabb_m': None, 'scene.sample_time_s': None})
            i['evidence']['last_reconciliation'] = evidence
            i['revision'] += 1

    def debug_snapshot(self):
        with self._lock:
            return {'time_s': self.time_s, 'instances': {iid: _pf400_copy.deepcopy({'state': i['state'], 'revision': i['revision'], 'containers': i['containers'], 'receivers': i['receivers'], 'constraints': {cid: {k: v for k, v in c.items() if k != 'adapter'} for cid, c in i['constraints'].items()}, 'evidence': i['evidence']}) for iid, i in self._instances.items()}}


class PreciseFlex400Session:
    def __init__(self, host, principal_id):
        self.__host, self.__principal = host, principal_id

    def _get(self, iid):
        return self.__host._get(iid, self.__principal)

    def _visible(self, sid):
        slots = _pf400_slots()
        return isinstance(sid, str) and sid in slots and (slots[sid]['visibility'] == 'agent' or self.__host._principals[self.__principal][0] in ('debug', 'evaluator'))

    def _response(self, iid, rid=None, status='succeeded', result=None, observations=None, code=None, side='none'):
        i = self._get(iid)
        return {'protocol_version': '1.0.0', 'instance_id': iid if isinstance(iid, str) else None, 'request_id': rid if isinstance(rid, str) else None, 'operation_id': None, 'status': status, 'state_revision': i['revision'] if i else None,
            'clock': {'domain': 'simulation', 'time_s': self.__host.time_s}, 'result': _pf400_copy.deepcopy(result or {}), 'observations': _pf400_copy.deepcopy(observations or {}),
            'error': None if code is None else {'code': code, 'message': 'Request does not satisfy declared access, input or operating conditions.', 'retryable': False, 'side_effects': side, 'progress': None}}

    def _error(self, iid, code, rid=None, status='rejected', side='none'):
        return self._response(iid, rid, status, code=code, side=side)

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or (asset_type_id is not None and asset_type_id != PRECISEFLEX400_TYPE):
                return self._error(instance_id, 'INVALID_ARGUMENT')
            if instance_id is not None and self._get(instance_id) is None:
                return self._error(instance_id, 'UNKNOWN_INSTANCE')
            if self.__host._principals[self.__principal][1] == frozenset():
                return self._error(instance_id, 'ACCESS_DENIED')
            data = export_preciseflex400_contract_metadata()
            data.pop('host_api')
            data['state_slots'] = {sid: s for sid, s in data['state_slots'].items() if self._visible(sid)}
            data.update({'protocol_version': '1.0.0', 'instance_id': instance_id, 'adapter': 'basic40_runtime_automation.PreciseFlex400Session'})
            return data

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            i = self._get(instance_id)
            if i is None:
                return self._error(instance_id, 'UNKNOWN_INSTANCE')
            if state_ids is None:
                state_ids = [sid for sid in _pf400_slots() if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(s, str) for s in state_ids) or len(set(state_ids)) != len(state_ids):
                return self._error(instance_id, 'INVALID_ARGUMENT')
            if any(not self._visible(sid) for sid in state_ids):
                return self._error(instance_id, 'ACCESS_DENIED')
            obs = {}
            for sid in state_ids:
                value = i['state'][sid]
                pose_slot = sid.startswith('scene.')
                stamp = i['state']['scene.sample_time_s'] if pose_slot else self.__host.time_s
                obs[sid] = {'value': _pf400_copy.deepcopy(value), 'unit': _pf400_slots()[sid]['unit'], 'quality': 'unavailable' if value is None else 'stale' if pose_slot and stamp != self.__host.time_s else 'estimated' if pose_slot else 'valid', 'sample_time_s': stamp, 'sample_window_s': None}
                if value is None:
                    obs[sid]['reason'] = 'No host-confirmed scene, setpoint or attachment is available.'
            return self._response(instance_id, observations=obs)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        with self.__host._lock:
            i = self._get(instance_id)
            if i is None:
                return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            if self.__host._callback_active:
                return self._error(instance_id, 'BUSY', request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not _pf400_text(request_id) or len(request_id) > 256:
                return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            try:
                digest = _pf400_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(',', ':'))
            except (TypeError, ValueError, OverflowError, RecursionError):
                return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            key = (self.__principal, instance_id, request_id)
            if key in self.__host._requests:
                old, response = self.__host._requests[key]
                return _pf400_copy.deepcopy(response) if old == digest else self._error(instance_id, 'REQUEST_CONFLICT', request_id)
            response = self._execute(instance_id, i, capability_id, arguments, request_id)
            self.__host._requests[key] = (digest, _pf400_copy.deepcopy(response))
            return response

    @staticmethod
    def _near(position, port):
        return _pf400_math.dist(tuple(position[k] for k in ('x_m', 'y_m', 'z_m')), tuple(port['position_m'][k] for k in ('x_m', 'y_m', 'z_m'))) <= port['tolerance_m']

    def _execute(self, iid, i, cid, args, rid):
        caps = export_preciseflex400_contract_metadata()['capabilities']
        if cid not in caps:
            return self._error(iid, 'UNKNOWN_CAPABILITY', rid)
        cap, s = caps[cid], i['state']
        if not _pf400_matches(args, cap['input_schema']):
            return self._error(iid, 'INVALID_ARGUMENT', rid)
        if cap['availability'] == 'unsupported':
            return self._error(iid, 'UNSUPPORTED', rid, 'unsupported')
        if cid == 'motion.forward_kinematics':
            return self._response(iid, rid, result=preciseflex400_forward_kinematics(args['joints']))
        if cid == 'state.read':
            return self._response(iid, rid, result={sid: s[sid] for sid in cap['observes']})
        if cid == 'power.set':
            s['power.enabled'] = args['enabled']
        elif cid == 'motion.set_joints':
            if not s['power.enabled'] or not s['host.scene_bound'] or s['scene.joints'] is None or s['motion.status'] == 'fault':
                return self._error(iid, 'PRECONDITION_FAILED', rid)
            s['motion.target_joints'] = {k: float(v) for k, v in args['joints'].items()}
            s['motion.status'] = 'target_pending'
        else:
            if not s['power.enabled'] or s['motion.status'] != 'ready' or s['scene.sample_time_s'] != self.__host.time_s:
                return self._error(iid, 'PRECONDITION_FAILED', rid)
            attach = cid == 'sample.attach'
            if attach:
                container = i['containers'].get(args['container_id'])
                constraint = i['constraints'].get(args['constraint_id'])
                if s['sample.attached_container_id'] is not None or container is None or not container['position_confirmed'] or constraint is None or constraint['container_id'] != args['container_id'] or container['gross_payload_kg'] is None or container['gross_payload_kg'] > .5 or not self._near(s['scene.tcp_position_m'], container):
                    return self._error(iid, 'PRECONDITION_FAILED', rid)
                container_id, constraint_id, port_id = args['container_id'], args['constraint_id'], container['port_id']
            else:
                container_id, constraint_id = s['sample.attached_container_id'], s['sample.constraint_id']
                port = i['receivers'].get(args['receiver_port_id'])
                if container_id is None or constraint_id not in i['constraints'] or port is None or not self._near(s['scene.tcp_position_m'], port):
                    return self._error(iid, 'PRECONDITION_FAILED', rid)
                constraint = i['constraints'][constraint_id]
                port_id = args['receiver_port_id']
            event = {'event': 'attach' if attach else 'release', 'container_id': container_id, 'constraint_id': constraint_id, 'port_id': port_id, 'time_s': self.__host.time_s, 'constraint_authority': 'host', 'robot': 'absent'}
            self.__host._callback_active = True
            try:
                confirmed = constraint['adapter'](iid, _pf400_copy.deepcopy(event)) is True
            except Exception:
                confirmed = False
            finally:
                self.__host._callback_active = False
            if not confirmed:
                s['motion.status'] = 'fault'
                i['containers'][container_id]['position_confirmed'] = False
                i['revision'] += 1
                return self._error(iid, 'DEVICE_ERROR', rid, 'failed', 'unknown')
            s['sample.attached_container_id'] = container_id if attach else None
            s['sample.constraint_id'] = constraint_id if attach else None
            s['sample.events'].append(event)
            if not attach:
                i['containers'][container_id].update({'position_m': _pf400_copy.deepcopy(port['position_m']), 'tolerance_m': port['tolerance_m'], 'port_id': port_id, 'position_confirmed': True})
        i['revision'] += 1
        return self._response(iid, rid, result={sid: s[sid] for sid in cap['observes']})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._get(instance_id) is None:
                return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            slots = _pf400_slots()
            if not self._visible(state_id) or slots[state_id]['write_via'] is None:
                return self._error(instance_id, 'ACCESS_DENIED', request_id)
            via = slots[state_id]['write_via']
            return self.invoke(instance_id=instance_id, capability_id=via['capability_id'], arguments={via['parameter']: value}, request_id=request_id)
# END CHEM-178 PRECISEFLEX 400 APPEND-ONLY RUNTIME

# BEGIN CHEM-174 QUANTOS FRONT PANEL
# The terminal of the Quantos (touch screen, hardware keys, SmartSens, head height handle) and the instrument functions behind its menus, from
# the Quantos operating instructions (references/manuals/mt_quantos_q2_xpe206dr/operating_manual__quantos_operating_instructions.pdf; 'p.N'
# = PDF page N): user profiles, Application Setup / User Setting / System settings, the security system, the dosing-steps data (user / sample
# ID, tolerance in %, tolerance mode, sample counter), the head RFID data (Write / Info / Copy head, Set content, Un/Lock, dose limit, expiry),
# zero, internal adjustment / test, side doors, tapper, result resolution, data output records, date / time and standby.  The original
# commands keep their schemas; their effects gain what the device does around them (tolerance follows the target in %, the head locks at
# dosing start, counters and records after a dosing, side doors must be closed to tare / dose).
import datetime as _qtp_dt
import hashlib as _qtp_hashlib
import re as _qtp_re
import tempfile as _qtp_tempfile
from pathlib import Path as _qtp_Path

QUANTOS_Q2_ASSET_DIR = _qtp_Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t5_automation/mt_quantos_q2'
QUANTOS_PROFILES = ['Home'] + ['User %d' % _n for _n in range(1, 8)]
QUANTOS_FUNCTION_KEYS = ['Start', 'Quantity', 'Tolerance', 'User ID', 'Sample ID', 'Un/Lock', 'Copy head', 'Info head', 'Write head', 'Samples',
                         'Set content', 'Right', 'Left', 'Home', 'Tapper', 'Setup', 'SafePos', 'Adjust.int', 'Adjust.ext', 'Test int', 'Test ext',
                         '1/10d', '1/100d', '1/1000d', 'Adj. History', 'Purge']                                              # p.114, p.53-54
QUANTOS_INFO_FIELDS = ['Var1', 'Var2', 'Var3', 'Var4', 'Target quantity', 'Tolerance', 'User ID', 'Sample ID', 'Substance', 'Samples',
                       'Rem. samples', 'Rem. dosages']                                                                         # p.114, p.55
QUANTOS_DOSING_STEPS = ['User ID', 'Sample ID', 'Target quantity', 'Tolerance', 'Place container', 'Lowering head', 'Unlock head']   # p.61
QUANTOS_HEAD_DEF_FIELDS = ['Substance', 'Lot ID', 'Filling date', 'Exp. date', 'Retest date reached', 'Content [mg,g]', 'Var1', 'Var2', 'Var3', 'Var4',
                           'Dose limit']                                                                                       # p.62, p.119
QUANTOS_INFO_HEAD_FIELDS = ['Substance', 'Lot ID', 'Filling date', 'Exp. date', 'Retest date reached', 'Head type', 'Head prod. date',
                            'Content [mg,g]', 'Rem. quantity', 'Rem. dosages', 'Accuracy', 'Dose limit', 'Var1', 'Var2', 'Var3', 'Var4',
                            'Type data version', 'Head ID']                                                                    # p.61, p.118
QUANTOS_LABEL_ITEMS = ['Substance', 'Sample ID', 'Lot ID', 'Content [mg]', 'Tolerance', 'User ID', 'Dispense date', 'Exp. date', 'Retest date',
                       'Balance ID', 'Var1', 'Var2', 'Var3', 'Var4', 'Title 1', 'Title 2', 'Validity', 'MinWeigh', 'Dose duration', 'Target quantity',
                       'Label index', 'Sample position', 'Head type', 'Head ID']                                               # p.115, p.56-57
QUANTOS_PROTOCOL_FRAME = ['Appl. Name', 'Title 1', 'Title 2', 'Date/Time', 'User ID', 'Balance Type', 'SNR:', 'Balance ID', 'Levelcontrol', 'Var1',
                          'Var2', 'Var3', 'Var4', 'Last cal.', 'Signature', 'Blank Line', 'Dash Line', 'Dash Line 2', '3 Blank Lines']   # p.116, p.59
QUANTOS_PROTOCOL_SINGLE = ['Header', 'Var1', 'Var2', 'Var3', 'Var4', 'Title 1', 'Title 2', 'Substance', 'Lot ID', 'Sample ID', 'Content [mg]', 'Tolerance',
                           'User ID', 'Dispense date', 'Exp. date', 'Retest date', 'Validity', 'MinWeigh', 'Dose duration', 'Target quantity',
                           'Signature', 'Head type', 'Head ID', 'Blank Line', 'Dash Line', 'Dash Line 2', '3 Blank Lines']        # p.117, p.60
QUANTOS_RIGHTS_AREAS = ['Application Setup', 'Weighing parameters', 'Adjustweight', 'User', 'User ID/Password', 'System', 'User Setting']   # p.82
QUANTOS_WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
QUANTOS_RESOLUTIONS = ['1d', '1/10d', '1/100d', '1/1000d']                                                                     # p.32
QUANTOS_RECORDS_MAX = 20
QUANTOS_ADJ_HISTORY_MAX = 50                                                                                                   # p.75
# Dosing timing behind the weighing parameters, the algorithm and the tapper (p.62, p.66-67, p.70).  The manual describes the effects but gives
# no numbers: the values are assumptions.  The ideal (noise-free) model releases the result at once with the factory weighing parameters; slower
# ones add the difference of their release time to the factory one (0.75 s), faster ones cannot beat the ideal reading.
QUANTOS_VALUE_RELEASE_S = {'Very fast': 0.25, 'Fast': 0.5, 'Reliable+Fast': 0.75, 'Reliable': 1.5, 'Very reliable': 3.0}   # stable-value release
QUANTOS_ENVIRONMENT_FACTOR = {'Very stable': 0.5, 'Stable': 0.75, 'Standard': 1.0, 'Unstable': 1.5, 'Very unstable': 2.0}   # filter: longer settling
QUANTOS_WEIGHING_MODE_FACTOR = {'Universal': 1.0, 'Dosing': 0.5, 'Sensor Mode': 1.0, 'Checkweighing': 2.0}   # Dosing reacts very quickly; Checkweighing very stable
QUANTOS_ALGO_P = (0.9, 0.25)              # Algo P (reactive): from 90 % of the target the head doses at a quarter of the rate
QUANTOS_AUTO_TAP_S = 1.0                  # 'Tapping while dosing': automatic tap (intensity / duration automatic) when the powder flow stops
QUANTOS_PROFACT_QUIET_S = 120.0           # p.36: ProFACT starts when the instrument is unloaded and no key was touched for 2 minutes
QUANTOS_LEVEL_REPEAT_S = 900.0            # p.84: Repeat: warning every 15 minutes
QUANTOS_LEVEL_OK_RADIUS = 0.25            # bubble offset (level-glass radii) still counted as level (assumption)
QUANTOS_PW_REMINDER_REPEAT_S = 10800.0    # p.83: after OK the reminder reappears every 3 hours
QUANTOS_LABEL_LAYOUTS = {1: (5, 'matrix', 'large'), 2: (5, 'matrix', 'small'), 3: (5, None, 'large'), 4: (8, 'matrix', 'small'), 5: (10, None, 'small'),
                         6: (5, None, 'small'), 7: (8, 'matrix', 'large and small'), 8: (3, 'barcode', 'small'), 9: (3, 'barcode', 'large'),
                         10: (6, 'barcode', 'small')}                  # p.58: text rows, code section, font of the 10 layout schemes


def _qtp_enum(options, factory, ev, scope='profile', group=None):
    return dict(kind='enum', options=list(options), factory=factory, evidence=ev, scope=scope, group=group)


def _qtp_bool(factory, ev, scope='profile', group=None):
    return dict(kind='bool', factory=factory, evidence=ev, scope=scope, group=group)


def _qtp_int(lo, hi, step, factory, ev, scope='profile', group=None, unit='1'):
    return dict(kind='int', min=lo, max=hi, step=step, factory=factory, evidence=ev, scope=scope, group=group, unit=unit)


def _qtp_text(max_len, factory, ev, scope='profile', group=None, nullable=False):
    return dict(kind='text', max_len=max_len, factory=factory, evidence=ev, scope=scope, group=group, nullable=nullable)


def _qtp_multi(options, factory, ev, scope='profile', group=None, max_items=None, ordered=False):
    return dict(kind='multi', options=list(options), factory=list(factory), evidence=ev, scope=scope, group=group, max_items=max_items, ordered=ordered)


_QTP_FACTORY_FK = ['Start', 'Un/Lock', 'Info head', 'Write head', 'Quantity', 'Tolerance', 'Samples']
# Profile settings (Application Setup and User Setting, stored per user profile, p.24, p.51, p.69) and system settings (p.74).
QUANTOS_SETTINGS = {
    # Application Setup
    'function_keys': _qtp_multi(QUANTOS_FUNCTION_KEYS, _QTP_FACTORY_FK, 'p.53-54, p.114 (numbers give the order; printout excerpt p.53)', group='Function Keys', ordered=True),
    'info_fields': _qtp_multi(QUANTOS_INFO_FIELDS, ['Target quantity', 'Tolerance', 'Sample ID', 'Substance'], 'p.55, p.114 (max. 4)', group='Info Field', max_items=4, ordered=True),
    'sample_label_text': _qtp_multi(QUANTOS_LABEL_ITEMS, ['Substance', 'Sample ID', 'Content [mg]', 'Dispense date', 'Exp. date'], 'p.56-57, p.115 (max. 5 with a code)', group='Data output', max_items=8, ordered=True),
    'sample_label_matrix': _qtp_multi(QUANTOS_LABEL_ITEMS, [], 'p.57 (max. 8 items; none: no code)', group='Data output', max_items=8, ordered=True),
    'sample_label_layout': _qtp_int(1, 10, 1, 1, 'p.58 (10 layout schemes)', group='Data output'),
    'sample_label_quantity': _qtp_int(1, 5, 1, 1, 'p.58 (max. 5 labels)', group='Data output'),
    'sample_label_barcode': _qtp_enum(['Substance', 'Sample ID', 'Lot ID', 'Dos. value'], 'Sample ID', 'p.58 (one item)', group='Data output'),
    'sample_label_cutter': _qtp_enum(['Samples', 'Series'], 'Samples', 'p.58-59', group='Data output'),
    'sample_protocol_header': _qtp_multi(QUANTOS_PROTOCOL_FRAME, ['Balance ID', 'Dash Line'], 'p.59-60 (factory record example)', group='Data output', ordered=True),
    'sample_protocol_single': _qtp_multi(QUANTOS_PROTOCOL_SINGLE, ['Substance', 'Sample ID', 'Content [mg]', 'Dispense date', 'Exp. date'], 'p.60 (factory record example)', group='Data output', ordered=True),
    'sample_protocol_footer': _qtp_multi(QUANTOS_PROTOCOL_FRAME, ['Dash Line', 'User ID'], 'p.60 (factory record example)', group='Data output', ordered=True),
    'sample_output_devices': _qtp_multi(['Protocol', 'Label', 'Host (XML)'], ['Protocol'], 'p.60', group='Data output'),
    'sample_output_mode': _qtp_enum(['Manual', 'Automatic'], 'Automatic', 'p.60-61 (factory: Automatic)', group='Data output'),
    'head_protocol': _qtp_multi(QUANTOS_INFO_HEAD_FIELDS, ['Substance', 'Lot ID', 'Filling date', 'Exp. date', 'Content [mg,g]'], 'p.56, p.117', group='Data output', ordered=True),
    'head_output_devices': _qtp_multi(['Protocol', 'Label', 'Host (XML)'], ['Protocol'], 'p.56, p.117', group='Data output'),
    'head_output_mode': _qtp_enum(['Manual', 'Automatic'], 'Automatic', 'p.61', group='Data output'),
    'info_head_fields': _qtp_multi(QUANTOS_INFO_HEAD_FIELDS, ['Substance', 'Lot ID', 'Filling date', 'Exp. date', 'Retest date reached', 'Head type',
                                                             'Content [mg,g]', 'Rem. quantity', 'Rem. dosages', 'Dose limit', 'Head ID'], 'p.61, p.118', group='Data output', ordered=True),
    'dosing_steps': _qtp_multi(QUANTOS_DOSING_STEPS, QUANTOS_DOSING_STEPS, 'p.21, p.61, p.118 (factory: all steps)', group='Dosing steps (solid)'),
    'tolerance_mode': _qtp_enum(['+/- Tolerance', '0/+Tolerance'], '+/- Tolerance', 'p.62', group='Tolerance Mode'),
    'head_def_data': _qtp_multi(QUANTOS_HEAD_DEF_FIELDS, ['Substance', 'Lot ID', 'Filling date', 'Exp. date', 'Retest date reached', 'Content [mg,g]', 'Var1', 'Dose limit'],
                                'p.29-31, p.62 (Var2-4 not in the default sequence)', group='Head def. data'),
    'algo': _qtp_enum(['Algo M', 'Algo P'], 'Algo M', 'p.62', group='Algo'),
    'display_unit': _qtp_enum(['g', 'mg'], 'mg', 'p.62-63 (target always in mg)', group='Display Unit'),
    'title_1': _qtp_text(20, 'T1', 'p.63', group='Titles'),
    'title_2': _qtp_text(20, 'T2', 'p.63', group='Titles'),
    'barcode': _qtp_enum(['Off', 'Host', 'Open Input'], 'Open Input', 'p.63', group='Barcode'),
    'ext_keyboard': _qtp_enum(['Off', 'Host', 'Open Input'], 'Open Input', 'p.63', group='Ext. Keyboard'),
    'smartsens_left': _qtp_enum(['Off', 'Door', 'Start', '1/10d', '1/100d', '1/1000d'], 'Off', 'p.64', group='Smart & ErgoSens'),
    'smartsens_right': _qtp_enum(['Off', 'Door', 'Start', '1/10d', '1/100d', '1/1000d'], 'Off', 'p.64', group='Smart & ErgoSens'),
    'ergosens_1': _qtp_enum(['Off', 'Door', 'Start', '1/10d', '1/100d', '1/1000d', 'AntiStatic Kit'], 'AntiStatic Kit', 'p.64', group='Smart & ErgoSens'),
    'ergosens_2': _qtp_enum(['Off', 'Door', 'Start', '1/10d', '1/100d', '1/1000d', 'AntiStatic Kit'], 'AntiStatic Kit', 'p.64', group='Smart & ErgoSens'),
    'side_doors_linked': _qtp_multi(['Dosing', 'Un/Lock', 'Int. Adj, int. Tst'], ['Dosing'], 'p.65', group='Side doors'),
    'front_door_mounted': _qtp_enum(['Mounted', 'Unmounted'], 'Mounted', 'p.65', group='Front door'),
    'front_door_linked': _qtp_multi(['Dosing', 'Un/Lock', 'MinWeigh test', 'Int. Adj, int. Tst'], ['Dosing'], 'p.65', group='Front door'),
    'tapper_intensity': _qtp_int(10, 100, 1, 50, 'p.66', group='Tapper'),
    'tapper_duration_s': _qtp_int(1, 10, 1, 1, 'p.66', group='Tapper', unit='s'),
    'tap_before_dosing': _qtp_bool(False, 'p.67', group='Tapper'),
    'tap_while_dosing': _qtp_bool(True, 'p.67', group='Tapper'),
    # User Setting
    'weighing_mode': _qtp_enum(['Universal', 'Dosing', 'Sensor Mode', 'Checkweighing'], 'Universal', 'p.70', group='Wghparam'),
    'environment': _qtp_enum(['Very stable', 'Stable', 'Standard', 'Unstable', 'Very unstable'], 'Standard', 'p.70', group='Wghparam'),
    'value_release': _qtp_enum(['Very fast', 'Fast', 'Reliable+Fast', 'Reliable', 'Very reliable'], 'Reliable+Fast', 'p.70', group='Wghparam'),
    'autozero': _qtp_bool(True, 'p.70', group='Wghparam'),
    'user_name': _qtp_text(20, None, 'p.70-71 (factory: Home, User 1 ... User 7; unique)', group='User'),
    'language': _qtp_enum(['English', 'Deutsch', 'Français'], 'English', 'p.71, p.120', group='User'),
    'doorway_percent': _qtp_enum([100, 75, 50, 25], 100, 'p.71 (side doors only)', group='Door'),
    'brightness_percent': _qtp_int(20, 100, 20, 80, 'p.72', group='Terminal', unit='%'),
    'colour_palette': _qtp_int(1, 8, 1, 1, 'p.72 (8 palettes: 4 colours, strong / soft)', group='Terminal'),
    'sound_percent': _qtp_int(0, 100, 10, 70, 'p.72', group='Terminal', unit='%'),
    'touch_function': _qtp_bool(True, 'p.72', group='Terminal'),
    'optical_key_feedback': _qtp_bool(True, 'p.72', group='Terminal'),
    'speedread': _qtp_bool(True, 'p.73', group='Terminal'),
    # System (all profiles)
    'adj_history_selection': _qtp_multi(['Manual adjust.', 'Temperature', 'Time Adjust.'], ['Manual adjust.', 'Temperature', 'Time Adjust.'], 'p.75', scope='system', group='Adjust/Test'),
    'adj_history_datasets': _qtp_enum([50, 40, 30, 20, 10], 50, 'p.75-76', scope='system', group='Adjust/Test'),
    'profact': _qtp_bool(True, 'p.76', scope='system', group='Adjust/Test'),
    'profact_weekdays': _qtp_multi(QUANTOS_WEEKDAYS, QUANTOS_WEEKDAYS, 'p.76', scope='system', group='Adjust/Test'),
    'profact_time_1': _qtp_text(5, '09:00', 'p.76 (Time 1 9:00, Time 2 / 3 Off)', scope='system', group='Adjust/Test'),
    'profact_temp_criterion': _qtp_enum(['Off', '1 Kelvin', '2 Kelvin', '3 Kelvin'], '2 Kelvin', 'p.76', scope='system', group='Adjust/Test'),
    'profact_protocol_trigger': _qtp_bool(True, 'p.76', scope='system', group='Adjust/Test'),
    'auto_ext_adjust': _qtp_bool(False, 'p.76', scope='system', group='Adjust/Test'),
    'auto_ext_test': _qtp_bool(False, 'p.77', scope='system', group='Adjust/Test'),
    'adjust_protocol': _qtp_multi(['Date/Time', 'User', 'Balance Type', 'SNR', 'SW-Version', 'Balance ID', 'Weight ID', 'Certificate No.', 'Temperature',
                                   'Nominal Weight', 'Actual Weight', 'Difference', 'Level control off', 'Signature'],
                                  ['Date/Time', 'Balance Type', 'SNR', 'Balance ID', 'Nominal Weight', 'Actual Weight', 'Difference', 'Signature'],
                                  'p.78', scope='system', group='Adjust/Test'),
    'balance_id': _qtp_text(20, None, 'p.78 (factory: the serial number)', scope='system', group='Balance Info', nullable=True),
    'standby_min': _qtp_enum(['Off', 30, 60, 120, 240], 'Off', 'p.78-79', scope='system', group='Standby'),
    'date_format': _qtp_enum(['D.MMM YYYY', 'MMM D YYYY', 'DD.MM.YYYY', 'MM/DD/YYYY'], 'D.MMM YYYY', 'p.79', scope='system', group='Date/Time'),
    'time_format': _qtp_enum(['24:MM', '12:MM', '24.MM', '12.MM'], '24:MM', 'p.79', scope='system', group='Date/Time'),
    'history_record_mode': _qtp_bool(False, 'p.83', scope='system', group='Administrator'),
    'password_change_date': _qtp_text(10, None, 'p.83 (Off or a reminder date DD.MM.YYYY)', scope='system', group='Administrator', nullable=True),
    'users_enabled': _qtp_multi(QUANTOS_PROFILES[1:], QUANTOS_PROFILES[1:], 'p.83 (Home cannot be de-selected)', scope='system', group='Administrator'),
    'levelcontrol': _qtp_bool(True, 'p.83', scope='system', group='Levelcontrol'),
    'level_warning_text': _qtp_enum(['Off', 'Once', 'Repeat'], 'Once', 'p.83-84', scope='system', group='Levelcontrol'),
    'level_warning_beep': _qtp_enum(['Off', 'Once', 'Repeat'], 'Once', 'p.83-84', scope='system', group='Levelcontrol'),
}
QUANTOS_WEIGHT_SETS = ('adjust_weights', 'test_weights')                                       # Adjustweight / Testweight 1-5 (p.77-78)


def _qtp_factory_profile(name):
    out = {k: _qt_copy.deepcopy(v['factory']) for k, v in QUANTOS_SETTINGS.items() if v['scope'] == 'profile'}
    out['user_name'] = name
    return out


def _qtp_factory_system():
    out = {k: _qt_copy.deepcopy(v['factory']) for k, v in QUANTOS_SETTINGS.items() if v['scope'] == 'system'}
    out['rights'] = {p: {a: 'No Protection' for a in QUANTOS_RIGHTS_AREAS} for p in QUANTOS_PROFILES}                    # p.35, p.82 factory
    out['history'] = []
    for key, label in (('adjust_weights', 'Adjustweight'), ('test_weights', 'Testweight')):
        out[key] = [{'weight_g': None, 'id': '%s %d' % (label, n), 'certificate': None} for n in range(1, 6)]           # weight model-dependent
    return out


def _qtp_factory_credentials():
    out = {'admin': {'id': 'Z', 'password': 'Z'}}                                                                        # p.81
    out.update({p: {'id': str(n), 'password': str(n)} for n, p in enumerate(QUANTOS_PROFILES)})                           # p.71: Home 0, User n n
    return out


def _qtp_check_setting(key, value, current_names=None):
    """Value check of one setting (kind, range, options, order); returns the stored value or raises ValueError."""
    d = QUANTOS_SETTINGS[key]
    k = d['kind']
    if k == 'enum':
        if value not in d['options'] or isinstance(value, bool):
            raise ValueError(key)
        return value
    if k == 'bool':
        if not isinstance(value, bool):
            raise ValueError(key)
        return value
    if k == 'int':
        if isinstance(value, bool) or not isinstance(value, int) or not d['min'] <= value <= d['max'] or (value - d['min']) % d['step']:
            raise ValueError(key)
        return value
    if k == 'text':
        if value is None and d.get('nullable'):
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > d['max_len']:
            raise ValueError(key)
        if key == 'profact_time_1' and not _qtp_re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError(key)
        if key == 'password_change_date' and _qtp_parse_date(value) is None:
            raise ValueError(key)
        if key == 'user_name' and current_names is not None and value in current_names:
            raise ValueError(key)                                                                   # p.71: a name already in use is refused
        return value
    if k == 'multi':
        if not isinstance(value, list) or any(v not in d['options'] for v in value) or len(set(value)) != len(value):
            raise ValueError(key)
        if d.get('max_items') is not None and len(value) > d['max_items']:
            raise ValueError(key)
        return list(value) if d.get('ordered') else [o for o in d['options'] if o in value]
    raise ValueError(key)


def _qtp_parse_date(text):
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return _qtp_dt.datetime.strptime(text, fmt).date()
        except (TypeError, ValueError):
            pass
    return None


def _qtp_weight_entry(v):
    if not isinstance(v, dict) or set(v) != {'weight_g', 'id', 'certificate'}:
        raise ValueError('weight')
    if v['weight_g'] is not None and (not _qt_number(v['weight_g']) or v['weight_g'] <= 0):
        raise ValueError('weight')
    for f in ('id', 'certificate'):
        if v[f] is not None and (not isinstance(v[f], str) or len(v[f]) > 20):
            raise ValueError('weight')
    return dict(v)


def _qtp_default_head(head_id):
    """RFID data of a head that has not been written yet: factory dose limit 250 (p.30), the rest empty (question marks on the screen)."""
    return {'head_id': head_id, 'head_type': 'powder', 'substance': None, 'lot_id': None, 'filling_date': None, 'exp_date': None, 'retest_date': None,
            'content_kg': None, 'var': [{'name': 'Var%d' % n, 'value': None} for n in range(1, 5)], 'dose_limit': 250, 'remaining_dosages': 250,
            'remaining_quantity_kg': None, 'written': False}


_QTP_NSTR20 = {'anyOf': [{'type': 'string', 'minLength': 1, 'maxLength': 20}, {'type': 'null'}]}
_QTP_SLOTS = {
    'profile.active': _qt_slot({'type': 'string', 'enum': list(QUANTOS_PROFILES)}, '1', 'setpoint', 'profile.select', 'profile'),
    'profile.names': _qt_slot({'type': 'array', 'items': {'type': 'string'}, 'minItems': 8, 'maxItems': 8}, '1', 'setpoint'),
    'settings.profile': _qt_slot({'type': 'object'}, '1', 'setpoint'),
    'settings.system': _qt_slot({'type': 'object'}, '1', 'setpoint'),
    'dosing.user_id': _qt_slot(_QTP_NSTR20, '1', 'setpoint'),
    'dosing.sample_id': _qt_slot(_QTP_NSTR20, '1', 'setpoint'),
    'dosing.tolerance_percent': _qt_slot(_qt_num('%', 0.001, 100, True), '%', 'setpoint', 'dosing.set_tolerance_percent', 'percent'),
    'dosing.samples_total': _qt_slot({'type': 'integer', 'minimum': 0, 'maximum': 1000, 'x-unit': '1'}, '1', 'setpoint', 'dosing.set_samples', 'count'),
    'dosing.samples_remaining': _qt_slot({'type': 'integer', 'minimum': 0, 'maximum': 1000, 'x-unit': '1'}, '1', 'derived'),
    'dosing.last_result': _qt_slot({'anyOf': [{'type': 'object'}, {'type': 'null'}]}, '1', 'derived'),
    'head.locked': _qt_slot(_QT_BOOL, '1', 'derived'),
    'head.data': _qt_slot({'anyOf': [{'type': 'object'}, {'type': 'null'}]}, '1', 'setpoint'),
    'head.content_buffer_kg': _qt_slot(_qt_num('kg', nullable=True), 'kg', 'derived'),
    'head.copy_memory': _qt_slot(_QT_BOOL, '1', 'derived'),
    'head.position': _qt_slot({'type': 'string', 'enum': ['raised', 'lowered']}, '1', 'setpoint', 'head.set_height', 'position'),
    'door.side_opening_fraction': _qt_slot(_qt_num('1', 0, 1), '1', 'setpoint'),
    'balance.net_mode': _qt_slot(_QT_BOOL, '1', 'derived'),
    'balance.adjustment_history': _qt_slot({'type': 'array', 'items': {'type': 'object'}, 'maxItems': QUANTOS_ADJ_HISTORY_MAX}, '1', 'derived'),
    'display.resolution': _qt_slot({'type': 'string', 'enum': list(QUANTOS_RESOLUTIONS)}, '1', 'setpoint', 'display.set_resolution', 'resolution'),
    'tapper.taps': _qt_slot({'type': 'integer', 'minimum': 0, 'x-unit': '1'}, '1', 'derived'),
    'tapper.last_tap_s': _qt_slot(_qt_num('s', nullable=True), 's', 'derived'),
    'tapper.last_tap': _qt_slot({'anyOf': [{'type': 'object'}, {'type': 'null'}]}, '1', 'derived'),
    'dosing.phase': _qt_slot({'anyOf': [{'type': 'string', 'enum': ['tapping', 'dosing', 'settling']}, {'type': 'null'}]}, '1', 'derived'),
    'balance.level_ok': _qt_slot({'anyOf': [_QT_BOOL, {'type': 'null'}]}, '1', 'derived'),
    'balance.adjust_pending': _qt_slot(_QT_BOOL, '1', 'derived'),
    'output.records': _qt_slot({'type': 'array', 'items': {'type': 'object'}, 'maxItems': QUANTOS_RECORDS_MAX}, '1', 'derived'),
    'clock.datetime': _qt_slot({'anyOf': [{'type': 'string'}, {'type': 'null'}]}, '1', 'derived'),
    'clock.date_text': _qt_slot({'anyOf': [{'type': 'string'}, {'type': 'null'}]}, '1', 'derived'),
    'clock.time_text': _qt_slot({'anyOf': [{'type': 'string'}, {'type': 'null'}]}, '1', 'derived'),
    'security.credentials': _qt_slot({'type': 'object'}, '1', 'setpoint', visibility='debug'),
    'device.error': _qt_slot({'anyOf': [{'type': 'string', 'enum': ['No. 0', 'No. 2', 'No. 3', 'No. 4', 'No. 5', 'Overload', 'Underload']}, {'type': 'null'}]},
                             '1', 'derived'),
}
_QT_SLOTS.update(_QTP_SLOTS)
# Documented errors of the operating instructions (p.89-92) injected by the evaluator (fault.inject / fault.clear): code -> (message, page, what
# the instrument refuses while the cause persists).  The cause is a hardware state (wiring, clogged head mechanism, a load beyond the weighing
# range) that the ideal model does not produce by itself.
QUANTOS_FAULTS = {
    'No. 0': ('Timeout', 'p.89 (communication between balance, dosing unit and terminal; check the wiring)'),
    'No. 2': ('Dosing head actuator blocked', 'p.90 (head clogged or blocked mechanism; tap the head, other powder settings, new head)'),
    'No. 3': ('Dosing head actuator blocked', 'p.90'), 'No. 4': ('Dosing head actuator blocked', 'p.90'), 'No. 5': ('Dosing head actuator blocked', 'p.90'),
    'Overload': ('Overload', 'p.92 (the weight on the pan exceeds the weighing capacity; reduce the weight)'),
    'Underload': ('Underload', 'p.92 (check that the weighing pan is positioned correctly)')}
_QTP_FAULT_REFUSED = ('dosing.start', 'balance.tare', 'balance.zero', 'balance.adjust_internal', 'balance.test_internal', 'tapper.tap', 'head.set_content')
# Commands that would change another caller's running dosing (lead rule 2026-10-19: refused ACCESS_DENIED, with dosing.abort and a power-off).
_QTP_RUN_CHANGES = ('dosing.set_target', 'dosing.set_tolerance', 'dosing.set_tolerance_percent', 'dosing.set_samples', 'dosing.set_ids', 'head.select',
                    'head.set_lock', 'head.write_data', 'head.copy_data', 'container.select_position', 'tapper.tap', 'profile.select', 'settings.set_profile',
                    'settings.user_reset', 'settings.master_reset', 'balance.tare', 'balance.zero', 'balance.adjust_internal', 'balance.test_internal')
_QTP_PROFILE_SCHEMA_VALUE = {'anyOf': [{'type': 'string', 'maxLength': 20}, {'type': 'integer', 'x-unit': '1'}, {'type': 'boolean'},
                                       {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 30}, {'type': 'null'}]}
_QTP_SYSTEM_VALUE = {'anyOf': [{'type': 'string', 'maxLength': 20}, {'type': 'integer', 'x-unit': '1'}, {'type': 'boolean'},
                               {'type': 'array', 'items': {'anyOf': [{'type': 'string'}, {'type': 'object'}]}, 'maxItems': 7}, {'type': 'object'}, {'type': 'null'}]}
_QTP_HEAD_DATA_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'substance': {'anyOf': [{'type': 'string', 'minLength': 1, 'maxLength': 20}, {'type': 'null'}]},
    'lot_id': {'anyOf': [{'type': 'string', 'minLength': 1, 'maxLength': 15}, {'type': 'null'}]},
    'filling_date': {'anyOf': [{'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}, {'type': 'null'}]},
    'exp_date': {'anyOf': [{'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}, {'type': 'null'}]},
    'retest_date': {'anyOf': [{'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}, {'type': 'null'}]},
    'content_kg': {'anyOf': [{'type': 'number', 'x-unit': 'kg', 'minimum': 0, 'maximum': 1}, {'type': 'null'}]},
    'var': {'type': 'array', 'minItems': 4, 'maxItems': 4, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['name', 'value'], 'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 10}, 'value': {'anyOf': [{'type': 'string', 'maxLength': 15}, {'type': 'null'}]}}}},
    'dose_limit': {'type': 'integer', 'minimum': 1, 'maximum': 999, 'x-unit': '1'}}, 'required': []}


def _qtp_valid(schema, value):
    """JSON-schema subset used by the front-panel commands (type, enum, bounds, anyOf, objects, arrays, strings)."""
    if 'anyOf' in schema:
        return any(_qtp_valid(s, value) for s in schema['anyOf'])
    t = schema.get('type')
    if t == 'null':
        return value is None
    if t == 'boolean':
        ok = isinstance(value, bool)
    elif t == 'integer':
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif t == 'number':
        ok = _qt_number(value)
    elif t == 'string':
        ok = isinstance(value, str) and schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 10 ** 9) and \
            ('pattern' not in schema or _qtp_re.search(schema['pattern'], value) is not None)
    elif t == 'array':
        ok = isinstance(value, list) and schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10 ** 9) and \
            all(_qtp_valid(schema.get('items', {}), v) for v in value)
    elif t == 'object':
        props = schema.get('properties', {})
        ok = isinstance(value, dict) and all(k in value for k in schema.get('required', [])) and \
            (schema.get('additionalProperties', True) is not False or all(k in props for k in value)) and all(_qtp_valid(props[k], v) for k, v in value.items() if k in props)
    else:
        ok = True
    if ok and 'enum' in schema:
        ok = value in schema['enum']
    if ok and t in ('integer', 'number'):
        ok = schema.get('minimum', -float('inf')) <= value <= schema.get('maximum', float('inf'))
    return ok


def _qtp_cap_specs():
    """New commands: (input props, effects, observes, example, components, preconditions, description, kind)."""
    S, B = {'type': 'string', 'minLength': 1, 'maxLength': 20}, _QT_BOOL
    prof = {'type': 'string', 'enum': list(QUANTOS_PROFILES)}
    profile_keys = sorted(k for k, v in QUANTOS_SETTINGS.items() if v['scope'] == 'profile')
    system_keys = sorted([k for k, v in QUANTOS_SETTINGS.items() if v['scope'] == 'system'] + ['rights', 'adjust_weights', 'test_weights'])
    head_fx = ['head.data', 'head.locked']
    return {
        'profile.select': ({'profile': prof}, ['profile.active', 'settings.profile', 'measurement.out_of_tolerance'], ['profile.active'], {'profile': 'User 1'},
                           ['terminal'], ['上电；只能选系统设置“Number of users”中启用的配置（Home总可选）。'], '选择用户配置（Home、User 1-7），其应用与用户设置随之生效。', 'configuration'),
        'settings.set_profile': ({'key': {'type': 'string', 'enum': profile_keys}, 'value': _QTP_PROFILE_SCHEMA_VALUE},
                                 ['settings.profile', 'profile.names', 'measurement.out_of_tolerance'], ['settings.profile'], {'key': 'tolerance_mode', 'value': '0/+Tolerance'},
                                 ['terminal'], ['上电；值须合乎该设置的选项、范围与顺序规则（configuration.settings）。'],
                                 '修改当前用户配置的一项应用设置（Application Setup）或用户设置（User Setting）；经主机命令修改不受密码保护（手册p.81）。', 'configuration'),
        'settings.set_system': ({'key': {'type': 'string', 'enum': system_keys}, 'value': _QTP_SYSTEM_VALUE}, ['settings.system', 'clock.date_text', 'clock.time_text'],
                                ['settings.system'], {'key': 'standby_min', 'value': 30}, ['terminal'], ['上电；值合乎该设置规则。'], '修改一项系统设置（所有用户配置共用）。', 'configuration'),
        'settings.set_credentials': ({'level': {'type': 'string', 'enum': ['admin', 'user']}, 'id': S, 'password': S}, ['security.credentials'], [], {'level': 'user', 'id': 'lab1', 'password': 'pw1'},
                                     ['terminal'], ['上电；ID与密码都必须给出（手册p.71、p.82）。'], '设置管理员或当前用户配置的ID与密码（只写入，不回读）。', 'configuration'),
        'security.verify': ({'level': {'type': 'string', 'enum': ['admin', 'user']}, 'id': S, 'password': S}, [], [], {'level': 'admin', 'id': 'Z', 'password': 'Z'},
                            ['terminal'], ['上电。用户级受保护区也接受管理员代码（p.82）。'], '核对受保护菜单区的ID与密码（终端操作的安全系统）；错误返回ACCESS_DENIED。', 'query'),
        'settings.user_reset': ({}, ['settings.profile', 'profile.names', 'security.credentials', 'measurement.out_of_tolerance'], ['settings.profile'], {}, ['terminal'], ['上电、空闲。'],
                                '当前用户配置的全部设置（含用户ID与密码）恢复出厂（p.73）。', 'device_command'),
        'settings.master_reset': ({}, ['settings.profile', 'settings.system', 'profile.names', 'profile.active', 'security.credentials', 'display.resolution',
                                       'clock.date_text', 'clock.time_text', 'measurement.out_of_tolerance'], ['settings.system'], {}, ['terminal'], ['上电、空闲。'],
                                  '全部仪器设置恢复出厂，校正记录与日期时间保留（p.82）。', 'device_command'),
        'dosing.set_ids': ({'user_id': _QTP_NSTR20, 'sample_id': _QTP_NSTR20}, ['dosing.user_id', 'dosing.sample_id'], ['dosing.user_id', 'dosing.sample_id'],
                           {'user_id': 'TRPF', 'sample_id': 'XK-414'}, ['terminal'], ['上电、空闲。'], '设置投料步骤中的User ID与Sample ID（各最多20字符，Sample ID不检查唯一，p.22）。', 'configuration'),
        'dosing.set_tolerance_percent': ({'percent': _qt_num('%', 0.001, 100)}, ['dosing.tolerance_percent', 'dosing.tolerance_kg'] + _QT_MEASURE_IDS, ['dosing.tolerance_percent', 'dosing.tolerance_kg'],
                                         {'percent': 1.0}, ['terminal'], ['上电、空闲。'], '以目标量百分数设置允许偏差（投料步骤“Tolerance”，p.22）；目标改变时绝对偏差随之换算。', 'configuration'),
        'dosing.set_samples': ({'count': {'type': 'integer', 'minimum': 0, 'maximum': 1000, 'x-unit': '1'}}, ['dosing.samples_total', 'dosing.samples_remaining'],
                               ['dosing.samples_total', 'dosing.samples_remaining'], {'count': 10}, ['terminal'], ['上电、空闲。'], '样品计数器：0关闭，1-1000为要投料的样品数（p.32）。', 'configuration'),
        'dosing.abort': ({}, ['dosing.status', 'dosing.last_result', 'head.locked', 'dosing.phase'], ['dosing.status'], {}, ['terminal', 'dosing_head'],
                         ['有进行中的投料，且由本调用者启动（他人的投料返回ACCESS_DENIED；评估者不受限）。'],
                         '终端上的[C]：中止调用者自己的当前投料，保留已转移的粉末（p.21）。', 'device_command'),
        'head.set_lock': ({'locked': B}, ['head.locked', 'scene.door_opening_m', 'door.side_opening_fraction', 'balance.tared'], ['head.locked'], {'locked': False}, ['dosing_head', 'head_slot'],
                          ['上电、空闲、已装头。'], '锁定或释放投料头（[Un/Lock]，p.22、p.32）；开始投料或写头数据时自动锁定。', 'device_command'),
        'head.write_data': ({'data': _QTP_HEAD_DATA_SCHEMA}, head_fx, ['head.data'], {'data': {'substance': 'Glucose', 'lot_id': 'L-1', 'exp_date': '2027-08-21'}},
                            ['dosing_head', 'terminal'], ['上电、空闲、已装头。'], '写入投料头RFID的用户数据（[Write head]，p.29-31）；同时锁定头。剂量限值只能在头首次使用前修改。', 'device_command'),
        'head.set_content': ({}, ['head.content_buffer_kg'], ['head.content_buffer_kg'], {}, ['balance', 'terminal'], ['上电、空闲、有去皮后的净重读数。'],
                             '[Set content]：暂存当前净重作为写头时的Content（p.28-29）。', 'device_command'),
        'head.copy_data': ({'action': {'type': 'string', 'enum': ['read', 'write']}}, ['head.copy_memory', 'head.data', 'head.locked'], ['head.copy_memory'], {'action': 'read'},
                           ['dosing_head', 'terminal'], ['上电、空闲、已装头；write需先read且为另一只头。'], '[Copy head]：读入源头的用户与内部数据，再写到新头（p.31）。', 'device_command'),
        'head.set_height': ({'position': {'type': 'string', 'enum': ['raised', 'lowered']}}, ['head.position'], ['head.position'], {'position': 'lowered'}, ['head_slot'],
                            ['空闲。'], '头高度调节手柄：降低到容器上方约0.5-1 mm或抬起（p.23）；无SafePos时仪器不检测高度。', 'device_command'),
        'balance.zero': ({}, ['balance.tared', 'balance.net_mode'] + _QT_MEASURE_IDS, ['balance.tared', 'measurement.balance_net_kg'], {}, ['balance'],
                         ['上电、空闲、门关；容器位置已选；理想观测模型已配置。'], '置零：新的零点，皮重为0，净重=毛重=0（p.13）。', 'device_command'),
        'balance.adjust_internal': ({}, ['balance.adjustment_history', 'balance.tared'] + _QT_MEASURE_IDS, ['balance.adjustment_history'], {}, ['balance'], ['上电、空闲、门关。'],
                                    '[Adjust.int]：用内置砝码校正，记入校正历史（p.36-37）；理想天平无校正误差。', 'device_command'),
        'balance.test_internal': ({}, ['balance.adjustment_history'], ['balance.adjustment_history'], {}, ['balance'], ['上电、空闲、门关。'],
                                  '[Test int]：用内置砝码检查校正（p.37），记入历史。', 'device_command'),
        'door.set_side': ({'open': B}, ['door.side_opening_fraction', 'balance.tared'] + _QT_MEASURE_IDS, ['door.side_opening_fraction'], {'open': True},
                          ['side_door_left', 'side_door_right'], ['空闲。'],
                          '左右侧门开/关（侧门键，内部电机同时驱动两扇），开启宽度按用户设置Doorway（p.25、p.71）：两扇侧门沿+Y后滑Doorway×0.16 m（行程为图纸估计）。', 'device_command'),
        'tapper.tap': ({}, ['tapper.taps', 'tapper.last_tap_s', 'tapper.last_tap'], ['tapper.taps', 'tapper.last_tap'], {}, ['dosing_head'], ['上电、空闲、已装头。'],
                       '[Tapper]：按设定强度与时长手动拍打一次并记录（p.66-67）；没有粉末流动模型，强度不改变出粉。', 'device_command'),
        'display.set_resolution': ({'resolution': {'type': 'string', 'enum': list(QUANTOS_RESOLUTIONS)}}, ['display.resolution'], ['display.resolution'], {'resolution': '1/10d'},
                                   ['terminal'], ['上电。'], '结果显示分辨率1d、1/10d、1/100d、1/1000d（p.32）。', 'configuration'),
        'output.print': ({'content': {'type': 'string', 'enum': ['sample', 'head', 'settings', 'adjustments', 'weight']}}, ['output.records'], ['output.records'], {'content': 'sample'},
                         ['terminal'], ['上电。'], '打印键：把结果、头数据、设置或校正历史作为记录送出（p.13、p.53）；记录保存在output.records（打印机本身不建模）。', 'device_command'),
        'clock.set': ({'date': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}, 'time': {'type': 'string', 'pattern': r'^\d{2}:\d{2}(:\d{2})?$'}},
                      ['clock.datetime', 'clock.date_text', 'clock.time_text'], ['clock.datetime'], {'date': '2026-10-19', 'time': '09:30:00'}, ['terminal'], ['上电。'],
                      '设置日期与时间（p.79）；之后随仿真时钟走。', 'configuration'),
        'fault.inject': ({'code': {'type': 'string', 'enum': list(QUANTOS_FAULTS)}}, ['device.error', 'dosing.status', 'dosing.last_result', 'head.locked', 'dosing.phase'],
                         ['device.error'], {'code': 'No. 2'}, ['terminal', 'dosing_head', 'balance'], ['调用者为evaluator/debug身份；当前无注入的故障。'],
                         '评测者：注入操作说明书p.89-92的错误（Timeout No. 0、Dosing head actuator blocked No. 2-5、Overload、Underload）：进行中的投料停止（failed），'
                         '终端显示手册的错误消息（Overload/Underload显示在称量值处），投料、去皮、置零、校正与拍打被拒，直到fault.clear；用于测试视觉agent对故障画面的处理。',
                         'simulation'),
        'fault.clear': ({}, ['device.error'], ['device.error'], {}, ['terminal'], ['调用者为evaluator/debug身份。'],
                        '评测者：故障原因已排除（检查接线、清理或更换投料头、减载或放正秤盘，手册p.89-92）：错误状态清除。', 'simulation'),
    }


_qtp_export_v1 = export_quantos_q2_contract_metadata
_QTP_PANEL = {}


def export_quantos_q2_contract_metadata(panel=False):
    """Type metadata with the terminal's instrument functions; panel=True adds the panel.* capabilities (separate group).  The runtime's own
    calls use panel=False, so a missing or broken panel spec never stops the device commands."""
    meta = _qtp_export_v1()
    caps, slots = meta['capabilities'], meta['state_slots']
    template = caps['dosing.set_target']
    extra = {'dosing.set_target': ['dosing.tolerance_kg'], 'dosing.set_tolerance': ['dosing.tolerance_percent'], 'balance.tare': ['balance.net_mode'],
             'dosing.start': ['head.locked', 'head.data', 'tapper.taps', 'tapper.last_tap_s', 'tapper.last_tap', 'dosing.last_result', 'dosing.phase'],
             'power.set': ['head.locked', 'dosing.last_result', 'dosing.samples_remaining', 'dosing.samples_total', 'head.data', 'output.records'],
             'dosing.cancel': ['head.locked', 'dosing.last_result'], 'door.set_opening': []}
    for cid, sids in extra.items():
        have = {e['state_id'] for e in caps[cid]['state_effects']}
        caps[cid]['state_effects'] += [{'state_id': s, 'timing': 'immediate', 'description_zh': '仪器在该命令周围自动完成的动作（见panel_ui与运行层说明）。'} for s in sids if s not in have]
    caps['dosing.start']['state_effects'] += [{'state_id': s, 'timing': 'on_step', 'description_zh': '投料结束后：头剩余量与计数、样品计数、自动记录、按投料步骤自动解锁头。'}
                                              for s in ('head.data', 'head.locked', 'dosing.samples_remaining', 'dosing.samples_total', 'output.records', 'dosing.last_result')]
    caps['dosing.start']['state_effects'] += [{'state_id': s, 'timing': 'on_step', 'description_zh':
                                               '投料阶段：投料前拍打（tapping）、出粉（dosing，Algo P最后10 %降速）、到量后按称量参数稳定（settling）；出粉停止且开启“Tapping while dosing”时先自动拍打再报失败。'}
                                              for s in ('dosing.phase', 'tapper.taps', 'tapper.last_tap', 'tapper.last_tap_s')]
    for cid in ('dosing.cancel', 'power.set'):
        caps[cid]['state_effects'] += [{'state_id': 'dosing.phase', 'timing': 'immediate', 'description_zh': '投料结束时清空。'}]
    caps['dosing.start']['preconditions_zh'] = list(caps['dosing.start']['preconditions_zh']) + [
        '侧门关闭；头剩余投料次数大于0；若已设日期与有效期，未过有效期（p.29、p.91）。']
    caps['balance.tare']['preconditions_zh'] = list(caps['balance.tare']['preconditions_zh']) + ['侧门关闭。']
    for cid, (props, effects, observed, example, components, prereq, desc, kind) in _qtp_cap_specs().items():
        cap = _qt_copy.deepcopy(template)
        cap.update({'name_zh': desc, 'description_zh': desc, 'kind': kind, 'visibility': 'agent', 'availability': 'implemented', 'component_ids': components,
                    'input_schema': _qt_obj(_qt_copy.deepcopy(props)), 'output_schema': _qt_obj({sid: _qt_copy.deepcopy(slots[sid]['value_schema']) for sid in observed}),
                    'observes': observed, 'preconditions_zh': prereq, 'example_arguments': [_qt_copy.deepcopy(example)],
                    'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': '同步更新本次声明的状态。'} for sid in dict.fromkeys(effects)],
                    'binding': 'basic40_runtime_automation.QuantosQ2Session.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
                    'implementation': {'process': 'implemented', 'scene': 'not_applicable', 'host': 'implemented', 'hardware': 'absent', 'robot': 'absent'},
                    'model_effects': {'mode': 'none', 'binding_ids': [], 'description_zh': '只改变运行层状态；终端画面由panel.capture渲染。'},
                    'execution_notes': {'mode': 'synchronous', 'time_domain': 'simulation', 'cancellable': False, 'completion_zh': '返回succeeded表示设置或命令已提交。',
                                        'partial_failure_zh': '拒绝时不改变仪器状态。', 'retry_zh': '当前内存中相同请求ID及参数返回原响应；不同参数拒绝。'}})
        caps[cid] = cap
    meta['configuration'] = {'settings': {k: {kk: vv for kk, vv in v.items() if kk != 'evidence'} for k, v in QUANTOS_SETTINGS.items()},
                             'weight_sets': list(QUANTOS_WEIGHT_SETS), 'rights_areas': list(QUANTOS_RIGHTS_AREAS), 'profiles': list(QUANTOS_PROFILES),
                             'factory_credentials': 'admin Z / Z; profile n: n / n (p.71, p.81)'}
    meta['limitations'] = list(meta['limitations']) + [
        '终端（触摸屏、硬件键、SmartSens、头高度手柄）按操作说明书实现：用户配置、Application Setup / User Setting / System设置、安全系统、投料步骤数据、头RFID数据、置零、内置校正/测试、侧门、拍打器、分辨率、数据输出记录、日期时间与待机。',
        '投料时序（手册只描述效果，数值为假设）：到量后按称量参数稳定再给出结果，释放时间=Value Release（Very fast 0.25 s … Reliable+Fast 0.75 s … Very reliable 3 s）'
        '×环境（Very stable 0.5 … Standard 1 … Very unstable 2）×称量模式（Dosing 0.5、Universal/Sensor 1、Checkweighing 2）；理想无噪声读数在出厂参数下立即给出结果，'
        '较慢的参数增加与出厂值（0.75 s）之差的稳定时间，较快的参数不能快于理想读数；Algo P从目标的90 %起以1/4速率出粉（Algo M为原恒速）；'
        '投料前拍打按设定时长暂停出粉；出粉停止时若开启“Tapping while dosing”先自动拍打1 s再报失败。投料结果的Dose duration含拍打与稳定时间，dosing.elapsed_s只计出粉时间。',
        '理想天平：读数无噪声与漂移，故AutoZero无可校正的零点漂移（不建模零点漂移）；拍打强度只记入拍打记录（没有粉末流动模型）；水平传感器只报告水泡位置（host_set_level），'
        '不水平不改变称量值（无余弦误差模型）。ProFACT按时间准则（Time 1、星期）在空载、空闲、2分钟无按键时自动内部校正并按Protocol Trigger打印报告；温度准则与上电24 h内的额外校正没有温度/上电历史模型；'
        '外部砝码校正/测试、自动进样器、液体模块、SafePos、MinWeigh、外设接口与以太网属服务或缺失硬件。',
        '面板手势按发出手势的会话身份执行：终端启动的投料属于该会话（poll/cancel/[C]）；直接调用启动的投料也使终端显示投料画面。投料进行中，其他会话的[C]/dosing.abort、关机'
        '（On/Off或power.set）与改变该投料的命令（目标、偏差、样品数、ID、头、容器、配置设置、去皮/置零/校正、拍打）在面板与直接路径都被拒（ACCESS_DENIED，状态不变），'
        '终端显示提示窗（文字为假设，仪器没有调用者概念）；他人用operation_id的poll/cancel仍为UNKNOWN_OPERATION（操作号按调用者区分）；评估者不受限。',
        '错误（操作说明书p.89-92）：Timeout No. 0、Dosing head actuator blocked No. 2-5、Overload、Underload由评测者fault.inject注入、fault.clear清除：进行中的投料停止（failed），'
        '终端显示手册的错误消息（OK确认后消息关闭，故障仍在），Overload/Underload显示在称量值处；故障期间投料、去皮、置零、内置校正/测试、拍打与Set content被拒（PRECONDITION_FAILED）；'
        '故障原因（接线、投料头堵塞、超出称量范围）不作物理仿真；需要缺失选件的错误（No. 8、13、16、27、液体流量）与其它头型仍不支持。',
        '记录（打印、标签、Host XML）保存在output.records，打印机、标签打印机与主机链路不建模；日期在设置前未知，有效期与复检日期只在日期已知时检查。']
    meta['configuration']['dosing_timing'] = {'value_release_s': dict(QUANTOS_VALUE_RELEASE_S), 'environment_factor': dict(QUANTOS_ENVIRONMENT_FACTOR),
                                              'weighing_mode_factor': dict(QUANTOS_WEIGHING_MODE_FACTOR), 'algo_p_from_fraction': QUANTOS_ALGO_P[0],
                                              'algo_p_rate_factor': QUANTOS_ALGO_P[1], 'auto_tap_s': QUANTOS_AUTO_TAP_S, 'evidence': 'assumption (manual p.62, p.66-67, p.70 describe the effects only)'}
    meta['configuration']['label_layouts'] = {str(k): {'text_rows': v[0], 'code': v[1], 'font': v[2]} for k, v in QUANTOS_LABEL_LAYOUTS.items()}
    meta['host_api']['host_set_level'] = {'entrypoint': 'QuantosQ2Runtime.host_set_level',
                                          'description_zh': '宿主（场景）给出水平泡位置（以水平玻璃半径计，-1..1；0,0为水平）与依据；水平传感器据此报告balance.level_ok。'}
    for cid in ('fault.inject', 'fault.clear'):
        caps[cid]['visibility'] = 'evaluator'
    meta['asset_version'] = '1.2.2'   # 1.2.1 (2026-10-22): dose residue below the numerical resolution ends the dosing; the clock always advances
    #                                   1.2.2 (2026-10-23): real front door (lever-lifted), left / right side doors with travel, open dosing head support
    caps['door.set_side']['model_effects'] = {'mode': 'coupled', 'binding_ids': ['side_door_left_slide', 'side_door_right_slide'],
                                              'description_zh': '两扇侧门的平移关节跟随door.side_opening_fraction（宿主镜像，apply_quantos_q2_scene_patch：位置=比例×0.16 m）。'}
    if panel:
        meta['panel_capabilities'] = _qtp_panel_caps()
    return meta


def _qtp_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _QTP_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if '_chemsim_panel_ui' not in sys.modules:
                spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', QUANTOS_Q2_ASSET_DIR.parents[3] / 'scripts/asset_runtime/_panel_ui.py')
                sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
            engine = sys.modules['_chemsim_panel_ui']
        caps = set(export_quantos_q2_contract_metadata(panel=False)['capabilities'])
        spec = engine.load_spec(QUANTOS_Q2_ASSET_DIR / 'panel_ui.json', capabilities=caps)
        _QTP_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _QTP_PANEL


def _qtp_panel_caps():
    out = {}
    for cid, d in _qtp_panel()['caps'].items():
        cap = _qt_copy.deepcopy(d)
        cap.update({'name_zh': d['function_zh'], 'description_zh': d['function_zh'], 'availability': 'implemented', 'observes': [], 'state_effects': [],
                    'preconditions_zh': [], 'example_arguments': [_qt_copy.deepcopy(d['example_arguments'])],
                    'binding': 'basic40_runtime_automation.QuantosQ2Session.invoke(instance_id=..., capability_id=%r, arguments=..., request_id=...)' % cid,
                    'implementation': {'process': 'implemented', 'scene': 'not_applicable', 'host': 'implemented', 'hardware': 'absent', 'robot': 'absent'},
                    'model_effects': {'mode': 'none', 'binding_ids': [], 'description_zh': '面板手势调用同一运行层的能力；画面由panel.capture渲染。'},
                    'execution_notes': {'mode': 'synchronous', 'time_domain': 'simulation', 'cancellable': False, 'completion_zh': '返回是否被仪器接受；界面变化须看屏幕。',
                                        'partial_failure_zh': '一个手势可依次调用多个能力，前面已提交的不回滚（与真实终端相同）。', 'retry_zh': '相同请求ID及参数返回原响应。'},
                    'errors': _qtp_export_v1()['capabilities']['power.set']['errors']})
        out[cid] = cap
    return out


# ---- instance state
_qt_create_instance_v1 = QuantosQ2Runtime.create_instance


def _qtp_create_instance(self, instance_id, scene_adapter=None):
    with self._lock:
        _qt_create_instance_v1(self, instance_id, scene_adapter)
        i = self._instances[instance_id]
        i['qtp'] = {'profiles': {p: _qtp_factory_profile(p) for p in QUANTOS_PROFILES}, 'system': _qtp_factory_system(), 'credentials': _qtp_factory_credentials(),
                    'active': 'Home', 'clock_base_s': None, 'last_activity_s': self.time_s, 'power_epoch': 0, 'copy_memory': None, 'copy_source': None,
                    'tare_kind': None, 'pending_post': None,
                    'phase': None, 'hold_s': 0.0, 'extra_s': 0.0,                 # dosing phase, pause left (tap / settling), pause time of this cycle
                    'last_key_s': None, 'profact_pending': False, 'bubble': [0.0, 0.0], 'level_events': 0, 'level_lost_s': None, 'fault': None, 'fault_seq': 0}
        i['state'].update({'dosing.samples_total': 0, 'dosing.samples_remaining': 0, 'head.locked': False, 'head.copy_memory': False, 'head.position': 'raised',
                           'door.side_opening_fraction': 0.0, 'balance.net_mode': False, 'balance.adjustment_history': [], 'display.resolution': '1d',
                           'tapper.taps': 0, 'output.records': [], 'tapper.last_tap': None, 'dosing.phase': None, 'balance.level_ok': True,
                           'balance.adjust_pending': False, 'device.error': None})
        _qtp_sync(self, i)


def _qtp_sync(host, i):
    """Agent-visible copies of the profile / system settings and the derived clock texts."""
    q, s = i['qtp'], i['state']
    s['profile.active'] = q['active']
    s['profile.names'] = [q['profiles'][p]['user_name'] for p in QUANTOS_PROFILES]
    s['settings.profile'] = _qt_copy.deepcopy(q['profiles'][q['active']])
    s['settings.system'] = _qt_copy.deepcopy(q['system'])
    s['security.credentials'] = _qt_copy.deepcopy(q['credentials'])
    level = (q['bubble'][0] ** 2 + q['bubble'][1] ** 2) ** 0.5 <= QUANTOS_LEVEL_OK_RADIUS + 1e-12
    s['balance.level_ok'] = level if q['system']['levelcontrol'] else None          # the level sensor (p.83); deselected: no reading
    if s['balance.level_ok'] is False and q['level_lost_s'] is None:
        q['level_lost_s'], q['level_events'] = host.time_s, q['level_events'] + 1   # a new 'not level' detection
    elif s['balance.level_ok'] is not False:
        q['level_lost_s'] = None
    s['balance.adjust_pending'] = q['profact_pending']
    if q['clock_base_s'] is None:
        s['clock.datetime'] = s['clock.date_text'] = s['clock.time_text'] = None
    else:
        now = _qtp_dt.datetime(1970, 1, 1) + _qtp_dt.timedelta(seconds=q['clock_base_s'] + host.time_s)
        s['clock.datetime'] = now.replace(microsecond=0).isoformat()
        months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
        df, tf = q['system']['date_format'], q['system']['time_format']
        s['clock.date_text'] = {'D.MMM YYYY': '%d. %s %d' % (now.day, months[now.month - 1], now.year), 'MMM D YYYY': '%s %d %d' % (months[now.month - 1], now.day, now.year),
                                'DD.MM.YYYY': now.strftime('%d.%m.%Y'), 'MM/DD/YYYY': now.strftime('%m/%d/%Y')}[df]
        h12 = '%d%s%02d %s' % ((now.hour % 12) or 12, '{sep}', now.minute, 'PM' if now.hour >= 12 else 'AM')
        s['clock.time_text'] = {'24:MM': now.strftime('%H:%M'), '24.MM': now.strftime('%H.%M'), '12:MM': h12.replace('{sep}', ':'), '12.MM': h12.replace('{sep}', '.')}[tf]


def _qtp_today(host, i):
    base = i['qtp']['clock_base_s']
    return None if base is None else (_qtp_dt.datetime(1970, 1, 1) + _qtp_dt.timedelta(seconds=base + host.time_s)).date()


_qt_refresh_v1 = QuantosQ2Runtime._refresh


def _qtp_refresh(i):
    """Tolerance check of the active tolerance mode: +/-: |error| <= tolerance; 0/+: 0 <= error <= tolerance (p.62)."""
    _qt_refresh_v1(i)
    s = i['state']
    q = i.get('qtp')
    if q is not None and s['measurement.out_of_tolerance'] is not None and q['profiles'][q['active']]['tolerance_mode'] == '0/+Tolerance':
        s['measurement.out_of_tolerance'] = not (0 <= s['measurement.error_kg'] <= s['dosing.tolerance_kg'])


QuantosQ2Runtime._refresh = staticmethod(_qtp_refresh)
QuantosQ2Runtime.create_instance = _qtp_create_instance


def _qtp_record(host, i, device, kind, lines, **extra):
    rec = dict({'device': device, 'kind': kind, 'time_s': host.time_s, 'datetime': i['state']['clock.datetime'], 'lines': list(lines)}, **extra)
    i['state']['output.records'] = (i['state']['output.records'] + [rec])[-QUANTOS_RECORDS_MAX:]


def _qtp_fmt_mg(kg, digits=3):
    return '--' if kg is None else ('%.' + str(digits) + 'f') % (kg * 1e6)


def _qtp_level_text(i):
    ok = i['state']['balance.level_ok']
    return 'Level control off' if ok is None else ('Level OK' if ok else 'Level not OK')


def _qtp_sample_values(host, i, result, label_index=1):
    """Values of the sample record / label items (p.56-60; 'Dos. value' is the barcode item, p.58)."""
    s, q = i['state'], i['qtp']
    prof, head = q['profiles'][q['active']], s['head.data'] or {}
    return {'Substance': head.get('substance'), 'Sample ID': s['dosing.sample_id'], 'Lot ID': head.get('lot_id'), 'User ID': s['dosing.user_id'],
            'Content [mg]': None if not result else _qtp_fmt_mg(result.get('actual_kg')), 'Tolerance': None if s['dosing.tolerance_percent'] is None else '%g %%' % s['dosing.tolerance_percent'],
            'Dispense date': s['clock.date_text'], 'Exp. date': head.get('exp_date'), 'Retest date': head.get('retest_date'), 'Balance ID': q['system']['balance_id'],
            'Title 1': prof['title_1'], 'Title 2': prof['title_2'], 'Validity': None if not result or result.get('valid') is None else ('Valid' if result['valid'] else 'Invalid'),
            'MinWeigh': 'Off', 'Dose duration': None if not result else '%.1f s' % result['duration_s'],
            'Target quantity': None if s['dosing.target_mass_kg'] is None else _qtp_fmt_mg(s['dosing.target_mass_kg']) + ' mg',
            'Head type': head.get('head_type'), 'Head ID': head.get('head_id'), 'Appl. Name': 'Dosing', 'Date/Time': ' '.join(x for x in (s['clock.date_text'], s['clock.time_text']) if x) or None,
            'Balance Type': 'Quantos Q2 / XPE206DR', 'Levelcontrol': _qtp_level_text(i), 'Last cal.': None, 'Label index': str(label_index), 'Sample position': None,
            'SNR:': None, 'Dos. value': None if not result else _qtp_fmt_mg(result.get('actual_kg')) + ' mg'}


def _qtp_sample_lines(host, i, items, result, label_index=1):
    """Text of a sample record / label in the order of the selected items (p.56-60); items without data print '-'."""
    head = i['state']['head.data'] or {}
    val = _qtp_sample_values(host, i, result, label_index)
    lines = []
    for it in items:
        if it in ('Dash Line', 'Dash Line 2'):
            lines.append('-' * 24)
        elif it == 'Blank Line':
            lines.append('')
        elif it == '3 Blank Lines':
            lines += ['', '', '']
        elif it == 'Signature':
            lines.append('Signature ________')
        elif it == 'Header':
            continue
        elif it.startswith('Var') and it in ('Var1', 'Var2', 'Var3', 'Var4'):
            entry = head.get('var', [{}] * 4)[int(it[3]) - 1] if head else {}
            lines.append('%-14s %s' % (entry.get('name', it), entry.get('value') or '-'))
        else:
            v_ = val.get(it)
            lines.append('%-14s %s' % (it, '-' if v_ in (None, '') else v_))
    return lines


def _qtp_label(host, i, result, index, quantity):
    """One sample label (p.56-59): the text items that fit the layout scheme's rows (printing starts with item 1), the code section of the scheme
    (matrix code with the selected items, none selected: no code; Code 128 barcode with the one barcode item), and the cut of the cutter."""
    s, q = i['state'], i['qtp']
    prof = q['profiles'][q['active']]
    rows, code_kind, font = QUANTOS_LABEL_LAYOUTS[prof['sample_label_layout']]
    vals = _qtp_sample_values(host, i, result, index)
    code = None
    if code_kind == 'matrix' and prof['sample_label_matrix']:
        code = {'type': 'matrix', 'items': {it: vals.get(it) for it in prof['sample_label_matrix']}}
    elif code_kind == 'barcode':
        code = {'type': 'code128', 'item': prof['sample_label_barcode'], 'value': vals.get(prof['sample_label_barcode'])}
    series = s['dosing.samples_total'] > 0 or (result or {}).get('last_sample')
    last_of_sample = index == quantity
    cut = last_of_sample and (prof['sample_label_cutter'] == 'Samples' or not series or bool((result or {}).get('last_sample')))
    lines = _qtp_sample_lines(host, i, prof['sample_label_text'], result, index)[:rows]
    return dict(lines=lines, layout=prof['sample_label_layout'], font=font, code=code, cut=cut, label_index=index)


def _qtp_auto_output(host, i, result):
    q = i['qtp']
    prof = q['profiles'][q['active']]
    if prof['sample_output_mode'] != 'Automatic':
        return
    for dev in prof['sample_output_devices']:
        if dev == 'Protocol':
            lines = _qtp_sample_lines(host, i, prof['sample_protocol_header'], result) + _qtp_sample_lines(host, i, prof['sample_protocol_single'], result) + \
                _qtp_sample_lines(host, i, prof['sample_protocol_footer'], result)
            _qtp_record(host, i, 'printer', 'sample_protocol', lines)
        elif dev == 'Label':
            for n in range(1, prof['sample_label_quantity'] + 1):
                lab = _qtp_label(host, i, result, n, prof['sample_label_quantity'])
                _qtp_record(host, i, 'label_printer', 'sample_label', lab.pop('lines'), **lab)
        else:
            _qtp_record(host, i, 'host', 'sample_xml', ['<Sample id="%s" content_mg="%s" valid="%s"/>' % (i['state']['dosing.sample_id'] or '', _qtp_fmt_mg(result.get('actual_kg')),
                                                                                                         result.get('valid'))])


def _qtp_after_dosing(host, i):
    """End of a dosing cycle (succeeded, failed, cancelled): result, head counters, sample counter, automatic output, automatic unlock (p.23, p.28, p.32, p.61).
    The dose duration includes the tap before dosing and the settling before the result is released."""
    s, q = i['state'], i['qtp']
    status = s['dosing.status']
    actual = s['truth.actual_dosed_kg']
    valid = None if s['measurement.out_of_tolerance'] is None else not s['measurement.out_of_tolerance']
    result = {'status': status, 'actual_kg': actual, 'target_kg': s['dosing.target_mass_kg'], 'error_kg': s['measurement.error_kg'], 'valid': valid,
              'duration_s': s['dosing.elapsed_s'] + q['extra_s'], 'sample_id': s['dosing.sample_id'], 'user_id': s['dosing.user_id'], 'datetime': s['clock.datetime'],
              'last_sample': False, 'resolution': s['display.resolution']}
    q.update(phase=None, hold_s=0.0, extra_s=0.0)
    s['dosing.phase'] = None
    head = s['head.data']
    if head is not None and head.get('remaining_quantity_kg') is not None and actual:
        head['remaining_quantity_kg'] = max(0.0, head['remaining_quantity_kg'] - actual)
        _qtp_store_head(host, i)
    if status in ('succeeded', 'failed') and s['dosing.samples_total']:
        s['dosing.samples_remaining'] = max(0, s['dosing.samples_remaining'] - 1)
        if s['dosing.samples_remaining'] == 0:
            result['last_sample'] = True                                          # 'Last sample reached'; both values back to 0 (p.32)
            s['dosing.samples_total'] = 0
    s['dosing.last_result'] = result
    if status in ('succeeded', 'failed'):
        _qtp_auto_output(host, i, result)
    if 'Unlock head' in q['profiles'][q['active']]['dosing_steps']:
        s['head.locked'] = False


def _qtp_release_s(prof):
    return (QUANTOS_VALUE_RELEASE_S[prof['value_release']] * QUANTOS_ENVIRONMENT_FACTOR[prof['environment']]
            * QUANTOS_WEIGHING_MODE_FACTOR[prof['weighing_mode']])


def _qtp_settle_s(i):
    """Extra time until the balance releases the stable result after the powder stops (value release x environment x weighing mode, p.70),
    counted from the factory release that the ideal model already gives at once."""
    prof = i['qtp']['profiles'][i['qtp']['active']]
    return max(0.0, _qtp_release_s(prof) - _qtp_release_s(_qtp_factory_profile('Home')))


def _qtp_tap(host, i, trigger):
    """A tapping cycle (p.66-67): the set intensity and duration for [Tapper] and 'Tapping before dosing', automatic ones while dosing."""
    s, q = i['state'], i['qtp']
    prof = q['profiles'][q['active']]
    auto = trigger == 'while_dosing'
    s['tapper.taps'] += 1
    s['tapper.last_tap_s'] = host.time_s
    s['tapper.last_tap'] = {'trigger': trigger, 'time_s': host.time_s, 'intensity_percent': None if auto else prof['tapper_intensity'],
                            'duration_s': QUANTOS_AUTO_TAP_S if auto else float(prof['tapper_duration_s'])}
    return s['tapper.last_tap']['duration_s']


def _qtp_reopen(host, i, opid, phase, hold_s):
    """The powder has stopped (target reached or flow stopped) but the cycle goes on: settling, or an automatic tap before the failure."""
    op = host._operations[opid]
    op['status'] = 'running'
    op.pop('error', None)
    i['active_operation'] = opid
    i['state']['dosing.status'] = 'running'
    i['qtp'].update(phase=phase, hold_s=hold_s)
    i['state']['dosing.phase'] = 'tapping' if phase in ('tap', 'tap_fail') else phase
    host._refresh(i)
    op['result'] = host._result(i)


def _qtp_hold_done(host, i):
    """End of a pause: the tap before dosing (the powder starts), the settling (result released) or the tap after the flow stopped (failure)."""
    q, opid = i['qtp'], i['active_operation']
    q['hold_s'] = 0.0
    if q['phase'] == 'tap':
        q['phase'], i['state']['dosing.phase'] = 'dosing', 'dosing'
        return
    if q['phase'] == 'settling':
        host._stop(i, 'succeeded')
    else:
        host._stop(i, 'failed', 'SOURCE_DEPLETED')
    host._refresh(i)
    host._operations[opid]['result'] = host._result(i)
    i['revision'] += 1


def _qtp_store_head(host, i):
    head = i['state']['head.data']
    if head is not None:
        host._qtp_rfid[head['head_id']] = _qt_copy.deepcopy(head)


_qt_host_step_v2 = QuantosQ2Runtime.host_step


def _qtp_transfer(self, dt_s, world_time_s):
    """The common clock step of the dosing cycles in pieces: an instance in a pause (tap before dosing, settling, tap after the flow stopped)
    takes no powder (its operation is held back from the transfer step), and Algo P doses the last 10 % at a quarter of the rate.  Time left in
    a piece after the powder stopped counts towards the next pause.  With no pause and Algo M this is the original single step."""
    left, end = dt_s, world_time_s
    while True:
        chunk = left
        live = [(iid, i) for iid, i in self._instances.items() if 'qtp' in i and i['active_operation'] is not None]
        for iid, i in live:
            q, s = i['qtp'], i['state']
            if q['hold_s'] > 0:
                chunk = min(chunk, q['hold_s'])
            elif q['profiles'][q['active']]['algo'] == 'Algo P' and i['rate_kg_s']:
                to_slow = (QUANTOS_ALGO_P[0] * s['dosing.target_mass_kg'] - s['truth.actual_dosed_kg']) / i['rate_kg_s']
                if to_slow > 1e-6:
                    chunk = min(chunk, to_slow)
        if left - chunk < 1e-9:
            chunk = left
        last = chunk == left
        held = [(i, i['active_operation']) for _, i in live if i['qtp']['hold_s'] > 0]
        slow = [(i, i['rate_kg_s']) for _, i in live if i['qtp']['hold_s'] <= 0 and i['qtp']['profiles'][i['qtp']['active']]['algo'] == 'Algo P'
                and i['rate_kg_s'] and i['state']['truth.actual_dosed_kg'] >= QUANTOS_ALGO_P[0] * i['state']['dosing.target_mass_kg'] * (1 - 1e-9)]
        before = {iid: i['state']['dosing.elapsed_s'] for iid, i in live}
        ops = {iid: i['active_operation'] for iid, i in live}
        for i, _ in held:
            i['active_operation'] = None
        for i, rate in slow:
            i['rate_kg_s'] = rate * QUANTOS_ALGO_P[1]
        try:
            _qt_host_step_v2(self, chunk, world_time_s=end if last else self.time_s + chunk)
        finally:
            for i, opid in held:
                i['active_operation'] = opid
            for i, rate in slow:
                i['rate_kg_s'] = rate
        held_ids = {id(i) for i, _ in held}
        for iid, i in live:
            q, s = i['qtp'], i['state']
            if id(i) in held_ids:
                q['hold_s'] -= chunk
                q['extra_s'] += chunk
                if q['hold_s'] <= 1e-9:
                    _qtp_hold_done(self, i)
                continue
            if i['active_operation'] is not None:
                continue
            spare = max(0.0, chunk - (s['dosing.elapsed_s'] - before[iid]))          # the powder stopped inside this piece
            prof = q['profiles'][q['active']]
            if s['dosing.status'] == 'succeeded':
                hold, phase = _qtp_settle_s(i), 'settling'
            elif s['dosing.status'] == 'failed' and prof['tap_while_dosing']:
                hold, phase = _qtp_tap(self, i, 'while_dosing'), 'tap_fail'
            else:
                continue
            if hold <= 0:
                continue
            _qtp_reopen(self, i, ops[iid], phase, hold)
            used = min(hold, spare)
            q['hold_s'] -= used
            q['extra_s'] += used
            if q['hold_s'] <= 1e-9:
                _qtp_hold_done(self, i)
        left -= chunk
        if last:
            return


def _qtp_adjustment(host, i, type_, kind, result):
    """An adjustment or test with the internal weight: recorded (all kinds, max. 50, p.75) with date, type and leveling."""
    s = i['state']
    entry = {'type': type_, 'kind': kind, 'time_s': host.time_s, 'datetime': s['clock.datetime'], 'result': result, 'level': _qtp_level_text(i)}
    s['balance.adjustment_history'] = (s['balance.adjustment_history'] + [entry])[-QUANTOS_ADJ_HISTORY_MAX:]
    if type_ == 'Adjust int':
        s['balance.tared'] = False                                             # the internal weight is placed and lifted: tare again
    return entry


def _qtp_adjustment_list(i):
    """The Adj. History list: the kinds chosen in 'Selection', the last 'Display Datasets' of them (p.75-76; the memory keeps all)."""
    sysd = i['qtp']['system']
    rows = [e for e in i['state']['balance.adjustment_history'] if e.get('kind') in sysd['adj_history_selection']]
    return rows[-sysd['adj_history_datasets']:]


def _qtp_adjust_lines(host, i, entries):
    """Adjustment / test report (p.78): the items chosen in 'Protocol' for each entry.  Values the simulation does not have (serial number,
    software version, temperature, internal weight values) print '-'."""
    q = i['qtp']
    sysd = q['system']
    out = []
    for e in entries:
        out += ['%-14s %s' % ('Adjustment', e['type']), '%-14s %s' % ('Result', e['result'])]
        for it in sysd['adjust_protocol']:
            if it == 'Signature':
                out.append('Signature ________')
                continue
            if it == 'Level control off':
                if e.get('level') == 'Level control off':
                    out.append('Level control off')
                continue
            v_ = {'Date/Time': e['datetime'] or '%.0f s' % e['time_s'], 'User': q['profiles'][q['active']]['user_name'], 'Balance Type': 'Quantos Q2 / XPE206DR',
                  'Balance ID': sysd['balance_id'], 'Weight ID': 'Internal weight'}.get(it)
            out.append('%-14s %s' % (it, '-' if v_ in (None, '') else v_))
    return out


def _qtp_profact(host, i, t0):
    """ProFACT time criterion (p.36, p.76): at Time 1 of a selected weekday the status icon appears; the adjustment with the internal weight runs
    when the instrument is on, idle, unloaded and no key was touched for 2 minutes (then the report prints if Protocol Trigger is on).  The
    temperature criterion and the extra runs of the first 24 h need a temperature / power history the simulation does not have."""
    q, s = i['qtp'], i['state']
    sysd = q['system']
    changed = False
    if sysd['profact'] and q['clock_base_s'] is not None and not q['profact_pending']:
        epoch = _qtp_dt.datetime(1970, 1, 1)
        a = epoch + _qtp_dt.timedelta(seconds=q['clock_base_s'] + t0)
        b = epoch + _qtp_dt.timedelta(seconds=q['clock_base_s'] + host.time_s)
        hh, mm = (int(x) for x in sysd['profact_time_1'].split(':'))
        day = a.date()
        while day <= b.date():
            at = _qtp_dt.datetime(day.year, day.month, day.day, hh, mm)
            if a < at <= b and QUANTOS_WEEKDAYS[day.weekday()] in sysd['profact_weekdays']:
                q['profact_pending'] = changed = True
                break
            day += _qtp_dt.timedelta(days=1)
    if (q['profact_pending'] and s['power.enabled'] and i['active_operation'] is None and s['container.loaded_id'] is None
            and host.time_s - q['last_activity_s'] >= QUANTOS_PROFACT_QUIET_S - 1e-9):
        entry = _qtp_adjustment(host, i, 'Adjust int', 'Time Adjust.', 'done')
        q['profact_pending'] = False
        if sysd['profact_protocol_trigger']:
            _qtp_record(host, i, 'printer', 'adjustment_protocol', _qtp_adjust_lines(host, i, [entry]))
        changed = True
    return changed


def _qtp_host_step(self, dt_s, *, world_time_s):
    with self._lock:
        if not (_qt_number(dt_s) and dt_s > 0 and _qt_number(world_time_s) and world_time_s > self.time_s and self.time_s + dt_s > self.time_s
                and _qt_math.isclose(world_time_s, self.time_s + dt_s, rel_tol=0, abs_tol=1e-9)):
            return _qt_host_step_v2(self, dt_s, world_time_s=world_time_s)  # the original checks refuse it (nothing changed)
        t0 = self.time_s
        running = {iid for iid, i in self._instances.items() if i['active_operation'] is not None}
        _qtp_transfer(self, dt_s, world_time_s)
        for iid, i in self._instances.items():
            if 'qtp' not in i:
                continue
            changed = False
            if iid in running and i['active_operation'] is None:
                _qtp_after_dosing(self, i)
                changed = True
            changed = _qtp_profact(self, i, t0) or changed
            q, s = i['qtp'], i['state']
            sb = q['system']['standby_min']
            if sb != 'Off' and s['power.enabled'] and i['active_operation'] is None and self.time_s - q['last_activity_s'] >= sb * 60.0 - 1e-9:
                s['power.enabled'], s['balance.tared'] = False, False                # standby = the state after [On/Off] (p.78-79)
                changed = True
            before = (s['clock.date_text'], s['clock.time_text'])
            _qtp_sync(self, i)
            if changed or before != (s['clock.date_text'], s['clock.time_text']):
                self._refresh(i)
                i['revision'] += 1


QuantosQ2Runtime.host_step = _qtp_host_step
_qt_load_head_v1 = QuantosQ2Runtime.host_load_head
_qt_unload_head_v1 = QuantosQ2Runtime.host_unload_head


def _qtp_load_head(self, iid, *, head_id, source_mass_kg, free_flowing, evidence):
    """Inserting a head: its RFID data comes with it (written data kept per head ID, else a new head); not locked yet (p.21, p.28)."""
    with self._lock:
        _qt_load_head_v1(self, iid, head_id=head_id, source_mass_kg=source_mass_kg, free_flowing=free_flowing, evidence=evidence)
        i = self._instances[iid]
        i['state']['head.data'] = _qt_copy.deepcopy(self._qtp_rfid.get(head_id) or _qtp_default_head(head_id))
        i['state']['head.locked'] = False
        i['revision'] += 1


def _qtp_unload_head(self, iid):
    with self._lock:
        i = self._instances.get(iid)
        if i is not None and i['state'].get('head.locked'):
            raise ValueError('The dosing head is locked; release it with [Un/Lock] first')
        result = _qt_unload_head_v1(self, iid)
        i['state']['head.data'] = None
        return result


QuantosQ2Runtime.host_load_head = _qtp_load_head
QuantosQ2Runtime.host_unload_head = _qtp_unload_head
QuantosQ2Runtime._qtp_rfid = None
_qt_init_v1 = QuantosQ2Runtime.__init__


def _qtp_init(self):
    _qt_init_v1(self)
    self._qtp_rfid, self._panels, self.panel_capture_dir = {}, {}, None


QuantosQ2Runtime.__init__ = _qtp_init


def _qtp_runtime_panel(self, instance_id):
    """The terminal (panel engine) of an instance (trusted host / tests: render, frame); created on first use."""
    with self._lock:
        if instance_id not in self._instances:
            raise ValueError('Unknown instance')
        if instance_id not in self._panels:
            self._panels[instance_id] = _qtp_panel()['engine'].PanelUI(_qtp_panel()['spec'], _QuantosPanelHost(self, instance_id))
        return self._panels[instance_id]


QuantosQ2Runtime.panel = _qtp_runtime_panel


# ---- commands
_qt_execute_v1 = QuantosQ2Session._execute
_qt_describe_v1 = QuantosQ2Session.describe


def _qtp_fail(sess, iid, code, rid):
    return sess._error(iid, code, rid)


def _qtp_foreign_run(sess, host, i, cid, args):
    """Another caller's dosing is running and this command would abort it, power the instrument off or change the dosing (lead rule
    2026-10-19: ACCESS_DENIED on the terminal and the direct path); the evaluator / debug identities are unrestricted."""
    opid, me = i['active_operation'], sess._QuantosQ2Session__principal
    if opid is None or host._operations[opid]['principal'] == me or me == _QuantosPanelHost.PRINCIPAL or host._principals[me][0] in ('debug', 'evaluator'):
        return False                                                          # the terminal's own actions (no gesturing caller) are not foreign
    return cid in _QTP_RUN_CHANGES or cid == 'dosing.abort' or (cid == 'power.set' and isinstance(args, dict) and args.get('enabled') is False)


def _qtp_fault(sess, host, iid, i, cid, args, rid):
    """fault.inject / fault.clear (evaluator): the documented error state of p.89-92."""
    if host._principals[sess._QuantosQ2Session__principal][0] not in ('debug', 'evaluator'):
        return _qtp_fail(sess, iid, 'ACCESS_DENIED', rid)
    props = _qtp_cap_specs()[cid][0]
    if not isinstance(args, dict) or set(args) != set(props) or not all(_qtp_valid(props[k], args[k]) for k in props):
        return _qtp_fail(sess, iid, 'INVALID_ARGUMENT', rid)
    q, s = i['qtp'], i['state']
    if cid == 'fault.inject':
        if q['fault'] is not None:
            return _qtp_fail(sess, iid, 'PRECONDITION_FAILED', rid)
        q['fault'], q['fault_seq'] = args['code'], q['fault_seq'] + 1
        opid = i['active_operation']
        if opid is not None:                                                    # the dosing stops with the error (p.89-92)
            host._stop(i, 'failed')
            text = QUANTOS_FAULTS[args['code']][0]
            host._operations[opid]['error'] = {'code': 'DEVICE_ERROR', 'message': '%s %s' % (text, args['code']) if args['code'].startswith('No.') else text,
                                               'retryable': False, 'side_effects': 'partial', 'progress': host._result(i)}
            host._refresh(i)
            _qtp_after_dosing(host, i)
    else:
        q['fault'] = None
    s['device.error'] = q['fault']
    return _qtp_done(sess, host, iid, i, cid, rid)


def _qtp_execute(self, iid, i, cid, args, rid):
    host = self._QuantosQ2Session__host
    news = _qtp_cap_specs()
    if isinstance(cid, str) and cid.startswith('panel.'):
        return _qtp_invoke_panel(self, iid, i, cid, args, rid)
    if cid in ('fault.inject', 'fault.clear'):
        return _qtp_fault(self, host, iid, i, cid, args, rid)
    if _qtp_foreign_run(self, host, i, cid, args):
        return _qtp_fail(self, iid, 'ACCESS_DENIED', rid)
    if cid in _QTP_FAULT_REFUSED and i['qtp']['fault'] is not None and isinstance(args, dict):
        return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)                  # an error of p.89-92 is present
    if cid not in news:
        return _qtp_execute_original(self, host, iid, i, cid, args, rid)
    props = news[cid][0]
    if not isinstance(args, dict) or set(args) != set(props) or not all(_qtp_valid(props[k], args[k]) for k in props):
        return _qtp_fail(self, iid, 'INVALID_ARGUMENT', rid)
    s, q = i['state'], i['qtp']
    busy = i['active_operation'] is not None
    if cid == 'dosing.abort':                                                      # another caller's dosing: refused above (_qtp_foreign_run)
        if not busy:
            return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
        host._stop(i, 'cancelled')
        host._refresh(i)
        _qtp_after_dosing(host, i)
        return _qtp_done(self, host, iid, i, cid, rid)
    if busy and cid not in ('security.verify', 'display.set_resolution', 'output.print'):
        return _qtp_fail(self, iid, 'BUSY', rid)
    if cid not in ('head.set_height', 'door.set_side') and not s['power.enabled']:   # the doors as the original door command
        return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
    prof = q['profiles'][q['active']]
    try:
        if cid == 'profile.select':
            if args['profile'] != 'Home' and args['profile'] not in q['system']['users_enabled']:
                return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
            q['active'] = args['profile']
        elif cid == 'settings.set_profile':
            names = [q['profiles'][p]['user_name'] for p in QUANTOS_PROFILES if p != q['active']]
            value = _qtp_check_setting(args['key'], args['value'], names)
            if args['key'] == 'user_name' and value is None:
                raise ValueError('name')
            prof[args['key']] = value
        elif cid == 'settings.set_system':
            key, value = args['key'], args['value']
            if key == 'rights':                                                        # {profile: {area: right}}: the given entries change
                if not isinstance(value, dict) or not value or any(p_ not in QUANTOS_PROFILES or not isinstance(v, dict) or not v or
                                                                   any(a_ not in QUANTOS_RIGHTS_AREAS or x not in ('No Protection', 'User Protect.', 'Adm. Protect.')
                                                                       for a_, x in v.items()) for p_, v in value.items()):
                    raise ValueError(key)
                for p_, v in value.items():
                    q['system'][key][p_].update(v)
            elif key in QUANTOS_WEIGHT_SETS:                                           # all five weights, or one: {index 1-5, weight_g, id, certificate}
                if isinstance(value, dict) and set(value) == {'index', 'weight_g', 'id', 'certificate'} and value['index'] in (1, 2, 3, 4, 5) \
                        and not isinstance(value['index'], bool):
                    q['system'][key][value['index'] - 1] = _qtp_weight_entry({k_: value[k_] for k_ in ('weight_g', 'id', 'certificate')})
                elif isinstance(value, list) and len(value) == 5:
                    q['system'][key] = [_qtp_weight_entry(v) for v in value]
                else:
                    raise ValueError(key)
            else:
                q['system'][key] = _qtp_check_setting(key, value)
                if key == 'users_enabled' and q['active'] != 'Home' and q['active'] not in q['system'][key]:
                    q['active'] = 'Home'
        elif cid == 'settings.set_credentials':
            q['credentials']['admin' if args['level'] == 'admin' else q['active']] = {'id': args['id'], 'password': args['password']}
        elif cid == 'security.verify':
            cands = [q['credentials']['admin']] + ([q['credentials'][q['active']]] if args['level'] == 'user' else [])
            if not any(c['id'] == args['id'] and c['password'] == args['password'] for c in cands):
                return _qtp_fail(self, iid, 'ACCESS_DENIED', rid)
            return self._response(iid, rid, result={})
        elif cid == 'settings.user_reset':
            q['profiles'][q['active']] = _qtp_factory_profile(q['active'])
            q['credentials'][q['active']] = _qtp_factory_credentials()[q['active']]
        elif cid == 'settings.master_reset':
            history = s['balance.adjustment_history']
            q.update(profiles={p: _qtp_factory_profile(p) for p in QUANTOS_PROFILES}, credentials=_qtp_factory_credentials(), active='Home')
            keep = {k: q['system'][k] for k in ('date_format', 'time_format')}          # date and time are kept (p.82)
            q['system'] = dict(_qtp_factory_system(), **keep)
            s['balance.adjustment_history'], s['display.resolution'] = history, '1d'
        elif cid == 'dosing.set_ids':
            s['dosing.user_id'], s['dosing.sample_id'] = args['user_id'], args['sample_id']
        elif cid == 'dosing.set_tolerance_percent':
            s['dosing.tolerance_percent'] = float(args['percent'])
            if s['dosing.target_mass_kg'] is not None:
                s['dosing.tolerance_kg'] = s['dosing.target_mass_kg'] * s['dosing.tolerance_percent'] / 100.0
        elif cid == 'dosing.set_samples':
            s['dosing.samples_total'] = s['dosing.samples_remaining'] = args['count']
        elif cid == 'head.set_lock':
            if s['head.loaded_id'] is None:
                return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
            s['head.locked'] = args['locked']
            if not args['locked']:
                _qtp_linked_doors(self, host, iid, i, 'Un/Lock', open_=True)
        elif cid == 'head.write_data':
            head = s['head.data']
            if head is None:
                return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
            data = args['data']
            for f in ('filling_date', 'exp_date', 'retest_date'):
                if data.get(f) is not None and _qtp_parse_date(data[f]) is None:
                    return _qtp_fail(self, iid, 'INVALID_ARGUMENT', rid)
            if 'dose_limit' in data and data['dose_limit'] != head['dose_limit']:
                used = head['dose_limit'] - head['remaining_dosages']
                head['dose_limit'], head['remaining_dosages'] = data['dose_limit'], max(0, data['dose_limit'] - used)
            for k_, v_ in data.items():
                if k_ == 'content_kg':
                    new = None if v_ is None else float(v_)
                    if new != head['content_kg']:                                     # a new content restarts the remaining-quantity counter (p.30)
                        head['content_kg'] = head['remaining_quantity_kg'] = new
                elif k_ != 'dose_limit':
                    head[k_] = _qt_copy.deepcopy(v_)
            head['written'] = True
            s['head.locked'] = True
            _qtp_store_head(host, i)
            if prof['head_output_mode'] == 'Automatic':                             # head data output after [Write head] (p.31, p.61)
                for dev in prof['head_output_devices']:
                    _qtp_record(host, i, {'Protocol': 'printer', 'Label': 'label_printer', 'Host (XML)': 'host'}[dev], 'head_data', _qtp_head_lines(head, prof['head_protocol']))
        elif cid == 'head.set_content':
            if s['measurement.balance_net_kg'] is None:
                return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
            s['head.content_buffer_kg'] = s['measurement.balance_net_kg']
        elif cid == 'head.copy_data':
            if s['head.data'] is None:
                return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
            if args['action'] == 'read':
                q['copy_memory'], q['copy_source'] = _qt_copy.deepcopy(s['head.data']), s['head.data']['head_id']
                s['head.copy_memory'] = True
            else:
                if q['copy_memory'] is None or q['copy_source'] == s['head.data']['head_id']:
                    return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
                s['head.data'] = dict(_qt_copy.deepcopy(q['copy_memory']), head_id=s['head.data']['head_id'])
                q['copy_memory'] = q['copy_source'] = None
                s['head.copy_memory'], s['head.locked'] = False, True
                _qtp_store_head(host, i)
        elif cid == 'head.set_height':
            s['head.position'] = args['position']
        elif cid in ('balance.zero', 'balance.adjust_internal', 'balance.test_internal'):
            if s['scene.door_opening_m'] not in (0, 0.0, None) or s['door.side_opening_fraction'] > 0:
                return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
            if cid == 'balance.zero':
                if s['container.loaded_id'] is None or s['container.position_id'] != i['position_id'] or not s['simulation.rate_configured']:
                    return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
                i['tare_mass_kg'], s['balance.tared'], s['balance.net_mode'] = s['truth.container_mass_kg'], True, False
            else:
                _qtp_adjustment(host, i, 'Adjust int' if cid == 'balance.adjust_internal' else 'Test int', 'Manual adjust.',
                                'done' if cid == 'balance.adjust_internal' else 'passed')
        elif cid == 'door.set_side':
            s['door.side_opening_fraction'] = prof['doorway_percent'] / 100.0 if args['open'] else 0.0
            if args['open']:
                s['balance.tared'] = False
        elif cid == 'tapper.tap':
            if s['head.loaded_id'] is None:
                return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
            _qtp_tap(host, i, 'manual')                                         # with the set intensity and duration (p.66)
        elif cid == 'display.set_resolution':
            s['display.resolution'] = args['resolution']
        elif cid == 'output.print':
            _qtp_print(host, i, args['content'])
        elif cid == 'clock.set':
            d = _qtp_parse_date(args['date'])
            hh, mm, *ss = args['time'].split(':')
            if d is None or not (int(hh) < 24 and int(mm) < 60 and (not ss or int(ss[0]) < 60)):
                return _qtp_fail(self, iid, 'INVALID_ARGUMENT', rid)
            when = _qtp_dt.datetime(d.year, d.month, d.day, int(hh), int(mm), int(ss[0]) if ss else 0)
            q['clock_base_s'] = (when - _qtp_dt.datetime(1970, 1, 1)).total_seconds() - host.time_s
    except ValueError:
        return _qtp_fail(self, iid, 'INVALID_ARGUMENT', rid)
    if cid.startswith('settings.') and q['system']['history_record_mode']:          # History: changes of settings while Record Mode is on (p.82)
        q['system'].setdefault('history', [])
        q['system']['history'] = (q['system']['history'] + [{'time_s': host.time_s, 'datetime': s['clock.datetime'], 'change': cid,
                                                             'key': args.get('key') or args.get('level')}])[-50:]
    return _qtp_done(self, host, iid, i, cid, rid)


def _qtp_done(sess, host, iid, i, cid, rid):
    _qtp_sync(host, i)
    host._refresh(i)
    i['revision'] += 1
    i['qtp']['last_activity_s'] = host.time_s
    observed = _qtp_cap_specs()[cid][2]
    return sess._response(iid, rid, result={sid: i['state'][sid] for sid in observed})


def _qtp_head_lines(head, fields):
    names = {'Substance': 'substance', 'Lot ID': 'lot_id', 'Filling date': 'filling_date', 'Exp. date': 'exp_date', 'Retest date reached': 'retest_date',
             'Head type': 'head_type', 'Dose limit': 'dose_limit', 'Rem. dosages': 'remaining_dosages', 'Head ID': 'head_id'}
    out = []
    for f in fields:
        if f in names:
            v_ = head.get(names[f])
        elif f == 'Content [mg,g]':
            v_ = None if head.get('content_kg') is None else _qtp_fmt_mg(head['content_kg']) + ' mg'
        elif f == 'Rem. quantity':
            v_ = None if head.get('remaining_quantity_kg') is None else _qtp_fmt_mg(head['remaining_quantity_kg']) + ' mg'
        elif f.startswith('Var'):
            e = head['var'][int(f[3]) - 1]
            out.append('%-14s %s' % (e['name'], e['value'] or '-'))
            continue
        else:
            v_ = None
        out.append('%-14s %s' % (f, '-' if v_ in (None, '') else v_))
    return out


def _qtp_print(host, i, content):
    s, q = i['state'], i['qtp']
    prof = q['profiles'][q['active']]
    if content == 'sample':
        lines = _qtp_sample_lines(host, i, prof['sample_protocol_header'] + prof['sample_protocol_single'] + prof['sample_protocol_footer'], s['dosing.last_result'])
    elif content == 'head':
        lines = _qtp_head_lines(s['head.data'], prof['head_protocol']) if s['head.data'] else ['No head']
    elif content == 'settings':
        lines = ['%s %s' % (k, v) for k, v in sorted(prof.items())] + ['%s %s' % (k, v) for k, v in sorted(q['system'].items()) if k not in ('rights', 'adjust_weights', 'test_weights')]
    elif content == 'adjustments':
        lines = _qtp_adjust_lines(host, i, _qtp_adjustment_list(i)) or ['No adjustments']
    else:
        lines = ['Net %s mg' % _qtp_fmt_mg(s['measurement.balance_net_kg'])]
    _qtp_record(host, i, 'printer', content, lines)


def _qtp_linked_doors(sess, host, iid, i, event, open_):
    """Doors linked with a procedure open / close with it (p.65): the side doors here, the front door through the scene adapter when it is bound."""
    q, s = i['qtp'], i['state']
    prof = q['profiles'][q['active']]
    if event in prof['side_doors_linked']:
        s['door.side_opening_fraction'] = prof['doorway_percent'] / 100.0 if open_ else 0.0
        if open_:
            s['balance.tared'] = False
    if event in prof['front_door_linked'] and prof['front_door_mounted'] == 'Mounted' and i['adapter'] is not None and i['door_travel_m'] is not None:
        target = i['door_travel_m'] if open_ else 0.0
        if s['scene.door_opening_m'] != target:
            try:
                ok = i['adapter'](iid, {'scene.door_opening_m': float(target)}) is True
            except Exception:
                ok = False
            if ok:
                s['scene.door_opening_m'] = float(target)
                if open_:
                    s['balance.tared'] = False


def _qtp_execute_original(self, host, iid, i, cid, args, rid):
    """The original commands with what the device does around them: side doors closed for tare / dosing, head checks and lock at the start,
    tolerance kept in % when the target changes, dosing end processing on cancel / power-off."""
    s, q = i['state'], i.get('qtp')
    if q is None:
        return _qt_execute_v1(self, iid, i, cid, args, rid)
    valid = isinstance(args, dict) and args == {} if cid in ('balance.tare', 'dosing.start') else True
    idle_on = i['active_operation'] is None and s['power.enabled']
    if valid and idle_on and cid in ('balance.tare', 'dosing.start') and s['door.side_opening_fraction'] > 0:
        return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)
    head = s['head.data']
    if valid and idle_on and cid == 'dosing.start' and head is not None:
        today, exp = _qtp_today(host, i), _qtp_parse_date(head['exp_date']) if head.get('exp_date') else None
        if head['remaining_dosages'] <= 0 or (today is not None and exp is not None and today >= exp):
            return _qtp_fail(self, iid, 'PRECONDITION_FAILED', rid)               # 'Head dosage limit reached' / 'Expiry date reached' (p.91-92)
    was_running, was_on = i['active_operation'] is not None, s['power.enabled']
    response = _qt_execute_v1(self, iid, i, cid, args, rid)
    if response['status'] not in ('succeeded', 'accepted', 'cancelled'):
        return response
    changed = False
    if cid == 'dosing.start' and response['status'] == 'accepted':
        s['head.locked'] = True                                                      # p.21: the dosing head is locked
        if head is not None:
            head['remaining_dosages'] = max(0, head['remaining_dosages'] - 1)          # p.28: every started cycle counts
            _qtp_store_head(host, i)
        q.update(phase='dosing', hold_s=0.0, extra_s=0.0)
        s['dosing.phase'] = 'dosing'
        if q['profiles'][q['active']]['tap_before_dosing']:                       # the tap runs first, then the powder (p.66-67)
            q.update(phase='tap', hold_s=_qtp_tap(host, i, 'before_dosing'))
            s['dosing.phase'] = 'tapping'
        s['dosing.last_result'] = None
        changed = True
    elif cid == 'dosing.set_target' and s['dosing.tolerance_percent'] is not None:
        s['dosing.tolerance_kg'] = s['dosing.target_mass_kg'] * s['dosing.tolerance_percent'] / 100.0
        changed = True
    elif cid == 'dosing.set_tolerance':
        s['dosing.tolerance_percent'] = None
        changed = True
    elif cid == 'balance.tare':
        s['balance.net_mode'] = True
        changed = True
    elif cid == 'power.set' and s['power.enabled'] and not was_on:
        q['power_epoch'] += 1                                                        # a switch-on starts the terminal anew
    if was_running and i['active_operation'] is None and cid in ('power.set', 'dosing.cancel'):
        _qtp_after_dosing(host, i)
        changed = True
    if cid not in ('dosing.read', 'dosing.poll'):
        q['last_activity_s'] = host.time_s
    if changed:
        host._refresh(i)
        response = dict(response, state_revision=i['revision'])
    return response


QuantosQ2Session._execute = _qtp_execute


def _qtp_describe(self, *, asset_type_id=None, instance_id=None):
    data = _qt_describe_v1(self, asset_type_id=asset_type_id, instance_id=instance_id)
    if data.get('error') is not None or 'capabilities' not in data:
        return data
    role = self._QuantosQ2Session__host._principals[self._QuantosQ2Session__principal][0]
    if role not in ('debug', 'evaluator'):
        data['capabilities'] = {k: v for k, v in data['capabilities'].items() if v.get('visibility', 'agent') == 'agent'}
    for cid, cap in _qtp_panel_caps().items():
        if cap['visibility'] == 'agent' or role in ('debug', 'evaluator'):
            data['capabilities'][cid] = cap
    return data


QuantosQ2Session.describe = _qtp_describe


def _qtp_invoke_panel(sess, iid, i, cid, args, rid):
    """panel.* commands: a gesture on the instance's terminal (engine + panel_ui.json), a screen capture, or the evaluator frame."""
    host = sess._QuantosQ2Session__host
    panel = _qtp_panel()
    definition = panel['caps'].get(cid)
    if definition is None:
        return sess._error(iid, 'UNKNOWN_CAPABILITY', rid)
    role = host._principals[sess._QuantosQ2Session__principal][0]
    if definition['visibility'] != 'agent' and role not in ('debug', 'evaluator'):
        return sess._error(iid, 'ACCESS_DENIED', rid)
    if panel['engine'].check_arguments(definition['input_schema'], args):
        return sess._error(iid, 'INVALID_ARGUMENT', rid)
    ui = host.panel(iid)
    try:
        if cid == 'panel.frame':
            return sess._response(iid, rid, result={'frame': ui.frame()})
        if cid == 'panel.capture':
            data, width, height = ui.png_bytes(args['display_id'])
            digest = _qtp_hashlib.sha256(data).hexdigest()
            folder = _qtp_Path(host.panel_capture_dir) if host.panel_capture_dir else _qtp_Path(_qtp_tempfile.gettempdir()) / 'chemistry_sim_panel' / iid
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ('%s_%s.png' % (args['display_id'], digest[:16]))
            if not path.exists():
                path.write_bytes(data)
            return sess._response(iid, rid, result={'display_id': args['display_id'], 'time_s': host.time_s,
                                                    'image': {'path': str(path), 'format': 'png', 'width': width, 'height': height, 'sha256': digest}})
        i['qtp']['last_activity_s'] = i['qtp']['last_key_s'] = host.time_s       # a key, touch or sensor is terminal activity (standby, dimming, key LEDs)
        ui.host.caller = sess._QuantosQ2Session__principal                        # the gesture acts as this session's caller (owns its dosing)
        try:
            if cid == 'panel.press':
                accepted = ui.press(args['input_id'], float(args.get('duration_s', 0.1)), held=args.get('held', ()))
            elif cid == 'panel.switch':
                accepted = ui.switch(args['input_id'], args['position'])
            else:
                to = (args['to_x'], args['to_y']) if 'to_x' in args else None
                accepted = ui.touch(args['display_id'], args['x'], args['y'], float(args.get('duration_s', 0.1)), to)
        finally:
            ui.host.caller = None
    except panel['engine'].PanelError:
        return sess._error(iid, 'INVALID_ARGUMENT', rid)
    return sess._response(iid, rid, result={'accepted': bool(accepted)})


def _qtp_level_warning_id(host, i):
    """Number of the level warning due now (p.83-84): None when level, the level sensor is off or Warning Text is Off; a new detection or,
    with Repeat, every 15 minutes gives a new number (the terminal shows the warning until OK)."""
    q, s = i['qtp'], i['state']
    mode = q['system']['level_warning_text']
    if s['balance.level_ok'] is not False or mode == 'Off' or q['level_lost_s'] is None:
        return None
    k = int((host.time_s - q['level_lost_s']) // QUANTOS_LEVEL_REPEAT_S) if mode == 'Repeat' else 0
    return q['level_events'] * 100000 + k


def _qtp_password_due(host, i):
    """Password change reminder (p.83): due from the set date on (the clock must be set)."""
    date, today = _qtp_parse_date(i['qtp']['system']['password_change_date'] or ''), _qtp_today(host, i)
    return date is not None and today is not None and today >= date


def _qtp_host_set_level(self, iid, *, bubble_x, bubble_y, evidence):
    """Trusted host (scene): where the air bubble of the level glass is, in level-glass radii (0, 0 = exactly level; p.17).  Turning the
    footscrews is a host / scene action; the level sensor (Levelcontrol) reports it, the weighing itself stays ideal."""
    with self._lock:
        i = self._instances.get(iid)
        if i is None or not all(_qt_number(v) and -1.0 <= v <= 1.0 for v in (bubble_x, bubble_y)) or not _qt_text(evidence):
            raise ValueError('Known instance, bubble offsets -1..1 and evidence required')
        i['qtp']['bubble'] = [float(bubble_x), float(bubble_y)]
        _qtp_sync(self, i)
        i['revision'] += 1


QuantosQ2Runtime.host_set_level = _qtp_host_set_level


class _QuantosPanelHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py for one instance: what the terminal firmware reads, calls and times by."""
    PRINCIPAL = '__qtp_panel__'

    def __init__(self, runtime, instance_id):
        self.runtime, self.instance_id = runtime, instance_id
        if self.PRINCIPAL not in runtime._principals:
            runtime._principals[self.PRINCIPAL] = ('agent', None)
        self.n = 0
        self.caller = None                                                    # principal of the session whose gesture is being handled

    def panel_read(self):
        r = self.runtime
        i = r._instances[self.instance_id]
        out = {sid: _qt_copy.deepcopy(v) for sid, v in i['state'].items() if _QT_SLOTS[sid]['visibility'] == 'agent'}
        q = i['qtp']
        out.update({'host.powered': i['state']['power.enabled'], 'host.power_epoch': q['power_epoch'], 'host.door_travel_m': i['door_travel_m'],
                    'host.container_position_id': i['position_id'], 'host.active_operation_id': i['active_operation'], 'host.last_activity_s': q['last_activity_s'],
                    'host.front_door_bound': i['adapter'] is not None and i['door_travel_m'] is not None,
                    'host.smarttrac_fraction': None if not (i['state']['measurement.balance_net_kg'] is not None and i['state']['dosing.target_mass_kg'])
                    else min(1.0, max(0.0, i['state']['measurement.balance_net_kg'] / (1.2 * i['state']['dosing.target_mass_kg']))),
                    'host.today': None if _qtp_today(r, i) is None else _qtp_today(r, i).isoformat(),
                    'host.last_key_s': q['last_key_s'], 'host.adjustment_list': _qt_copy.deepcopy(_qtp_adjustment_list(i)),
                    'host.bubble_x': q['bubble'][0], 'host.bubble_y': q['bubble'][1], 'host.level_warning_id': _qtp_level_warning_id(r, i),
                    'host.password_reminder_due': _qtp_password_due(r, i), 'host.fault': q['fault'], 'host.fault_seq': q['fault_seq']})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A terminal action takes the checked path of a direct call (same preconditions and revision) as the gesturing session's caller (a
        dosing started on the terminal belongs to it; [C] aborts only its own); the panel principal only without a session gesture."""
        r = self.runtime
        self.n += 1
        sess = r.session(self.caller or self.PRINCIPAL)
        i = r._instances[self.instance_id]
        response = sess._execute(self.instance_id, i, capability_id, dict(arguments), '__panel_%d' % self.n)
        if response['status'] not in ('succeeded', 'accepted', 'cancelled'):
            raise _qtp_panel()['engine'].PanelRejected(response['error']['code'])
        return response['result']

    def panel_time(self):
        return self.runtime.time_s
# END CHEM-174 QUANTOS FRONT PANEL

# BEGIN CHEM-175 OPENTRONS FLEX TOUCHSCREEN
# The robot software behind the Flex touchscreen (docs.opentrons.com Flex manual, saved under references/manuals/opentrons_flex_single_5_1000ul):
# stored protocols (sent by the Opentrons App = the host), Prepare to run (runtime parameters, instruments, deck hardware, labware offsets
# with Labware Position Check, labware and liquids), the run (one protocol command per simulated second, play / pause / stop, error
# recovery, run completion and history), quick transfers created on the touchscreen, deck configuration, instrument attach / detach /
# calibration, robot settings, deck lights, home / restart / shut down, device reset, file manager and the status light.  Protocol
# commands drive the same joint / volume runtime (motion.move, tip.attach / tip.release with the host's confirmations, sample.aspirate /
# sample.dispense); a run waits while a tip event awaits the host's confirmation.
import hashlib as _fts_hashlib
import tempfile as _fts_tempfile
from pathlib import Path as _fts_Path

OPENTRONS_FLEX_ASSET_DIR = _fts_Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t5_automation/opentrons_flex'
FLEX_PIPETTE_MODEL = 'Flex 1-Channel 1000 uL'
FLEX_HOME_POSITION = {'x_m': 0.24, 'y_m': 0.21, 'z_m': 0.22}              # back right of the working area, Z up (robot dashboard docs)
FLEX_COMMAND_S = 1.0                                                     # simulated time per protocol command (assumption)
FLEX_MAX_PROTOCOLS, FLEX_MAX_PINNED = 20, 8                              # protocol management / quick transfer docs
FLEX_FIXTURE_SLOTS = {'trash_bin': ('A1', 'B1', 'C1', 'D1', 'A3', 'B3', 'C3', 'D3'), 'staging_area': ('A3', 'B3', 'C3', 'D3'), 'waste_chute': ('D3',),
                      'waste_chute_staging': ('D3',)}                     # robot docs, deck fixtures table
FLEX_CUTOUTS = ('A1', 'B1', 'C1', 'D1', 'A3', 'B3', 'C3', 'D3')
FLEX_DEFAULT_FIXTURES = {'A1': 'trash_bin', 'A3': 'staging_area', 'B3': 'staging_area', 'C3': 'staging_area', 'D3': 'staging_area'}
FLEX_SLEEP_OPTIONS = ['never', 3, 5, 10, 15, 30, 60]
FLEX_SETTINGS = {'status_leds': True, 'brightness_level': 4, 'sleep_min': 'never', 'language': 'English', 'recovery_mode': True, 'home_on_restart': True,
                 'camera_photos': False, 'camera_video': False, 'camera_on_error': False, 'auto_updates': False, 'update_channel': 'stable',
                 'share_robot_logs': False, 'share_display_usage': False, 'developer_tools': False, 'download_protocol_source': False}
_FLEX_SETTING_RULES = {'status_leds': [True, False], 'brightness_level': [1, 2, 3, 4, 5, 6], 'sleep_min': FLEX_SLEEP_OPTIONS, 'language': ['English', 'Chinese'],
                       'recovery_mode': [True, False], 'home_on_restart': [True, False], 'camera_photos': [True, False], 'camera_video': [True, False],
                       'camera_on_error': [True, False], 'auto_updates': [True, False], 'update_channel': ['stable', 'beta'], 'share_robot_logs': [True, False],
                       'share_display_usage': [True, False], 'developer_tools': [True, False], 'download_protocol_source': [True, False]}
FLEX_RECOVERY = {'no_liquid_detected': ['retry_same_tips', 'retry_new_tips', 'skip_fill', 'ignore_skip', 'cancel'],
                 'overpressure_aspirate': ['retry_new_tips', 'cancel'], 'overpressure_dispense': ['skip_same_tips', 'skip_new_tips', 'cancel'],
                 'general': ['retry', 'skip', 'cancel']}                  # protocol run docs, error recovery table
_FLEX_STR = {'type': 'string', 'minLength': 1, 'maxLength': 64}
_FLEX_ANY_VALUE = {'anyOf': [{'type': 'boolean'}, {'type': 'number', 'x-unit': '1'}, {'type': 'string', 'maxLength': 64}]}

_FTS_SLOTS = {
    'robot.name': _flex_slot({'type': 'string'}, '1', 'setpoint', write_via={'capability_id': 'robot.set_name', 'parameter': 'name', 'fixed_arguments': {}}),
    'robot.settings': _flex_slot({'type': 'object'}, '1', 'setpoint'),
    'robot.lights_on': _flex_slot({'type': 'boolean'}, '1', 'setpoint', write_via={'capability_id': 'robot.set_lights', 'parameter': 'on', 'fixed_arguments': {}}),
    'robot.status': _flex_slot({'type': 'string', 'enum': ['off', 'idle', 'busy', 'setup', 'running', 'paused', 'awaiting_recovery', 'completed', 'failed', 'canceled']}, '1', 'derived'),
    'robot.status_light': _flex_slot({'type': 'object'}, '1', 'derived'),
    'deck.fixtures': _flex_slot({'type': 'object'}, '1', 'setpoint'),
    'instruments.mounts': _flex_slot({'type': 'object'}, '1', 'derived'),
    'protocols.list': _flex_slot({'type': 'array', 'items': {'type': 'object'}, 'maxItems': FLEX_MAX_PROTOCOLS}, '1', 'derived'),
    'run.state': _flex_slot({'anyOf': [{'type': 'object'}, {'type': 'null'}]}, '1', 'derived'),
    'run.history': _flex_slot({'type': 'array', 'items': {'type': 'object'}, 'maxItems': 50}, '1', 'derived'),
    'labware.offsets': _flex_slot({'type': 'object'}, '1', 'derived'),
    'files.records': _flex_slot({'type': 'array', 'items': {'type': 'object'}, 'maxItems': 50}, '1', 'derived'),
    'protocols.recent': _flex_slot({'type': 'array', 'items': {'type': 'object'}, 'maxItems': 8}, '1', 'derived'),
    'protocols.pinned': _flex_slot({'type': 'array', 'items': {'type': 'object'}, 'maxItems': FLEX_MAX_PINNED}, '1', 'derived'),
    'protocols.order': _flex_slot({'type': 'object'}, '1', 'derived'),
    'protocols.newest_id': _flex_slot({'anyOf': [{'type': 'string'}, {'type': 'null'}]}, '1', 'derived'),
    'usb.drive': _flex_slot({'anyOf': [{'type': 'object'}, {'type': 'null'}]}, '1', 'scene'),
    'door.open': _flex_slot({'type': 'boolean'}, '1', 'scene'),
}
_FLEX_SLOTS.update(_FTS_SLOTS)
_FTS_PUBLIC = tuple(_FTS_SLOTS)


def _fts_cap_specs():
    """New touchscreen commands: (input properties, effects, description, kind)."""
    pos = {k: _flex_num('m', -0.5, 0.5) for k in ('x_m', 'y_m', 'z_m')}
    run_fx = ['run.state', 'robot.status', 'robot.status_light']
    return {
        'robot.configure': ({'key': {'type': 'string', 'enum': sorted(FLEX_SETTINGS)}, 'value': _FLEX_ANY_VALUE}, ['robot.settings', 'robot.status_light'],
                            '修改一项机器人设置（Settings列表：状态灯、亮度6级、休眠、语言、恢复模式、重启归位、相机偏好、更新、隐私、开发者工具等）', 'configuration'),
        'robot.set_name': ({'name': {'type': 'string', 'minLength': 1, 'maxLength': 35}}, ['robot.name'], '修改机器人名称（显示在仪表盘左上角）', 'configuration'),
        'robot.set_lights': ({'on': {'type': 'boolean'}}, ['robot.lights_on'], '开关照亮工作区的LED灯（仪表盘⋮菜单）', 'device_command'),
        'robot.home': ({}, ['motion.x_m', 'motion.y_m', 'motion.z_m'], '龙门归位到工作区后右方原位（仪表盘⋮菜单Home gantry）', 'device_command'),
        'robot.restart': ({}, run_fx + ['protocols.list'], '软重启机器人（触摸屏重新启动；运行中不可）', 'device_command'),
        'robot.shutdown': ({}, ['power.enabled'] + run_fx, '关闭机器人软件（之后关后部电源开关）；运行中不可', 'device_command'),
        'robot.device_reset': ({'items': {'type': 'array', 'items': {'type': 'string', 'enum': ['calibration', 'protocols', 'run_history', 'offsets']}, 'minItems': 1, 'maxItems': 4}},
                               ['instruments.mounts', 'protocols.list', 'run.history', 'labware.offsets', 'files.records'], '批量删除校准、协议、运行历史或标定偏移（Device reset）', 'device_command'),
        'deck.configure': ({'slot': {'type': 'string', 'enum': list(FLEX_CUTOUTS)}, 'fixture': {'anyOf': [{'type': 'string', 'enum': sorted(FLEX_FIXTURE_SLOTS)}, {'type': 'null'}]}},
                           ['deck.fixtures', 'protocols.list'], '在甲板槽配置固定件（废料桶A1-D1/A3-D3、暂存区A3-D3→A4-D4、废料滑槽D3）或移除', 'configuration'),
        'instrument.calibrate': ({'mount': {'type': 'string', 'enum': ['left', 'right']}}, ['instruments.mounts', 'robot.status_light', 'protocols.list'],
                                 '自动校准已装移液器（校准探针由操作者装上；覆盖旧校准）', 'device_command'),
        'instrument.detach': ({'mount': {'type': 'string', 'enum': ['left', 'right']}}, ['instruments.mounts', 'robot.status_light', 'protocols.list'],
                              '拆下移液器（操作者松开固定螺钉取下；宿主负责实物）', 'device_command'),
        'instrument.attach': ({'mount': {'type': 'string', 'enum': ['left', 'right']}, 'model': {'type': 'string', 'enum': [FLEX_PIPETTE_MODEL]}},
                              ['instruments.mounts', 'robot.status_light', 'protocols.list'], '装上移液器（本资产只有1通道1000 µL移液器）；装后需校准', 'device_command'),
        'protocol.pin': ({'protocol_id': _FLEX_STR, 'pinned': {'type': 'boolean'}}, ['protocols.list'], '置顶或取消置顶协议（最多8个）', 'configuration'),
        'protocol.delete': ({'protocol_id': _FLEX_STR}, ['protocols.list', 'run.history'], '删除协议及其运行历史（不可恢复）', 'device_command'),
        'quick_transfer.create': ({'spec': {'type': 'object'}}, ['protocols.list'],
                                  '创建快速转移（吸头B2、源C2、目标D2、体积、名称与高级设置），存为协议', 'device_command'),
        'run.setup': ({'protocol_id': _FLEX_STR}, run_fx, '开始协议准备（Prepare to run）', 'device_command'),
        'run.set_parameter': ({'name': _FLEX_STR, 'value': _FLEX_ANY_VALUE}, ['run.state'], '设置运行时参数（确认前可改）', 'configuration'),
        'run.confirm_parameters': ({}, ['run.state'], '确认参数值（之后只读）', 'configuration'),
        'run.update_deck': ({}, ['deck.fixtures', 'run.state', 'protocols.list'], '按协议更新甲板配置以清除冲突（Update deck）', 'configuration'),
        'run.apply_offsets': ({}, ['run.state'], '应用已存的标定偏移（Apply Offsets）', 'configuration'),
        'run.lpc': ({'action': {'type': 'string', 'enum': ['start', 'save', 'exit']}}, ['run.state', 'labware.offsets', 'motion.x_m', 'motion.y_m', 'motion.z_m'],
                    'Labware Position Check：移到下一个耗材上方、保存当前对位为偏移或退出', 'device_command'),
        'run.confirm_placements': ({}, ['run.state'], '确认耗材位置（Confirm Placements）', 'configuration'),
        'run.play': ({}, run_fx, '开始或继续运行（准备完成后）', 'device_command'),
        'run.pause': ({}, run_fx, '暂停运行', 'device_command'),
        'run.stop': ({}, run_fx + ['run.history', 'files.records', 'protocols.list'], '取消运行', 'device_command'),
        'run.recover': ({'action': {'type': 'string', 'enum': sorted({a for v in FLEX_RECOVERY.values() for a in v})}}, run_fx + ['run.history', 'files.records'],
                        '错误恢复：按错误类型可用的选项（重试、换新吸头、跳过、忽略、取消）', 'device_command'),
        'run.close': ({}, run_fx, '运行结束后回到仪表盘（清除运行）', 'configuration'),
        'files.download': ({'file_id': _FLEX_STR}, ['usb.drive'], '把文件管理器中的文件下载到已插入的U盘', 'device_command'),
    }


_fts_export_v1 = export_opentrons_flex_contract_metadata
_FTS_PANEL = {}


def export_opentrons_flex_contract_metadata(panel=False):
    """Type metadata with the touchscreen's robot software; panel=True adds the panel.* capabilities (separate group)."""
    meta = _fts_export_v1()
    caps, slots = meta['capabilities'], meta['state_slots']
    template = caps['power.set']
    for cid, (props, effects, desc, kind) in _fts_cap_specs().items():
        cap = _flex_copy.deepcopy(template)
        cap.update({'availability': 'implemented', 'visibility': 'agent', 'kind': kind, 'description_zh': desc, 'input_schema': _flex_obj(_flex_copy.deepcopy(props)),
                    'output_schema': _flex_obj({sid: _flex_copy.deepcopy(slots[sid]['value_schema']) for sid in effects[:1]}),
                    'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': '原子提交此状态；拒绝不改变状态'} for sid in effects],
                    'observes': effects[:1], 'preconditions_zh': ['已上电（关机后需开电源）；按触摸屏流程的前提（见运行层说明）'],
                    'example_arguments': [_fts_example(cid)],
                    'binding': "basic40_runtime_automation.OpentronsFlexSession.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % cid,
                    'model_effects': {'mode': 'none', 'binding_ids': [], 'description_zh': '机器人软件状态；运动经motion.move同步场景，屏幕由panel.capture渲染。'},
                    'implementation': {'process': 'implemented', 'scene': 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'implemented'}})
        caps[cid] = cap
    caps['power.set']['state_effects'] += [{'state_id': s_, 'timing': 'immediate', 'description_zh': '开机/关机：机器人状态、状态灯；关机时进行中的运行被取消'}
                                           for s_ in ('robot.status', 'robot.status_light', 'run.state', 'run.history', 'files.records', 'protocols.list')]
    caps['run.play']['state_effects'] += [{'state_id': s, 'timing': 'on_step', 'description_zh': '宿主时钟推进时逐条执行协议命令'} for s in
                                          ('motion.x_m', 'motion.y_m', 'motion.z_m', 'tip.attached_id', 'tip.last_slot_id', 'tip.events', 'sample.transfer_count', 'run.history', 'files.records')]
    meta['limitations'] = list(meta['limitations']) + [
        '触摸屏（panel.*）与其背后的机器人软件已实现：存储协议（由宿主代替Opentrons App发送）、运行准备（参数、仪器、甲板硬件、标定偏移与Labware Position Check、耗材与液体）、'
        '运行（每条命令1 s仿真时间，播放/暂停/停止、错误恢复、完成与历史）、快速转移、甲板配置、仪器装拆与校准、设置、照明、归位/重启/关机、Device reset、文件管理与状态灯。',
        '协议命令经同一运行层执行：吸头拾取/释放仍需宿主确认（运行等待），吸排为体积账本；“No liquid detected”以供体体积不足近似，过压与一般错误由宿主注入；流速、吸头位置、触壁、气隙、吹出只记录不改变账本。',
        '不可用：网络设置与软件更新（无网络）、Stacker传感器与加密密钥（无该硬件/软件）、多通道/96通道/夹爪与模块；相机实时视频只在Opentrons App（宿主）中观看、机器人不保存；CSV参数只取宿主放入U盘的文件名。',
        '标定偏移作用于运行运动：应用偏移（Apply Offsets或完成Labware Position Check）后，运行到协议耗材槽位的拾取/吸排位置加上该耗材的偏移；Labware Position Check从检查位置'
        '（耗材上方10 mm，假设）起算，不点动保存即零偏移。仿真耗材正好在登记位置，故只有零偏移落在耗材上；非零偏移使移液器到耗材旁、吸排被拒并报运行错误（真实耗材的孔径容差不建模）。',
        '设置“Home gantry on restart”（出厂开）：触摸屏Restart/robot.restart与机器人电源开关开机后龙门归位；直接power.set是1.0关节运行层的仿真供电，保持原位姿（兼容）。',
        '内置相机：协议的相机步骤（capture_image，Still photographs开时）与运行错误（On-error image capture开时）在运行文件中记录一张甲板图像（files.records中kind=image）；'
        '图像像素由场景宿主渲染（过程层只记录事件）。',
        '运行属于设置它的调用者（run.setup，触摸屏手势按发出手势的会话身份）：运行在准备、进行、暂停或等待恢复时，其他会话的运行命令（播放/暂停/停止/恢复/准备步骤/新的准备）、'
        '触摸屏重启、关机与Device reset，以及运行进行中的直接运动与吸排都被拒（ACCESS_DENIED，状态不变），触摸屏弹出提示窗（文字为假设）；评估者不受限。'
        '后部电源开关（power.set即此开关）是硬开关：任何人都可操作，运行如断电般停止。']
    meta['asset_version'] = '1.2.0'  # 1.2.0 (2026-10-20): touchscreen glass and rear power switch in the 3D model (geometry only)
    if panel:
        meta['panel_capabilities'] = _fts_panel_caps()
    return meta


def _fts_example(cid):
    return {'robot.configure': {'key': 'recovery_mode', 'value': True}, 'robot.set_name': {'name': 'flex-lab-1'}, 'robot.set_lights': {'on': True},
            'robot.device_reset': {'items': ['run_history']}, 'deck.configure': {'slot': 'A1', 'fixture': 'trash_bin'},
            'instrument.calibrate': {'mount': 'left'}, 'instrument.detach': {'mount': 'left'}, 'instrument.attach': {'mount': 'left', 'model': FLEX_PIPETTE_MODEL},
            'protocol.pin': {'protocol_id': 'p1', 'pinned': True}, 'protocol.delete': {'protocol_id': 'p1'},
            'quick_transfer.create': {'spec': {'name': 'Fill plate', 'source_wells': ['src_1'], 'destination_wells': ['dst_1'], 'volume_m3': 1e-7}},
            'run.setup': {'protocol_id': 'p1'}, 'run.set_parameter': {'name': 'volume_ul', 'value': 50}, 'run.lpc': {'action': 'start'},
            'run.recover': {'action': 'retry'}, 'files.download': {'file_id': 'run_1'}}.get(cid, {})


def _fts_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _FTS_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if '_chemsim_panel_ui' not in sys.modules:
                spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', OPENTRONS_FLEX_ASSET_DIR.parents[3] / 'scripts/asset_runtime/_panel_ui.py')
                sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
            engine = sys.modules['_chemsim_panel_ui']
        caps = set(export_opentrons_flex_contract_metadata()['capabilities'])
        spec = engine.load_spec(OPENTRONS_FLEX_ASSET_DIR / 'panel_ui.json', capabilities=caps)
        _FTS_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _FTS_PANEL


def _fts_panel_caps():
    template = _fts_export_v1()['capabilities']['power.set']
    out = {}
    for cid, d in _fts_panel()['caps'].items():
        cap = _flex_copy.deepcopy(template)
        cap.update({'availability': 'implemented', 'visibility': d['visibility'], 'kind': d['kind'], 'description_zh': d['function_zh'], 'component_ids': d['component_ids'],
                    'input_schema': d['input_schema'], 'output_schema': d['output_schema'], 'state_effects': [], 'observes': [], 'preconditions_zh': [],
                    'example_arguments': [_flex_copy.deepcopy(d['example_arguments'])],
                    'binding': "basic40_runtime_automation.OpentronsFlexSession.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % cid,
                    'model_effects': {'mode': 'none', 'binding_ids': [], 'description_zh': '面板手势调用同一运行层的能力；画面由panel.capture渲染。'},
                    'implementation': {'process': 'implemented', 'scene': 'not_applicable', 'robot': 'absent', 'hardware': 'absent', 'host': 'implemented'}})
        out[cid] = cap
    return out


# ---- instance state
_flex_create_v1 = OpentronsFlexRuntime.create_instance


def _fts_create_instance(self, instance_id, asset_type_id=OPENTRONS_FLEX_TYPE):
    with self._lock:
        _flex_create_v1(self, instance_id, asset_type_id)
        item = self._instances[instance_id]
        item['ts'] = {'settings': dict(FLEX_SETTINGS), 'name': 'Opentrons Flex', 'lights': False, 'fixtures': dict(FLEX_DEFAULT_FIXTURES),
                      'mounts': {'left': {'model': FLEX_PIPETTE_MODEL, 'calibrated': True, 'last_calibrated_s': None}, 'right': None, 'extension': None},
                      'protocols': [], 'run': None, 'offsets': {}, 'history': [], 'files': [], 'power_epoch': 0, 'last_touch_s': self.time_s,
                      'waste': {}, 'inject': None, 'done_until': None, 'busy_until': None, 'usb': None, 'door_open': False, 'counter': 0}
        _fts_sync(self, item)


def _fts_protocol_view(item, p):
    ts = item['ts']
    keys = ('protocol_id', 'name', 'kind', 'author', 'description', 'added_s', 'last_run_s', 'pinned', 'parameters', 'fixtures', 'labware', 'liquids', 'pipette_mount')
    out = {k: _flex_copy.deepcopy(p.get(k)) for k in keys}
    out['n_steps'] = len(p['commands'])
    out['ready'] = _fts_instruments_ok(ts, p) and all(s == 'Configured' for s in _fts_deck_status(ts, p).values())
    return out


def _fts_instruments_ok(ts, p):
    m = ts['mounts'].get(p.get('pipette_mount', 'left'))
    return m is not None and m['model'] == FLEX_PIPETTE_MODEL and m['calibrated']


def _fts_deck_status(ts, p):
    """Fixture statuses of a protocol against the deck configuration (deck-config docs: Configured / Location conflict / Not configured)."""
    out = {}
    for slot, fixture in (p.get('fixtures') or {}).items():
        have = ts['fixtures'].get(slot)
        out[slot] = 'Configured' if have == fixture else 'Not configured' if have is None else 'Location conflict'
    return out


def _fts_offsets_ok(ts, p):
    return p.get('kind') == 'quick_transfer' or all(lw['name'] in ts['offsets'] for lw in p.get('labware', []))


def _fts_status(host, item):
    ts, s = item['ts'], item['state']
    if not s['power.enabled']:
        return 'off'
    run = ts['run']
    if run is not None:
        return {'setup': 'setup', 'running': 'running', 'paused': 'paused', 'awaiting_recovery': 'awaiting_recovery', 'succeeded': 'completed',
                'failed': 'failed', 'stopped': 'canceled'}[run['status']]
    return 'busy' if ts['busy_until'] is not None and host.time_s < ts['busy_until'] else 'idle'


def _fts_light(host, item):
    """Status light (robot docs): white solid idle / pulsing busy; green blink x2 done, solid running, pulsing complete; blue pulsing paused;
    yellow solid software error, pulsing recovery; red blink x3 physical error (not modelled)."""
    ts = item['ts']
    st_ = _fts_status(host, item)
    if st_ == 'off' or not ts['settings']['status_leds']:
        return {'color': 'off', 'pattern': 'off'}
    if ts['done_until'] is not None and host.time_s < ts['done_until'] and st_ in ('idle', 'setup'):
        return {'color': 'green', 'pattern': 'blink_twice'}
    color, pattern = {'idle': ('white', 'solid'), 'busy': ('white', 'pulsing'), 'setup': ('white', 'pulsing'), 'running': ('green', 'solid'),
                      'paused': ('blue', 'pulsing'), 'awaiting_recovery': ('yellow', 'pulsing'), 'completed': ('green', 'pulsing'), 'failed': ('yellow', 'solid'),
                      'canceled': ('white', 'solid')}[st_]
    return {'color': color, 'pattern': pattern}


def _fts_run_view(item):
    ts = item['ts']
    run = ts['run']
    if run is None:
        return None
    p = _fts_protocol(ts, run['protocol_id'])
    out = {k: _flex_copy.deepcopy(run[k]) for k in ('protocol_id', 'name', 'status', 'params', 'params_confirmed', 'placements_confirmed', 'offsets_applied', 'index',
                                                    'error', 'resolved_errors', 'started_s', 'ended_s', 'lpc')}
    out['n_steps'] = len(run['commands'])
    out['current_step'] = _fts_step_text(run['commands'][run['index']]) if run['commands'] and run['index'] < len(run['commands']) else None
    out['steps'] = [_fts_step_text(c) for c in run['commands']]
    out['steps_from_current'] = [{'text': x} for x in out['steps'][run['index']:]]      # the current step at the top of the list
    out['waiting_for_host'] = None if run['waiting'] is None else {k: run['waiting'][k] for k in ('event', 'tip_id', 'slot_id')}
    out['recovery_options'] = list(FLEX_RECOVERY.get((run['error'] or {}).get('type'), [])) if run['status'] == 'awaiting_recovery' else []
    if p is not None:
        out['param_rows'] = [dict(_flex_copy.deepcopy(par), value=_flex_copy.deepcopy(run['params'].get(par['name'])), index=n) for n, par in enumerate(p['parameters'])]
        out['deck_rows'] = [{'slot': k, 'fixture': v, 'status': _fts_deck_status(ts, p)[k]} for k, v in sorted(p['fixtures'].items())]
        out['offset_rows'] = [{'name': lw['name'], 'slot': lw['slot'], 'offset': _flex_copy.deepcopy(ts['offsets'].get(lw['name']))} for lw in p.get('labware', [])]
        out['deck_ok'] = all(v == 'Configured' for v in _fts_deck_status(ts, p).values())
        out['instruments_ok'] = _fts_instruments_ok(ts, p)
        out['deck_status'] = _fts_deck_status(ts, p)
        out['offsets_ok'] = _fts_offsets_ok(ts, p) or run['offsets_applied']
        out['offsets_missing'] = [lw['name'] for lw in p.get('labware', []) if lw['name'] not in ts['offsets']] if p.get('kind') != 'quick_transfer' else []
        out['ready'] = (run['params_confirmed'] or not p.get('parameters')) and out['instruments_ok'] and all(v == 'Configured' for v in out['deck_status'].values()) and out['offsets_ok']
    return out


def _fts_step_text(c):
    k = c['cmd']
    if k == 'pick_up_tip':
        return 'Picking up tip %s' % c['tip_id']
    if k == 'drop_tip':
        return 'Dropping tip in %s' % c['slot_id']
    if k in ('aspirate', 'dispense'):
        return '%s %.1f uL %s %s' % ('Aspirating' if k == 'aspirate' else 'Dispensing', (c['volume_m3'] if isinstance(c['volume_m3'], (int, float)) else 0.0) * 1e9,
                                     'from' if k == 'aspirate' else 'into', c['container_id'])
    if k == 'delay':
        return 'Delaying %.1f s' % c['s']
    if k == 'move':
        return 'Moving'
    return c.get('text', k)


def _fts_sync(host, item):
    ts, s = item['ts'], item['state']
    s['robot.name'] = ts['name']
    s['robot.settings'] = _flex_copy.deepcopy(ts['settings'])
    s['robot.lights_on'] = ts['lights']
    s['deck.fixtures'] = _flex_copy.deepcopy(ts['fixtures'])
    s['instruments.mounts'] = _flex_copy.deepcopy(ts['mounts'])
    s['protocols.list'] = views = [_fts_protocol_view(item, p) for p in ts['protocols']]
    s['protocols.recent'] = sorted([v for v in views if v['last_run_s'] is not None], key=lambda v: -v['last_run_s'])[:8]      # dashboard carousel
    s['protocols.pinned'] = [v for v in views if v['pinned']]
    others = [v for v in views if not v['pinned']]
    keys = {'name': lambda v: v['name'].lower(), 'last_run': lambda v: (v['last_run_s'] is None, v['last_run_s'] or 0.0), 'added': lambda v: v['added_s']}
    s['protocols.order'] = {f'{k}_{d}': sorted(others, key=f, reverse=d == 'desc') for k, f in keys.items() for d in ('asc', 'desc')}
    s['protocols.newest_id'] = max(ts['protocols'], key=lambda x: x['seq'])['protocol_id'] if ts['protocols'] else None
    s['run.state'] = _fts_run_view(item)
    s['run.history'] = _flex_copy.deepcopy(ts['history'][-50:])
    s['labware.offsets'] = _flex_copy.deepcopy(ts['offsets'])
    s['files.records'] = _flex_copy.deepcopy(ts['files'][-50:])
    s['usb.drive'] = None if ts['usb'] is None else dict(_flex_copy.deepcopy(ts['usb']), file_objects=[{'id': f, 'name': f} for f in ts['usb']['files']])
    s['door.open'] = ts['door_open']
    s['robot.status'] = _fts_status(host, item)
    s['robot.status_light'] = _fts_light(host, item)


def _fts_protocol(ts, pid):
    return next((p for p in ts['protocols'] if p['protocol_id'] == pid), None)


def _fts_check_protocol(item, p):
    """Host-sent protocol (what the Opentrons App transfers): name, fixtures, labware, liquids, parameters, tip order and commands."""
    if not isinstance(p, dict) or not _flex_id(p.get('protocol_id')) or not _flex_id(p.get('name')):
        raise ValueError('protocol_id and name required')
    for slot, fixture in (p.get('fixtures') or {}).items():
        if fixture not in FLEX_FIXTURE_SLOTS or slot not in FLEX_FIXTURE_SLOTS[fixture]:
            raise ValueError('fixture %r not allowed in %r' % (fixture, slot))
    for par in p.get('parameters') or []:
        if par.get('type') not in ('bool', 'int', 'float', 'str', 'csv') or not _flex_id(par.get('name')):
            raise ValueError('parameter')
    for c in p.get('commands') or []:
        if c.get('cmd') not in ('pick_up_tip', 'drop_tip', 'aspirate', 'dispense', 'delay', 'move', 'comment', 'capture_image'):
            raise ValueError('command %r' % c.get('cmd'))
        for k_, v_ in c.items():                                          # parameter references: {param}, {param, unit} or {param, scale}
            if isinstance(v_, dict) and 'param' in v_:
                if set(v_) not in ({'param'}, {'param', 'unit'}, {'param', 'scale'}) or \
                        ('unit' in v_ and (k_ != 'volume_m3' or v_['unit'] not in _FTS_PARAM_UNITS)) or ('scale' in v_ and not _flex_number(v_['scale'])):
                    raise ValueError('parameter reference %r in %r' % (v_, c.get('cmd')))
    return dict(_flex_copy.deepcopy(p), kind=p.get('kind', 'protocol'), pipette_mount=p.get('pipette_mount', 'left'), fixtures=dict(p.get('fixtures') or {}),
                labware=list(p.get('labware') or []), liquids=list(p.get('liquids') or []), parameters=list(p.get('parameters') or []), tips=list(p.get('tips') or []),
                commands=list(p.get('commands') or []))


def _fts_store(host, item, p):
    ts = item['ts']
    ts['protocols'] = [x for x in ts['protocols'] if x['protocol_id'] != p['protocol_id']]
    ts['counter'] += 1
    p.update(added_s=host.time_s, seq=ts['counter'], last_run_s=None, pinned=False)
    ts['protocols'].append(p)
    while len(ts['protocols']) > FLEX_MAX_PROTOCOLS:                      # Flex deletes older protocols beyond 20
        oldest = min(ts['protocols'], key=lambda x: (x['added_s'], x['seq']))
        ts['protocols'].remove(oldest)
    ts['done_until'] = host.time_s + 2.0                                  # green blinks twice: protocol stored


def _fts_host_store_protocol(self, instance_id, protocol):
    """Trusted host = the Opentrons App sending a protocol file to the robot."""
    with self._lock:
        item = self._host_instance(instance_id)
        p = _fts_check_protocol(item, protocol)
        _fts_store(self, item, p)
        _fts_sync(self, item)
        item['revision'] += 1
        return p['protocol_id']


def _fts_host_register_waste(self, instance_id, *, slot_id, position_m, evidence):
    """Trusted host: the drop position of the trash bin / waste chute in a configured slot (tips dropped there need the host's release confirmation)."""
    with self._lock:
        item = self._host_instance(instance_id)
        if slot_id not in FLEX_CUTOUTS or not self._position_valid(position_m) or not _flex_id(evidence):
            raise ValueError('Cutout slot, valid joint position and evidence required')
        item['ts']['waste'][slot_id] = _flex_copy.deepcopy(position_m)
        item['revision'] += 1


def _fts_host_inject_error(self, instance_id, *, kind):
    """Trusted host: the next aspirate / dispense meets pipette overpressure, or the next command a general error (error recovery tests)."""
    with self._lock:
        item = self._host_instance(instance_id)
        if kind not in ('overpressure', 'general'):
            raise ValueError('overpressure or general')
        item['ts']['inject'] = kind


def _fts_host_set_usb_drive(self, instance_id, files):
    """Trusted host: a USB drive in the front port with these file names (None: removed)."""
    with self._lock:
        item = self._host_instance(instance_id)
        if files is not None and (not isinstance(files, list) or any(not _flex_id(f) for f in files)):
            raise ValueError('List of file names or None')
        item['ts']['usb'] = None if files is None else {'files': list(files)}
        _fts_sync(self, item)
        item['revision'] += 1


def _fts_host_set_door(self, instance_id, *, open_):
    with self._lock:
        item = self._host_instance(instance_id)
        item['ts']['door_open'] = bool(open_)
        _fts_sync(self, item)
        item['revision'] += 1


def _fts_host_set_container_volume(self, instance_id, container_id, *, volume_m3, evidence):
    """Trusted host: an operator fills (or empties) a well by hand, e.g. before 'Manually fill well and retry' in error recovery."""
    with self._lock:
        item = self._host_instance(instance_id)
        c = item['containers'].get(container_id)
        if c is None or not _flex_number(volume_m3) or not 0 <= volume_m3 <= c['capacity_m3'] or not _flex_id(evidence):
            raise ValueError('Known container, volume within capacity and evidence required')
        c['volume_m3'] = float(volume_m3)
        item['evidence'][container_id + ':refill'] = evidence
        self._sync_inventory(item)
        item['revision'] += 1


def _fts_host_pending_tip_event(self, instance_id):
    """Trusted host: the tip event a run waits for (event, tip, slot, current joint position), or None."""
    with self._lock:
        item = self._host_instance(instance_id)
        run = item['ts']['run']
        if run is None or run['waiting'] is None:
            return None
        return dict(_flex_copy.deepcopy(run['waiting']), position_m=self._position(item))


# ---- protocol execution (one command per FLEX_COMMAND_S of the host clock)
def _fts_internal(host, item, iid, cid, args):
    return host._apply(item, iid, cid, args, None)


def _fts_offset_target(item, run, slot_id, position):
    """Where a run sends the pipette for labware in this slot: its position plus the stored labware offset once the offsets are applied
    (Apply Offsets or a finished Labware Position Check).  The simulated labware sits exactly at its registered position, so only a zero offset
    lands on it: a run with a non-zero offset acts beside the labware and the robot reports an error."""
    if not run.get('offsets_applied') or slot_id is None:
        return position
    p = _fts_protocol(item['ts'], run['protocol_id'])
    name = next((lw['name'] for lw in (p or {}).get('labware', []) if lw['slot'] == slot_id), None)
    off = item['ts']['offsets'].get(name) if name is not None else None
    if not off:
        return position
    return {k: round(position[k] + off[k], 12) for k in ('x_m', 'y_m', 'z_m')}


def _fts_image(host, item, trigger, **extra):
    """A still image of the deck from the built-in camera (flex/opentrons-app/camera): an image file of the run (pixels: the scene host)."""
    ts = item['ts']
    n = sum(1 for f in ts['files'] if f.get('kind') == 'image') + 1
    ts['files'].append(dict({'file_id': 'image_%d' % n, 'name': 'deck image %d (%s)' % (n, trigger), 'kind': 'image', 'trigger': trigger, 'time_s': host.time_s,
                             'image': None}, **extra))
    ts['files'][:] = ts['files'][-50:]


def _fts_exec(host, item, iid, run, c):
    """One command: 'done', 'wait' (host confirmation of a tip event) or ('error', type)."""
    ts, s = item['ts'], item['state']
    inject = ts['inject']
    if inject == 'general':
        ts['inject'] = None
        return ('error', 'general')
    k = c['cmd']
    if k in ('comment', 'delay'):
        return 'done'
    if k == 'capture_image':                                                  # camera step (Protocol Designer / capture_image())
        if ts['settings']['camera_photos']:
            _fts_image(host, item, 'step', run_name=run['name'])
        return 'done'
    if k == 'move':
        return 'done' if _fts_internal(host, item, iid, 'motion.move', c['position_m'])['status'] == 'succeeded' else ('error', 'general')
    if k == 'pick_up_tip':
        tip = item['tips'].get(c['tip_id'])
        if tip is None or tip['slot_id'] is None:
            return ('error', 'general')
        if s['tip.attached_id'] is not None:
            return ('error', 'general')
        target = _fts_offset_target(item, run, tip['slot_id'], tip['position_m'])
        if not host._position_matches(host._position(item), target):
            if _fts_internal(host, item, iid, 'motion.move', target)['status'] != 'succeeded':
                return ('error', 'general')
        r = _fts_internal(host, item, iid, 'tip.attach', {'tip_id': c['tip_id']})
        if r['status'] == 'succeeded':
            run['current_tip'] = c['tip_id']
            run['waiting'] = None
            return 'done'
        run['waiting'] = {'event': 'attach', 'tip_id': c['tip_id'], 'slot_id': tip['slot_id']}
        return 'wait'
    if k == 'drop_tip':
        tip_id = s['tip.attached_id']
        if tip_id is None:
            return 'done'
        target = ts['waste'].get(c['slot_id']) if c.get('position_m') is None else c['position_m']
        if target is None:
            return ('error', 'general')
        if not host._position_matches(host._position(item), target):
            if _fts_internal(host, item, iid, 'motion.move', target)['status'] != 'succeeded':
                return ('error', 'general')
        r = _fts_internal(host, item, iid, 'tip.release', {'target_slot_id': c['slot_id']})
        if r['status'] == 'succeeded':
            run['waiting'] = None
            run['current_tip'] = None
            return 'done'
        run['waiting'] = {'event': 'release', 'tip_id': tip_id, 'slot_id': c['slot_id']}
        return 'wait'
    if k in ('aspirate', 'dispense'):
        if inject == 'overpressure':
            ts['inject'] = None
            return ('error', 'overpressure_' + k)
        cont = item['containers'].get(c['container_id'])
        if cont is None or s['tip.attached_id'] is None:
            return ('error', 'general')
        target = _fts_offset_target(item, run, cont['slot_id'], cont['position_m'])
        if not host._position_matches(host._position(item), target):
            if _fts_internal(host, item, iid, 'motion.move', target)['status'] != 'succeeded':
                return ('error', 'general')
        vol = c['volume_m3']
        if k == 'aspirate' and cont['volume_m3'] < vol:
            return ('error', 'no_liquid_detected')
        r = _fts_internal(host, item, iid, 'sample.' + k, {'container_id': c['container_id'], 'volume_m3': vol})
        return 'done' if r['status'] == 'succeeded' else ('error', 'general')
    return ('error', 'general')


def _fts_end(host, item, status, error=None):
    ts = item['ts']
    run = ts['run']
    run['status'], run['ended_s'] = status, host.time_s
    run['waiting'] = None
    p = _fts_protocol(ts, run['protocol_id'])
    if p is not None:
        p['last_run_s'] = run['started_s']
    rec = {'run_id': 'run_%d' % (len(ts['history']) + 1), 'protocol_id': run['protocol_id'], 'name': run['name'], 'status': status, 'started_s': run['started_s'],
           'ended_s': host.time_s, 'steps_done': run['index'], 'n_steps': len(run['commands']), 'errors': _flex_copy.deepcopy(run['resolved_errors'] + ([run['error']] if run['error'] else []))}
    ts['history'].append(rec)
    ts['files'].append({'file_id': rec['run_id'], 'name': 'run record %s' % run['name'], 'kind': 'run_record', 'time_s': host.time_s})


def _fts_advance(host, iid, item):
    ts = item['ts']
    run = ts['run']
    changed = False
    for _ in range(10000):
        if run is None or run['status'] != 'running' or run['next_t'] > host.time_s + 1e-9:
            break
        if not item['state']['power.enabled']:
            break
        if run['index'] >= len(run['commands']):
            _fts_end(host, item, 'succeeded')
            changed = True
            break
        c = run['commands'][run['index']]
        out = _fts_exec(host, item, iid, run, c)
        changed = True
        if out == 'wait':
            break
        if out == 'done':
            run['index'] += 1
            run['next_t'] += c.get('s', FLEX_COMMAND_S) if c['cmd'] == 'delay' else FLEX_COMMAND_S   # keep the schedule, so one long clock jump runs every command due in it
            continue
        kind = out[1]
        run['error'] = {'type': kind, 'index': run['index'], 'step': _fts_step_text(c), 'time_s': host.time_s}
        if ts['settings']['camera_on_error']:                                 # on-error image capture (camera preferences)
            _fts_image(host, item, 'error', run_name=run['name'], error=kind)
        if ts['settings']['recovery_mode']:
            run['status'] = 'awaiting_recovery'
        else:
            _fts_end(host, item, 'failed')
        break
    if changed:
        _fts_sync(host, item)
        item['revision'] += 1
    return changed


_flex_set_clock_v1 = OpentronsFlexRuntime.host_set_clock
_flex_confirm_v1 = OpentronsFlexRuntime.host_confirm_tip_event


def _fts_set_clock(self, time_s):
    with self._lock:
        before = self.time_s
        _flex_set_clock_v1(self, time_s)
        for iid, item in self._instances.items():
            if 'ts' not in item:
                continue
            ts = item['ts']
            if _fts_advance(self, iid, item):
                continue
            light = item['state']['robot.status_light']
            status = item['state']['robot.status']
            if self.time_s != before and (light != _fts_light(self, item) or status != _fts_status(self, item)):
                _fts_sync(self, item)
                item['revision'] += 1
            if ts['busy_until'] is not None and self.time_s >= ts['busy_until']:
                ts['busy_until'] = None


def _fts_confirm(self, instance_id, *, event, tip_id, slot_id, position_m, evidence):
    with self._lock:
        _flex_confirm_v1(self, instance_id, event=event, tip_id=tip_id, slot_id=slot_id, position_m=position_m, evidence=evidence)
        item = self._instances[instance_id]
        run = item.get('ts', {}).get('run')
        if run is not None and run['status'] == 'running' and run['waiting'] is not None:
            run['next_t'] = self.time_s                                    # the waiting step ends now; the next one is due a step later
            _fts_advance(self, instance_id, item)


OpentronsFlexRuntime.create_instance = _fts_create_instance
OpentronsFlexRuntime.host_set_clock = _fts_set_clock
OpentronsFlexRuntime.host_confirm_tip_event = _fts_confirm
OpentronsFlexRuntime.host_store_protocol = _fts_host_store_protocol
OpentronsFlexRuntime.host_register_waste = _fts_host_register_waste
OpentronsFlexRuntime.host_inject_error = _fts_host_inject_error
OpentronsFlexRuntime.host_set_usb_drive = _fts_host_set_usb_drive
OpentronsFlexRuntime.host_set_door = _fts_host_set_door
OpentronsFlexRuntime.host_pending_tip_event = _fts_host_pending_tip_event
OpentronsFlexRuntime.host_set_container_volume = _fts_host_set_container_volume


# ---- quick transfer (quick transfer docs)
def _fts_trash_slot(ts):
    return next((s_ for s_, f in sorted(ts['fixtures'].items()) if f in ('trash_bin', 'waste_chute', 'waste_chute_staging')), None)


def _fts_quick_transfer(host, item, spec):
    """Validate a quick transfer and build its commands: tips from B2, source wells in C2, destinations in D2, the configured trash."""
    ts = item['ts']
    req = {'name', 'source_wells', 'destination_wells', 'volume_m3'}
    opt = {'liquid_class': 'aqueous', 'path': 'single', 'change_tip': 'once', 'tip_drop': 'trash', 'pre_wet': False, 'mix': None, 'delay_s': None,
           'touch_tip': False, 'air_gap_m3': None, 'blowout': None, 'aspirate_flow_m3_s': None, 'dispense_flow_m3_s': None, 'tip_height_m': 0.001}
    if not isinstance(spec, dict) or not req <= set(spec) or not set(spec) <= req | set(opt):
        raise ValueError('spec keys')
    q = dict(opt, **spec)
    tips = [t for t in sorted(item['tips'].values(), key=lambda t: t['tip_id']) if t['slot_id'] == 'B2']
    src = q['source_wells']
    dst = q['destination_wells']
    if not (isinstance(src, list) and isinstance(dst, list) and src and dst) or any(item['containers'].get(w, {}).get('slot_id') != 'C2' for w in src) \
            or any(item['containers'].get(w, {}).get('slot_id') != 'D2' for w in dst):
        raise ValueError('wells: sources in C2, destinations in D2')
    if q['liquid_class'] not in ('aqueous', 'viscous', 'volatile') or q['path'] not in ('single', 'multi_aspirate', 'multi_dispense') \
            or q['change_tip'] not in ('once', 'always', 'per_source') or q['tip_drop'] not in ('trash', 'return') or q['blowout'] not in (None, 'trash', 'source', 'destination'):
        raise ValueError('option')
    vol = q['volume_m3']
    cap = min([t['capacity_m3'] for t in tips] or [0.0])
    if not _flex_number(vol) or not OPENTRONS_FLEX_MIN_M3 <= vol <= min(OPENTRONS_FLEX_MAX_M3, cap or OPENTRONS_FLEX_MAX_M3) or not tips:
        raise ValueError('volume or tips')
    trash = _fts_trash_slot(ts)
    if q['tip_drop'] == 'trash' and (trash is None or trash not in ts['waste']):
        raise LookupError('trash')
    if q['path'] == 'single':
        if len(src) == len(dst):
            pairs = list(zip(src, dst))
        elif len(src) == 1:
            pairs = [(src[0], d) for d in dst]
        elif len(dst) == 1:
            pairs = [(s_, dst[0]) for s_ in src]
        else:
            raise ValueError('single transfer: equal well counts, or one source / destination')
    elif q['path'] == 'multi_aspirate':
        if len(dst) != 1:
            raise ValueError('multi-aspirate: many wells to one well')
        pairs = [(s_, dst[0]) for s_ in src]
    else:
        if len(src) != 1:
            raise ValueError('multi-dispense: one well to many wells')
        pairs = [(src[0], d) for d in dst]
    cmds, tip_iter, used = [], iter(tips), []

    def new_tip():
        if cmds and any(c['cmd'] == 'pick_up_tip' for c in cmds):
            cmds.append(drop())
        t = next(tip_iter, None)
        if t is None:
            raise ValueError('not enough tips in B2')
        used.append(t['tip_id'])
        cmds.append({'cmd': 'pick_up_tip', 'tip_id': t['tip_id']})

    def drop():
        if q['tip_drop'] == 'return':
            t = item['tips'][used[-1]]
            return {'cmd': 'drop_tip', 'slot_id': 'B2', 'position_m': _flex_copy.deepcopy(t['position_m'])}
        return {'cmd': 'drop_tip', 'slot_id': trash}

    def asp(w, v):
        if q['delay_s']:
            cmds.append({'cmd': 'delay', 's': float(q['delay_s'])})
        cmds.append({'cmd': 'aspirate', 'container_id': w, 'volume_m3': v})
        if q['air_gap_m3']:
            cmds.append({'cmd': 'comment', 'text': 'Air gap %.1f uL' % (q['air_gap_m3'] * 1e9)})

    def disp(w, v):
        if q['delay_s']:
            cmds.append({'cmd': 'delay', 's': float(q['delay_s'])})
        cmds.append({'cmd': 'dispense', 'container_id': w, 'volume_m3': v})
        if q['mix']:
            for _ in range(int(q['mix']['repeats'])):
                cmds.append({'cmd': 'aspirate', 'container_id': w, 'volume_m3': q['mix']['volume_m3']})
                cmds.append({'cmd': 'dispense', 'container_id': w, 'volume_m3': q['mix']['volume_m3']})
        if q['touch_tip']:
            cmds.append({'cmd': 'comment', 'text': 'Touch tip'})
        if q['blowout']:
            cmds.append({'cmd': 'comment', 'text': 'Blowout into %s' % q['blowout']})

    def prewet(w):
        if q['pre_wet']:
            v = min(cap * 2.0 / 3.0, item['containers'][w]['volume_m3'])
            if v >= OPENTRONS_FLEX_MIN_M3:
                cmds.append({'cmd': 'aspirate', 'container_id': w, 'volume_m3': round(v, 15)})
                cmds.append({'cmd': 'dispense', 'container_id': w, 'volume_m3': round(v, 15)})

    last_src = None
    if q['path'] == 'multi_dispense':
        per_tip = max(1, int(cap // vol))
        for n in range(0, len(pairs), per_tip):
            if n == 0 or q['change_tip'] in ('always',):
                new_tip()
            if n == 0:
                prewet(pairs[0][0])
            chunk = pairs[n:n + per_tip]
            asp(chunk[0][0], round(vol * len(chunk), 15))
            for _, d in chunk:
                disp(d, vol)
    elif q['path'] == 'multi_aspirate':
        per_tip = max(1, int(cap // vol))
        for n in range(0, len(pairs), per_tip):
            if n == 0 or q['change_tip'] in ('always', 'per_source'):
                new_tip()
            chunk = pairs[n:n + per_tip]
            for s_, _ in chunk:
                asp(s_, vol)
            disp(chunk[0][1], round(vol * len(chunk), 15))
    else:
        for s_, d in pairs:
            if not used or q['change_tip'] == 'always' or (q['change_tip'] == 'per_source' and s_ != last_src):
                new_tip()
                prewet(s_)
            asp(s_, vol)
            disp(d, vol)
            last_src = s_
    cmds.append(drop())
    return {'protocol_id': 'qt_%d' % (ts['counter'] + 1), 'name': q['name'], 'kind': 'quick_transfer', 'author': None,
            'description': 'Quick transfer (%s, %s, %.1f uL)' % (q['path'], q['liquid_class'], vol * 1e9), 'pipette_mount': 'left',
            'fixtures': {trash: ts['fixtures'][trash]} if q['tip_drop'] == 'trash' else {}, 'labware': [{'slot': 'B2', 'name': 'tip rack'}, {'slot': 'C2', 'name': 'source'},
                                                                                                         {'slot': 'D2', 'name': 'destination'}],
            'liquids': [], 'parameters': [], 'tips': used, 'commands': cmds, 'settings': _flex_copy.deepcopy(q)}


# ---- commands
_flex_invoke_v1 = OpentronsFlexRuntime._invoke


def _fts_home_on_restart(host, item, iid):
    """Robot settings 'Home gantry on restart' (on by default): the gantry homes any time Flex turns on (the robot's power switch) or restarts
    (touchscreen Restart / robot.restart).  The direct power.set of the 1.0 joint runtime is the simulation supply and keeps its pose."""
    if item['ts']['settings']['home_on_restart'] and item['state']['power.enabled']:
        if _fts_internal(host, item, iid, 'motion.move', dict(FLEX_HOME_POSITION))['status'] == 'succeeded':
            item['ts']['busy_until'] = max(item['ts']['busy_until'] or 0.0, host.time_s + 2.0)


_FTS_FOREIGN_RUN_CMDS = ('run.setup', 'run.set_parameter', 'run.confirm_parameters', 'run.update_deck', 'run.apply_offsets', 'run.lpc', 'run.confirm_placements',
                         'run.play', 'run.pause', 'run.stop', 'run.recover', 'robot.restart', 'robot.shutdown', 'robot.device_reset')
_FTS_FOREIGN_MOTIONS = ('motion.move', 'sample.aspirate', 'sample.dispense', 'tip.attach', 'tip.release', 'velocity.set', 'sample.mix', 'liquid_level.detect')


def _fts_foreign_run(self, principal, iid, cid, args):
    """Another caller's protocol run is set up, running, paused or awaiting recovery: its run commands (play / pause / stop / recover / setup
    steps or a new setup), the touchscreen restart / shut down / device reset, and - while it runs - direct motions and liquid handling are
    refused (lead rule 2026-10-19: ACCESS_DENIED on the touchscreen and the direct path); the evaluator / debug identities are unrestricted.
    The rear power switch (power.set models it) is a hard switch: never refused, the run stops as at a power loss."""
    item = self._instances.get(iid) if isinstance(iid, str) else None
    run = item['ts']['run'] if item is not None and 'ts' in item else None
    if run is None or run['status'] not in ('setup', 'running', 'paused', 'awaiting_recovery') or run.get('owner') in (None, principal):
        return False
    if principal not in self._principals or principal == '__flex_panel__' or self._principals[principal][0] in ('debug', 'evaluator'):
        return False                                                          # the touchscreen's own actions (no gesturing caller) are not foreign
    return cid in _FTS_FOREIGN_RUN_CMDS or (cid in _FTS_FOREIGN_MOTIONS and run['status'] != 'setup')


def _fts_invoke(self, principal, iid, cid, args, rid):
    with self._lock:
        news = _fts_cap_specs()
        if isinstance(cid, str) and _fts_foreign_run(self, principal, iid, cid, args) and self._instance(iid, principal) is not None:
            key = (principal, iid, rid)
            try:
                fingerprint = _flex_json.dumps([cid, args], sort_keys=True, allow_nan=False, separators=(',', ':'))
            except (ValueError, TypeError, OverflowError):
                return self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
            if key in self._requests:
                previous, response = self._requests[key]
                return _flex_copy.deepcopy(response) if previous == fingerprint else self._response(iid, rid, status='rejected', code='REQUEST_CONFLICT')
            response = self._response(iid, rid, status='rejected', code='ACCESS_DENIED')
            if _flex_id(rid):
                self._requests[key] = (fingerprint, _flex_copy.deepcopy(response))
            return response
        if not isinstance(cid, str) or (cid not in news and not cid.startswith('panel.')):
            response = _flex_invoke_v1(self, principal, iid, cid, args, rid)
            item = self._instances.get(iid) if isinstance(iid, str) else None
            if item is not None and 'ts' in item and response['status'] == 'succeeded' and cid in ('power.set', 'motion.move', 'tip.attach', 'tip.release',
                                                                                                    'sample.aspirate', 'sample.dispense'):
                if cid == 'power.set' and args.get('enabled') is True:
                    item['ts']['power_epoch'] += 1
                if cid == 'power.set' and args.get('enabled') is False and item['ts']['run'] is not None and item['ts']['run']['status'] in ('running', 'paused', 'awaiting_recovery'):
                    _fts_end(self, item, 'stopped')
                _fts_sync(self, item)
            return response
        item = self._instance(iid, principal)
        if item is None:
            response = self._response(None, rid, status='rejected', code='UNKNOWN_INSTANCE')
            response['instance_id'] = iid if isinstance(iid, str) else None
            return response
        if not _flex_id(rid):
            return self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
        try:
            fingerprint = _flex_json.dumps([cid, args], sort_keys=True, allow_nan=False, separators=(',', ':'))
        except (ValueError, TypeError, OverflowError):
            return self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
        key = (principal, iid, rid)
        if key in self._requests:
            previous, response = self._requests[key]
            return _flex_copy.deepcopy(response) if previous == fingerprint else self._response(iid, rid, status='rejected', code='REQUEST_CONFLICT')
        response = _fts_panel_invoke(self, principal, iid, item, cid, args, rid) if cid.startswith('panel.') else _fts_apply(self, iid, item, cid, args, rid, principal)
        self._requests[key] = (fingerprint, _flex_copy.deepcopy(response))
        return response


def _fts_apply(self, iid, item, cid, args, rid, principal=None):
    """A touchscreen command on the robot software (direct call or panel) by principal (the run it sets up belongs to it)."""
    props = _fts_cap_specs()[cid][0]
    fail = lambda code='PRECONDITION_FAILED': self._response(iid, rid, status='rejected', code=code)      # noqa: E731
    if not isinstance(args, dict) or set(args) != set(props) or not all(_qtp_valid(props[k], args[k]) for k in props):
        return fail('INVALID_ARGUMENT')
    ts, s = item['ts'], item['state']
    if not s['power.enabled']:
        return fail()
    run = ts['run']
    active = run is not None and run['status'] in ('running', 'paused', 'awaiting_recovery')
    state_before = _flex_copy.deepcopy((item['ts'], item['state']))
    try:
        if cid == 'robot.configure':
            if args['value'] not in _FLEX_SETTING_RULES[args['key']] or isinstance(args['value'], bool) != isinstance(_FLEX_SETTING_RULES[args['key']][0], bool):
                return fail('INVALID_ARGUMENT')
            ts['settings'][args['key']] = args['value']
        elif cid == 'robot.set_name':
            ts['name'] = args['name']
        elif cid == 'robot.set_lights':
            ts['lights'] = args['on']
        elif cid == 'robot.home':
            if active:
                return fail('BUSY')
            r = _fts_internal(self, item, iid, 'motion.move', dict(FLEX_HOME_POSITION))
            if r['status'] != 'succeeded':
                return r
            ts['busy_until'] = self.time_s + 2.0
        elif cid in ('robot.restart', 'robot.shutdown'):
            if active:
                return fail('BUSY')
            ts['run'] = None
            if cid == 'robot.shutdown':
                s['power.enabled'] = False
            else:
                ts['power_epoch'] += 1
                ts['busy_until'] = self.time_s + 5.0
                _fts_home_on_restart(self, item, iid)
        elif cid == 'robot.device_reset':
            if active:
                return fail('BUSY')
            if 'calibration' in args['items']:
                for m in ts['mounts'].values():
                    if m is not None:
                        m.update(calibrated=False, last_calibrated_s=None)
            if 'protocols' in args['items']:
                ts['protocols'], ts['run'] = [], None
            if 'run_history' in args['items']:
                ts['history'], ts['files'] = [], []
            if 'offsets' in args['items']:
                ts['offsets'] = {}
        elif cid == 'deck.configure':
            slot, fix = args['slot'], args['fixture']
            if active:
                return fail('BUSY')
            if fix is not None and slot not in FLEX_FIXTURE_SLOTS[fix]:
                return fail()
            if fix is None:
                ts['fixtures'].pop(slot, None)
            else:
                ts['fixtures'][slot] = fix
        elif cid in ('instrument.calibrate', 'instrument.detach', 'instrument.attach'):
            if active:
                return fail('BUSY')
            m = ts['mounts'][args['mount']]
            if cid == 'instrument.attach':
                if m is not None or any(x is not None for k, x in ts['mounts'].items() if k in ('left', 'right')):
                    return fail()                                         # the asset has one 1-channel pipette
                ts['mounts'][args['mount']] = {'model': args['model'], 'calibrated': False, 'last_calibrated_s': None}
            elif m is None:
                return fail()
            elif cid == 'instrument.detach':
                if s['tip.attached_id'] is not None:
                    return fail()
                ts['mounts'][args['mount']] = None
            else:
                m.update(calibrated=True, last_calibrated_s=self.time_s)
            ts['done_until'] = self.time_s + 2.0
        elif cid == 'protocol.pin':
            p = _fts_protocol(ts, args['protocol_id'])
            if p is None or (args['pinned'] and not p['pinned'] and sum(x['pinned'] for x in ts['protocols']) >= FLEX_MAX_PINNED):
                return fail()
            p['pinned'] = args['pinned']
        elif cid == 'protocol.delete':
            p = _fts_protocol(ts, args['protocol_id'])
            if p is None or (run is not None and run['protocol_id'] == p['protocol_id']):
                return fail()
            ts['protocols'].remove(p)
            ts['history'] = [h for h in ts['history'] if h['protocol_id'] != p['protocol_id']]
        elif cid == 'quick_transfer.create':
            try:
                p = _fts_quick_transfer(self, item, args['spec'])
            except LookupError:
                return fail()
            _fts_store(self, item, _fts_check_protocol(item, p))
        elif cid == 'run.setup':
            p = _fts_protocol(ts, args['protocol_id'])
            if p is None or active:
                return fail()
            ts['run'] = {'protocol_id': p['protocol_id'], 'name': p['name'], 'status': 'setup', 'owner': principal,
                         'params': {x['name']: _flex_copy.deepcopy(x.get('default')) for x in p['parameters']},
                         'params_confirmed': False, 'placements_confirmed': False, 'offsets_applied': False, 'index': 0, 'commands': [], 'next_t': self.time_s,
                         'waiting': None, 'error': None, 'resolved_errors': [], 'started_s': None, 'ended_s': None, 'current_tip': None, 'lpc': None}
        elif cid.startswith('run.'):
            if run is None:
                return fail()
            p = _fts_protocol(ts, run['protocol_id'])
            st_ = run['status']
            if cid == 'run.set_parameter':
                par = next((x for x in (p or {}).get('parameters', []) if x['name'] == args['name']), None)
                v = args['value']
                if st_ != 'setup' or run['params_confirmed'] or par is None:
                    return fail()
                if not _fts_param_ok(par, v, ts):
                    return fail('INVALID_ARGUMENT')
                run['params'][args['name']] = v
            elif cid == 'run.confirm_parameters':
                if st_ != 'setup':
                    return fail()
                run['params_confirmed'] = True
            elif cid == 'run.update_deck':
                if st_ != 'setup' or p is None:
                    return fail()
                ts['fixtures'].update(p['fixtures'])
            elif cid == 'run.apply_offsets':
                if st_ != 'setup' or p is None or not _fts_offsets_ok(ts, p):
                    return fail()
                run['offsets_applied'] = True
            elif cid == 'run.confirm_placements':
                if st_ != 'setup':
                    return fail()
                run['placements_confirmed'] = True
            elif cid == 'run.lpc':
                if st_ != 'setup' or p is None:
                    return fail()
                r = _fts_lpc(self, iid, item, run, p, args['action'])
                if r is not None:
                    return r
            elif cid == 'run.play':
                view = _fts_run_view(item)
                if st_ == 'setup':
                    if not view['ready'] or s['tip.attached_id'] is not None:
                        return fail()
                    run['commands'] = [_fts_resolve(c, run['params']) for c in p['commands']]
                    run.update(status='running', index=0, next_t=self.time_s + FLEX_COMMAND_S, started_s=self.time_s)
                elif st_ == 'paused':
                    run.update(status='running', next_t=self.time_s + FLEX_COMMAND_S)
                else:
                    return fail()
            elif cid == 'run.pause':
                if st_ != 'running':
                    return fail()
                run['status'] = 'paused'
            elif cid == 'run.stop':
                if st_ not in ('running', 'paused', 'awaiting_recovery', 'setup'):
                    return fail()
                if st_ == 'setup':
                    ts['run'] = None
                else:
                    _fts_end(self, item, 'stopped')
            elif cid == 'run.recover':
                if st_ != 'awaiting_recovery' or args['action'] not in FLEX_RECOVERY[run['error']['type']]:
                    return fail()
                _fts_recover(self, item, run, args['action'])
            elif cid == 'run.close':
                if st_ not in ('succeeded', 'failed', 'stopped'):
                    return fail()
                ts['run'] = None
        elif cid == 'files.download':
            if ts['usb'] is None or not any(f['file_id'] == args['file_id'] for f in ts['files']):
                return fail()
            ts['usb']['files'] = ts['usb']['files'] + [args['file_id']]
    except ValueError:
        item['ts'], item['state'] = state_before
        return fail('INVALID_ARGUMENT')
    _fts_sync(self, item)
    item['revision'] += 1
    _fts_advance(self, iid, item)
    eff = _fts_cap_specs()[cid][1]
    return self._response(iid, rid, result={eff[0]: _flex_copy.deepcopy(item['state'][eff[0]])})


def _fts_param_ok(par, v, ts):
    t = par['type']
    if par.get('choices'):
        return v in [c['value'] if isinstance(c, dict) else c for c in par['choices']]
    if t == 'bool':
        return isinstance(v, bool)
    if t in ('int', 'float'):
        ok = _flex_number(v) and not isinstance(v, bool) and (t == 'float' or float(v).is_integer())
        return ok and par.get('min', -1e300) <= v <= par.get('max', 1e300)
    if t == 'csv':
        return ts['usb'] is not None and v in ts['usb']['files']
    return isinstance(v, str)


_FTS_PARAM_UNITS = {'uL': 1e-9, 'mL': 1e-6}                              # protocol parameter given in uL / mL for a volume_m3 field


def _fts_resolve(c, params):
    out = _flex_copy.deepcopy(c)
    for k_, v_ in list(out.items()):
        if isinstance(v_, dict) and 'param' in v_ and v_['param'] not in params:
            raise ValueError('protocol parameter %r is not defined' % v_['param'])     # run.play refuses with INVALID_ARGUMENT
        if isinstance(v_, dict) and set(v_) == {'param'}:
            out[k_] = params[v_['param']]
        elif isinstance(v_, dict) and set(v_) == {'param', 'unit'}:
            out[k_] = float(params[v_['param']]) * _FTS_PARAM_UNITS[v_['unit']]
        elif isinstance(v_, dict) and set(v_) == {'param', 'scale'}:
            out[k_] = float(params[v_['param']]) * v_['scale']
    return out


def _fts_lpc_approach(nom):
    """The Labware Position Check position: above the labware, 10 mm up (assumption); a save with no jog stores a zero offset."""
    return dict(nom, z_m=min(_FLEX_LIMITS['z_m'][1], nom['z_m'] + 0.01))


def _fts_lpc(self, iid, item, run, p, action):
    """Labware Position Check: start moves above the first labware, save stores (current - nominal) as its default offset and moves on."""
    ts = item['ts']
    labware = [lw for lw in p.get('labware', [])]
    nominal = lambda slot: next((c['position_m'] for c in sorted(list(item['containers'].values()) + list(item['tips'].values()),        # noqa: E731
                                                                 key=lambda x: x.get('container_id', x.get('tip_id'))) if c['slot_id'] == slot), None)
    if action == 'exit':
        run['lpc'] = None
        return None
    if action == 'start':
        run['lpc'] = {'index': 0, 'count': len(labware), 'labware': labware[0]['name'] if labware else None}
    else:
        if run['lpc'] is None:
            return self._response(iid, None, status='rejected', code='PRECONDITION_FAILED')
        lw = labware[run['lpc']['index']]
        nom = nominal(lw['slot'])
        if nom is None:
            return self._response(iid, None, status='rejected', code='PRECONDITION_FAILED')
        pos, approach = self._position(item), _fts_lpc_approach(nom)
        ts['offsets'][lw['name']] = {k: round(pos[k] - approach[k], 9) for k in ('x_m', 'y_m', 'z_m')}   # the jog from the check position
        run['lpc']['index'] += 1
        if run['lpc']['index'] >= len(labware):
            run['lpc'] = None
            run['offsets_applied'] = True
            return None
        run['lpc']['labware'] = labware[run['lpc']['index']]['name']
    lw = labware[run['lpc']['index']] if labware else None
    nom = nominal(lw['slot']) if lw else None
    if nom is not None:
        r = _fts_internal(self, item, iid, 'motion.move', _fts_lpc_approach(nom))
        if r['status'] != 'succeeded':
            return r
    return None


def _fts_recover(self, item, run, action):
    """Error recovery options: retry (the same command), new tips (drop to the trash, the next unused tip of the protocol), skip, cancel."""
    ts = item['ts']
    run['resolved_errors'].append(dict(run['error'], action=action))
    err, run['error'] = run['error'], None
    if action == 'cancel':
        run['resolved_errors'][-1]['action'] = 'cancel'
        _fts_end(self, item, 'stopped')
        return
    idx = err['index']
    if action in ('skip_fill', 'ignore_skip', 'skip', 'skip_same_tips', 'skip_new_tips'):
        idx += 1
    if action in ('retry_new_tips', 'skip_new_tips'):
        p = _fts_protocol(ts, run['protocol_id'])
        used = {c['tip_id'] for c in run['commands'] if c['cmd'] == 'pick_up_tip'}
        spare = next((t for t in (p or {}).get('tips', []) + sorted(item['tips']) if t not in used and item['tips'].get(t, {}).get('slot_id') is not None), None)
        trash = _fts_trash_slot(ts)
        if spare is not None and trash is not None:
            run['commands'][idx:idx] = [{'cmd': 'drop_tip', 'slot_id': trash}, {'cmd': 'pick_up_tip', 'tip_id': spare}]
    run.update(index=idx, status='running', next_t=self.time_s + FLEX_COMMAND_S)


def _fts_panel_invoke(self, principal, iid, item, cid, args, rid):
    panel = _fts_panel()
    definition = panel['caps'].get(cid)
    if definition is None:
        return self._response(iid, rid, status='rejected', code='UNKNOWN_CAPABILITY')
    if definition['visibility'] != 'agent' and self._principals[principal][0] not in ('debug', 'evaluator'):
        return self._response(iid, rid, status='rejected', code='ACCESS_DENIED')
    if panel['engine'].check_arguments(definition['input_schema'], args):
        return self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
    ui = self.panel(iid)
    try:
        if cid == 'panel.frame':
            return self._response(iid, rid, result={'frame': ui.frame()})
        if cid == 'panel.capture':
            data, width, height = ui.png_bytes(args['display_id'])
            digest = _fts_hashlib.sha256(data).hexdigest()
            folder = _fts_Path(self.panel_capture_dir) if self.panel_capture_dir else _fts_Path(_fts_tempfile.gettempdir()) / 'chemistry_sim_panel' / iid
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ('%s_%s.png' % (args['display_id'], digest[:16]))
            if not path.exists():
                path.write_bytes(data)
            return self._response(iid, rid, result={'display_id': args['display_id'], 'time_s': self.time_s,
                                                    'image': {'path': str(path), 'format': 'png', 'width': width, 'height': height, 'sha256': digest}})
        ui.host.caller = principal                                            # the gesture acts as this session's caller (owns the run it sets up)
        try:
            if cid == 'panel.touch':
                item['ts']['last_touch_s'] = self.time_s
                to = (args['to_x'], args['to_y']) if 'to_x' in args else None
                accepted = ui.touch(args['display_id'], args['x'], args['y'], float(args.get('duration_s', 0.1)), to)
            elif cid == 'panel.switch':
                accepted = ui.switch(args['input_id'], args['position'])
            else:
                accepted = ui.press(args['input_id'], float(args.get('duration_s', 0.1)))
        finally:
            ui.host.caller = None
    except panel['engine'].PanelError:
        return self._response(iid, rid, status='rejected', code='INVALID_ARGUMENT')
    return self._response(iid, rid, result={'accepted': bool(accepted)})


OpentronsFlexRuntime._invoke = _fts_invoke
_flex_visible_v1 = OpentronsFlexSession._visible


def _fts_visible(self):
    return _flex_visible_v1(self) | set(_FTS_PUBLIC)


OpentronsFlexSession._visible = _fts_visible
_flex_describe_v1 = OpentronsFlexSession.describe


def _fts_describe(self, *, asset_type_id=None, instance_id=None):
    out = _flex_describe_v1(self, asset_type_id=asset_type_id, instance_id=instance_id)
    if isinstance(out, dict) and 'capabilities' in out:
        role = self._OpentronsFlexSession__host._principals[self._OpentronsFlexSession__principal][0]
        for cid, cap in _fts_panel_caps().items():
            if cap['visibility'] == 'agent' or role in ('debug', 'evaluator'):
                out['capabilities'][cid] = cap
    return out


OpentronsFlexSession.describe = _fts_describe


def _fts_runtime_panel(self, instance_id):
    """The touchscreen (panel engine) of an instance (trusted host / tests: render, frame); created on first use."""
    with self._lock:
        if not hasattr(self, '_panels'):
            self._panels, self.panel_capture_dir = {}, None
        self._host_instance(instance_id)
        if instance_id not in self._panels:
            self._panels[instance_id] = _fts_panel()['engine'].PanelUI(_fts_panel()['spec'], _FlexPanelHost(self, instance_id))
        return self._panels[instance_id]


OpentronsFlexRuntime.panel = _fts_runtime_panel
OpentronsFlexRuntime.panel_capture_dir = None


class _FlexPanelHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py for one instance: what the touchscreen software reads, calls and times by."""
    PRINCIPAL = '__flex_panel__'

    def __init__(self, runtime, instance_id):
        self.runtime, self.instance_id = runtime, instance_id
        if self.PRINCIPAL not in runtime._principals:
            runtime._principals[self.PRINCIPAL] = ('agent', None)
        self.n = 0
        self.caller = None                                                    # principal of the session whose gesture is being handled

    def panel_read(self):
        r = self.runtime
        item = r._instances[self.instance_id]
        out = {sid: _flex_copy.deepcopy(v) for sid, v in item['state'].items() if sid in _FLEX_PUBLIC or sid in _FTS_PUBLIC}
        ts = item['ts']
        out.update({'host.powered': item['state']['power.enabled'], 'host.power_epoch': ts['power_epoch'], 'host.last_touch_s': ts['last_touch_s'],
                    'host.qt_tips': sorted(t['tip_id'] for t in item['tips'].values() if t['slot_id'] == 'B2'),
                    'host.qt_sources': [{'id': c['container_id'], 'name': c['container_id']} for c in sorted(item['containers'].values(), key=lambda c: c['container_id'])
                                        if c['slot_id'] == 'C2'],
                    'host.qt_destinations': [{'id': c['container_id'], 'name': c['container_id']} for c in sorted(item['containers'].values(), key=lambda c: c['container_id'])
                                             if c['slot_id'] == 'D2'],
                    'host.trash_slot': _fts_trash_slot(ts), 'host.trash_registered': _fts_trash_slot(ts) in ts['waste'],
                    'host.position': r._position(item)})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A touchscreen action takes the checked path of a direct call as the gesturing session's caller (the run it sets up belongs to it; another
        caller's run refuses it); the panel principal only without a session gesture.  Panel request ids never replay or occupy the caller's own."""
        r = self.runtime
        principal = self.caller or self.PRINCIPAL
        self.n += 1
        while (principal, self.instance_id, '__panel_%d' % self.n) in r._requests:
            self.n += 1
        key = (principal, self.instance_id, '__panel_%d' % self.n)
        response = r._invoke(principal, self.instance_id, capability_id, dict(arguments), key[2])
        r._requests.pop(key, None)
        if response['status'] not in ('succeeded', 'accepted'):
            raise _fts_panel()['engine'].PanelRejected(response['error']['code'])
        if capability_id == 'power.set' and arguments.get('enabled') is True:     # the robot's power switch: home gantry on restart
            item = r._instances[self.instance_id]
            _fts_home_on_restart(r, item, self.instance_id)
            _fts_sync(r, item)
        return response['result']

    def panel_time(self):
        return self.runtime.time_s
# END CHEM-175 OPENTRONS FLEX TOUCHSCREEN

# BEGIN CHEM-182 VICI EUTA MANUAL REMOTE
# The manual remote of the VICI universal actuator (operating manual rev 1.20, PDF pages: remote p.6, setup mode p.26-27, factory mode p.28)
# and the actuator settings behind its setup menu: actuator mode (two position with / without stops, multiposition), number of ports /
# positions, move direction, offset, learning the stops, serial baud rate, device ID and legacy mode.  Valve moves from the remote use the
# existing valve.select_position (the host confirms arrival as before).
import hashlib as _vr_hashlib
import string as _vr_string
import tempfile as _vr_tempfile
from pathlib import Path as _vr_Path

VICI_EUTA_ASSET_DIR = _vr_Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t5_automation/vici_euta'
VICI_EUTA_MODES = ['two_position_stops', 'two_position_no_stops', 'multiposition']                 # AM1 / AM2 / AM3 (p.9-10)
VICI_EUTA_DIRECTIONS = ['counterclockwise', 'clockwise', 'shortest']                               # setup menu Move Direction (p.27)
VICI_EUTA_BAUDS = [4800, 9600, 19200, 38400, 57600, 115200]                                       # p.12, p.27
VICI_EUTA_IDS = list(_vr_string.digits + _vr_string.ascii_uppercase)                              # ID 0-9, A-Z; '*' = none (p.8)
VICI_EUTA_SETTINGS = {'actuator.mode': 'multiposition', 'actuator.positions': 6, 'actuator.direction': 'shortest', 'actuator.offset': 1,
                      'actuator.stops_learned': False, 'interface.baud_rate': 9600, 'interface.device_id': None, 'interface.legacy_mode': False}
_VR_PANEL = {}


def _vr_slot(schema, unit, origin, components, cap=None, arg=None):
    return {'value_schema': schema, 'unit': unit, 'origin': origin, 'visibility': 'agent', 'access': 'read_only' if cap is None else 'read_write',
            'component_ids': components, 'write_via': None if cap is None else {'capability_id': cap, 'argument': arg, 'fixed_arguments': {}},
            'reference_conditions': None, 'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}


_veuta_export_v1 = export_vici_euta_contract_metadata


def export_vici_euta_contract_metadata(panel=False):
    """Type metadata with the setup-menu settings of the actuator; panel=True adds the panel.* capabilities (separate group)."""
    meta = _veuta_export_v1()
    integer = lambda lo, hi: {'type': 'integer', 'minimum': lo, 'maximum': hi, 'x-unit': '1'}       # noqa: E731
    slots = meta['state_slots']
    slots.update({
        'actuator.mode': _vr_slot({'type': 'string', 'enum': list(VICI_EUTA_MODES)}, '1', 'setpoint', ['actuator']),
        'actuator.positions': _vr_slot(integer(2, 40), '1', 'setpoint', ['actuator']),
        'actuator.direction': _vr_slot({'type': 'string', 'enum': list(VICI_EUTA_DIRECTIONS)}, '1', 'setpoint', ['actuator']),
        'actuator.offset': _vr_slot(integer(1, 94), '1', 'setpoint', ['actuator']),
        'actuator.stops_learned': _vr_slot({'type': 'boolean'}, '1', 'derived', ['actuator']),
        'interface.baud_rate': _vr_slot({'type': 'integer', 'enum': list(VICI_EUTA_BAUDS), 'x-unit': '1/s'}, '1/s', 'setpoint', ['actuator']),
        'interface.device_id': _vr_slot({'anyOf': [{'type': 'string', 'enum': list(VICI_EUTA_IDS)}, {'type': 'null'}]}, '1', 'setpoint', ['actuator']),
        'interface.legacy_mode': _vr_slot({'type': 'boolean'}, '1', 'setpoint', ['actuator']),
        'valve.move_path': _vr_slot({'anyOf': [{'type': 'array', 'items': integer(1, 40), 'minItems': 1, 'maxItems': 39}, {'type': 'null'}]}, '1', 'derived',
                                    ['actuator', 'rotor']),
        'valve.move_direction': _vr_slot({'anyOf': [{'type': 'string', 'enum': ['positive', 'negative']}, {'type': 'null'}]}, '1', 'derived', ['actuator', 'rotor']),
        'valve.move_time_s': _vr_slot({'anyOf': [{'type': 'number', 'minimum': 0, 'x-unit': 's'}, {'type': 'null'}]}, 's', 'derived', ['actuator']),
        'valve.position_number': _vr_slot({'anyOf': [integer(1, 95), {'type': 'null'}]}, '1', 'derived', ['actuator'])})
    select = meta['capabilities']['valve.select_position']
    select['input_schema']['properties']['position'] = integer(1, 95)         # serial numbering: SO .. SO+NP-1 (p.16), at most 96 - 1
    select['description_zh'] = ('按串口/BCD位置编号选择（多位模式下执行器只响应SO至SO+NP-1，SO即阀的1号位，p.16、p.22；两位模式1/2=A/B）；'
                                '立即撤销到位并断开抽象气路，成功不是切换完成。valve.target_position与valve.position为阀本身的位置1..6。')
    select['state_effects'] += ['valve.move_path', 'valve.move_direction', 'valve.move_time_s', 'valve.position_number']
    meta['host_capabilities']['host.arrive']['state_effects'] += ['valve.position_number']
    spp = {'anyOf': [{'type': 'number', 'exclusiveMinimum': 0, 'maximum': 60, 'x-unit': 's'}, {'type': 'null'}]}
    meta['host_capabilities']['host.configure_motion'] = {
        'input_schema': _veuta_obj({'seconds_per_position': spp}), 'output_schema': _veuta_obj({'seconds_per_position': _veuta_copy.deepcopy(spp)}),
        'example_arguments': {'seconds_per_position': 0.1}, 'state_effects': [], 'component_ids': ['actuator'], 'visibility': 'debug',
        'description_zh': '可信宿主给出每移动一个位置的时间（场景假设；本EUTA的切换时间未知），之后每次选择的valve.move_time_s=路径步数×该时间；null为未知。不自动到位。',
        'availability': 'implemented'}
    value = {'anyOf': [{'type': 'string', 'maxLength': 32}, {'type': 'integer', 'minimum': 1, 'maximum': 115200, 'x-unit': '1'}, {'type': 'boolean'}, {'type': 'null'}]}
    move = ['valve.target_position', 'valve.position', 'valve.in_position', 'route.connected_port_id', 'route.connections', 'valve.position_number']

    def cap(inputs, outputs, example, effects, description):
        return {'input_schema': _veuta_obj(inputs), 'output_schema': _veuta_obj({k: _veuta_copy.deepcopy(slots[k]['value_schema']) for k in outputs}),
                'example_arguments': example, 'state_effects': effects, 'component_ids': ['actuator'], 'visibility': 'agent', 'description_zh': description,
                'availability': 'implemented'}
    meta['capabilities'].update({
        'actuator.configure': cap({'key': {'type': 'string', 'enum': ['mode', 'positions', 'direction', 'offset']}, 'value': value},
                                  ['actuator.mode', 'actuator.positions', 'actuator.direction', 'actuator.offset'], {'key': 'direction', 'value': 'clockwise'},
                                  ['actuator.mode', 'actuator.positions', 'actuator.direction', 'actuator.offset', 'actuator.stops_learned', 'valve.position_number'],
                                  '设置执行器：模式（两位带/不带限位、多位）、端口/位置数（偶数2-40）、转动方向（逆时针/顺时针/最短）、首位偏移（1至96-位置数；'
                                  '多位模式下串口位置编号从偏移起算）；遥控器设置菜单与AM/NP/SM/SO命令'),
        'actuator.learn_stops': cap({}, ['actuator.stops_learned'], {}, ['actuator.stops_learned'] + move,
                                    '两位带限位模式下学习阀的机械限位（LRN）：完成后阀位为A；须先装好阀'),
        'interface.configure': cap({'key': {'type': 'string', 'enum': ['baud_rate', 'device_id', 'legacy_mode']}, 'value': value},
                                   ['interface.baud_rate', 'interface.device_id', 'interface.legacy_mode'], {'key': 'baud_rate', 'value': 19200},
                                   ['interface.baud_rate', 'interface.device_id', 'interface.legacy_mode'],
                                   '设置串口：波特率4800-115200、设备ID（0-9、A-Z或无）、兼容旧微电动执行器的Legacy响应；遥控器Interface Setup与SB/ID/LG命令')})
    meta['limitations'] = list(meta['limitations']) + [
        '手动遥控器（panel.*）：按操作手册（panel_ui.json页码证据）实现4行字符显示、UP/DOWN/HOME-SETUP键：普通模式步进/归位，按住HOME 5 s进入设置菜单（Valve Setup、Interface Setup、Information），'
        'UP在首行即返回；两箭头同时按住进入工厂模式（只显示，UP退出）。',
        '执行器设置已建模但阀拓扑仍是所装的6位选择阀：位置数或模式与阀不符时只能到1-6位；转动方向决定移动路径（valve.move_path）与移动时间（宿主给每步时间时），'
        '过渡中经过端口的瞬时流路不建模；偏移SO按手册p.16/p.22作用于串口/BCD位置编号：多位模式下valve.select_position只接受SO至SO+NP-1（SO为阀的1号位），'
        'valve.position_number报告此编号；遥控器按键与显示按阀本身的位置1..NP（手册未写遥控器显示是否加偏移，见panel_ui.json假设）；USB与BCD接口为选件，未装。']
    meta['asset_version'] = '1.2.0'  # 1.2.0: manual remote 3D parts (screen + key joints, 2026-10-20 panel geometry)
    if panel:
        meta['panel_capabilities'] = _vr_copy_caps()
    return meta


def _vr_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _VR_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if '_chemsim_panel_ui' not in sys.modules:
                spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', VICI_EUTA_ASSET_DIR.parents[3] / 'scripts/asset_runtime/_panel_ui.py')
                sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
            engine = sys.modules['_chemsim_panel_ui']
        caps = set(export_vici_euta_contract_metadata()['capabilities'])
        spec = engine.load_spec(VICI_EUTA_ASSET_DIR / 'panel_ui.json', capabilities=caps)
        _VR_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _VR_PANEL


def _vr_copy_caps():
    out = {}
    for cid, d in _vr_panel()['caps'].items():
        out[cid] = {'input_schema': d['input_schema'], 'output_schema': d['output_schema'], 'example_arguments': d['example_arguments'], 'state_effects': [],
                    'component_ids': d['component_ids'], 'visibility': d['visibility'], 'description_zh': d['function_zh'], 'availability': 'implemented', 'kind': d['kind']}
    return out


_veuta_init_v1 = ViciEutaValveRuntime.__init__
_veuta_invoke_v1 = ViciEutaValveRuntime._invoke
_veuta_describe_v1 = ViciEutaValveRuntime.describe


def _vr_init(self, instance_id, *, scene_adapter=None, initial_world_time_s=0.0):
    _veuta_init_v1(self, instance_id, scene_adapter=scene_adapter, initial_world_time_s=initial_world_time_s)
    self._state.update(_veuta_copy.deepcopy(VICI_EUTA_SETTINGS))
    self._state.update({'valve.move_path': None, 'valve.move_direction': None, 'valve.move_time_s': None, 'valve.position_number': None})
    self._serial_requests = {}                                                # request_id -> (commanded number, valve position it meant)
    self._seconds_per_position = None                                         # host scenario value (unknown for this EUTA)
    self._panel, self._panel_n, self.panel_capture_dir = None, 0, None


def _vr_motion(self, before, target):
    """Path of a move on the actuator's position ring (p.26-27 Move Direction, p.12 SM): counterclockwise is the positive direction (1 to 2),
    clockwise the negative (2 to 1), shortest takes the fewer steps (a tie goes positive: assumption).  Two-position modes: one step between the
    stops.  Unknown start position: no path.  Move time = steps x the host's time per position, when given."""
    s = self._state
    if before is None:
        path, direction = None, None
    elif s['actuator.mode'] != 'multiposition':
        path, direction = [target], None
    else:
        n = s['actuator.positions']
        forward, backward = (target - before) % n, (before - target) % n
        positive = s['actuator.direction'] == 'counterclockwise' or (s['actuator.direction'] == 'shortest' and forward <= backward)
        steps = forward if positive else backward
        path = [((before - 1 + k * (1 if positive else -1)) % n) + 1 for k in range(1, steps + 1)]
        direction = 'positive' if positive else 'negative'
    spp = self._seconds_per_position
    return {'valve.move_path': path, 'valve.move_direction': direction, 'valve.move_time_s': None if path is None or spp is None else len(path) * spp}


def _vr_first(self):
    """Serial / BCD number of the valve's position 1: the offset SO in multiposition mode (p.16 SO, p.22: 'that value is the first (or lowest)
    position an actuator will recognize'); the two-position modes number A / B as 1 / 2."""
    return self._state['actuator.offset'] if self._state['actuator.mode'] == 'multiposition' else 1


def _vr_invoke_numbered(self, **kwargs):
    with self._lock:
        response = _vr_invoke(self, **kwargs)
        s = self._state                                                       # the position as the serial commands number it (CP)
        s['valve.position_number'] = s['valve.position'] + _vr_first(self) - 1 if s['valve.in_position'] and s['valve.position'] is not None else None
        return response


def _vr_invoke(self, *, capability_id, arguments, request_id, host, instance_id=None):
    if not host and capability_id == 'valve.select_position':
        with self._lock:                                                      # the actuator goes only to its set positions (A / B in two position)
            valid = (self._instance_ok(instance_id) and isinstance(request_id, str) and request_id.strip()
                     and self._validate(self._metadata['capabilities'][capability_id]['input_schema'], arguments))
            if valid and ('agent', request_id) in self._requests:            # a replay keeps the valve position the number meant then
                prior = self._serial_requests.get(request_id)
                if prior is not None and prior[0] != arguments['position']:
                    return self._error('REQUEST_CONFLICT', request_id)
                return _veuta_invoke_v1(self, capability_id=capability_id, arguments={'position': prior[1]} if prior else arguments, request_id=request_id,
                                        host=False, instance_id=instance_id)
            if not valid:
                return _veuta_invoke_v1(self, capability_id=capability_id, arguments=arguments, request_id=request_id, host=False, instance_id=instance_id)
            position = arguments['position'] - _vr_first(self) + 1             # SO answers as the valve's position 1
            limit = self._state['actuator.positions'] if self._state['actuator.mode'] == 'multiposition' else 2
            if position > len(VICI_EUTA_PORT_IDS) - 1:
                return self._error('INVALID_ARGUMENT', request_id)            # beyond the installed 6-position valve (as the 1.0 schema)
            if not 1 <= position <= limit:
                return self._error('PRECONDITION_FAILED', request_id)         # outside SO .. SO+NP-1: the actuator does not answer
            before = self._state['valve.position'] if self._state['valve.in_position'] else None
            response = _veuta_invoke_v1(self, capability_id=capability_id, arguments={'position': position}, request_id=request_id, host=False,
                                        instance_id=instance_id)
            if response['status'] == 'succeeded':
                self._serial_requests[request_id] = (arguments['position'], position)
                self._state.update(_vr_motion(self, before, position))       # the same command: no extra revision
            return response
    if host and capability_id == 'host.configure_motion':
        with self._lock:
            cap = self._metadata['host_capabilities'][capability_id]
            if (not isinstance(request_id, str) or not request_id.strip() or not self._validate(cap['input_schema'], arguments)
                    or arguments['seconds_per_position'] is not None and not arguments['seconds_per_position'] > 0):   # exclusiveMinimum 0
                return self._error('INVALID_ARGUMENT', request_id)
            digest = _veuta_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            key = ('host', request_id)
            if key in self._requests:
                prior_digest, prior_result = self._requests[key]
                return _veuta_copy.deepcopy(prior_result) if prior_digest == digest else self._error('REQUEST_CONFLICT', request_id)
            v = arguments['seconds_per_position']
            self._seconds_per_position = None if v is None else float(v)
            self._revision += 1
            response = self._envelope(request_id=request_id, result={'seconds_per_position': self._seconds_per_position})
            self._requests[key] = (digest, _veuta_copy.deepcopy(response))
            return response
    if host or capability_id not in ('actuator.configure', 'actuator.learn_stops', 'interface.configure'):
        if not host and isinstance(capability_id, str) and capability_id.startswith('panel.'):
            return _vr_invoke_panel(self, capability_id, arguments, request_id, instance_id)
        return _veuta_invoke_v1(self, capability_id=capability_id, arguments=arguments, request_id=request_id, host=host, instance_id=instance_id)
    with self._lock:
        if not self._instance_ok(instance_id):
            return self._error('UNKNOWN_INSTANCE', request_id)
        cap = self._metadata['capabilities'][capability_id]
        if not isinstance(request_id, str) or not request_id.strip() or not self._validate(cap['input_schema'], arguments):
            return self._error('INVALID_ARGUMENT', request_id)
        digest = _veuta_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
        key = ('agent', request_id)
        if key in self._requests:
            prior_digest, prior_result = self._requests[key]
            return _veuta_copy.deepcopy(prior_result) if prior_digest == digest else self._error('REQUEST_CONFLICT', request_id)
        s = _veuta_copy.deepcopy(self._state)
        if s['valve.target_position'] is not None and not s['valve.in_position']:
            return self._error('BUSY', request_id)                            # setup only between moves
        if capability_id == 'actuator.configure':
            k, v = arguments['key'], arguments['value']
            if k == 'mode' and v in VICI_EUTA_MODES:
                s['actuator.mode'] = v
                if v != 'two_position_stops':
                    s['actuator.stops_learned'] = False
            elif k == 'positions' and type(v) is int and 2 <= v <= 40 and v % 2 == 0 and s['actuator.offset'] <= 96 - v:
                s['actuator.positions'] = v                                   # even 2-40 (p.10, p.12); the offset must still fit
            elif k == 'direction' and v in VICI_EUTA_DIRECTIONS:
                s['actuator.direction'] = v
            elif k == 'offset' and type(v) is int and 1 <= v <= 96 - s['actuator.positions']:
                s['actuator.offset'] = v                                      # 1 to 96 minus the positions (p.12)
            else:
                return self._error('INVALID_ARGUMENT', request_id)
        elif capability_id == 'interface.configure':
            k, v = arguments['key'], arguments['value']
            if k == 'baud_rate' and type(v) is int and v in VICI_EUTA_BAUDS:
                s['interface.baud_rate'] = v
            elif k == 'device_id' and (v is None or v in VICI_EUTA_IDS):
                s['interface.device_id'] = v
            elif k == 'legacy_mode' and type(v) is bool:
                s['interface.legacy_mode'] = v
            else:
                return self._error('INVALID_ARGUMENT', request_id)
        else:
            if s['actuator.mode'] != 'two_position_stops':
                return self._error('PRECONDITION_FAILED', request_id)
            s['actuator.stops_learned'] = True
            if not (s['valve.in_position'] and s['valve.position'] == 1):     # learning ends at position A (p.9)
                s.update({'valve.target_position': 1, 'valve.position': None, 'valve.in_position': False, 'route.connected_port_id': None, 'route.connections': []})
        self._state = s
        self._revision += 1
        outputs = cap['output_schema']['properties']
        response = self._envelope(request_id=request_id, result={k: _veuta_copy.deepcopy(s[k]) for k in outputs})
        self._requests[key] = (digest, _veuta_copy.deepcopy(response))
        return response


def _vr_describe(self, *, asset_type_id=None, instance_id=None):
    out = _veuta_describe_v1(self, asset_type_id=asset_type_id, instance_id=instance_id)
    if isinstance(out, dict) and 'capabilities' in out:
        out['capabilities'].update({k: v for k, v in _vr_copy_caps().items() if v['visibility'] == 'agent'})
    return out


def _vr_invoke_panel(self, capability_id, arguments, request_id, instance_id):
    """panel.* commands: a key on the manual remote (engine + panel_ui.json) or a capture of its display; panel.frame is the evaluator's
    (the trusted host reads it with runtime.panel().frame())."""
    with self._lock:
        if not self._instance_ok(instance_id):
            return self._error('UNKNOWN_INSTANCE', request_id)
        panel = _vr_panel()
        definition = panel['caps'].get(capability_id)
        if definition is None:
            return self._error('UNKNOWN_CAPABILITY', request_id)
        if definition['visibility'] != 'agent':
            return self._error('ACCESS_DENIED', request_id)
        if not isinstance(request_id, str) or not request_id.strip() or panel['engine'].check_arguments(definition['input_schema'], arguments):
            return self._error('INVALID_ARGUMENT', request_id)
        digest = _veuta_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
        key = ('agent', request_id)
        if key in self._requests:
            prior_digest, prior_result = self._requests[key]
            return _veuta_copy.deepcopy(prior_result) if prior_digest == digest else self._error('REQUEST_CONFLICT', request_id)
        ui = self.panel()
        try:
            if capability_id == 'panel.capture':
                data, width, height = ui.png_bytes(arguments['display_id'])
                sha = _vr_hashlib.sha256(data).hexdigest()
                folder = _vr_Path(self.panel_capture_dir) if self.panel_capture_dir else _vr_Path(_vr_tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ('%s_%s.png' % (arguments['display_id'], sha[:16]))
                if not path.exists():
                    path.write_bytes(data)
                result = {'display_id': arguments['display_id'], 'time_s': self._time_s,
                          'image': {'path': str(path), 'format': 'png', 'width': width, 'height': height, 'sha256': sha}}
            else:
                result = {'accepted': bool(ui.press(arguments['input_id'], float(arguments.get('duration_s', 0.1)), held=arguments.get('held', ())))}
        except panel['engine'].PanelError:
            return self._error('INVALID_ARGUMENT', request_id)
        response = self._envelope(request_id=request_id, result=result)
        self._requests[key] = (digest, _veuta_copy.deepcopy(response))
        return response


def _vr_runtime_panel(self):
    """The manual remote (panel engine) of this actuator; created on first use (trusted host / tests: frame, render)."""
    if self._panel is None:
        self._panel = _vr_panel()['engine'].PanelUI(_vr_panel()['spec'], _ViciRemoteHost(self))
    return self._panel


class _ViciRemoteHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py: what the remote's firmware reads, calls and times by."""
    def __init__(self, runtime):
        self.runtime = runtime

    def panel_read(self):
        r = self.runtime
        out = _veuta_copy.deepcopy(r._state)
        out['host.powered'] = True                                            # the remote is powered with the actuator (no switch)
        return out

    def panel_invoke(self, capability_id, arguments):
        r = self.runtime
        r._panel_n += 1
        arguments = dict(arguments)
        if capability_id == 'valve.select_position':                          # the remote steps and homes the valve's own positions (p.6-7)
            arguments['position'] += _vr_first(r) - 1
        response = r._invoke(capability_id=capability_id, arguments=arguments, request_id='__remote_%d' % r._panel_n, host=False)
        if response['status'] != 'succeeded':
            raise _vr_panel()['engine'].PanelRejected(response['error']['code'])
        return response['result']

    def panel_time(self):
        return self.runtime._time_s


def _vr_host_configure_motion(self, *, seconds_per_position, request_id):
    """Trusted host: time per position step of the actuator (scenario value; None = unknown); later moves report valve.move_time_s."""
    return self._invoke(capability_id='host.configure_motion', arguments={'seconds_per_position': seconds_per_position}, request_id=request_id, host=True)


ViciEutaValveRuntime.host_configure_motion = _vr_host_configure_motion
ViciEutaValveRuntime.__init__ = _vr_init
ViciEutaValveRuntime._invoke = _vr_invoke_numbered
ViciEutaValveRuntime.describe = _vr_describe
ViciEutaValveRuntime.panel = _vr_runtime_panel
# END CHEM-182 VICI EUTA MANUAL REMOTE
