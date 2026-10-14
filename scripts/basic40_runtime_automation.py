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
import threading as _amc_threading

ALICAT_MC_TYPE = "instrument.alicat_mc_1slpm_rev6"
ALICAT_MC_FS_M3_S = 1e-3 / 60
ALICAT_MC_MIN_M3_S = ALICAT_MC_FS_M3_S * 0.0001
ALICAT_MC_PSI_PA = 6894.757293168
ALICAT_MC_R = 8.31446261815324


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
    "gas.reference_temperature_K": _amc_slot(_amc_num("K", 0), "K", "setpoint"),
    "gas.reference_pressure_Pa": _amc_slot(_amc_num("Pa", 0), "Pa", "setpoint"),
    "flow.setpoint_m3_s": _amc_slot(_amc_num("m^3/s", 0, ALICAT_MC_FS_M3_S), "m^3/s", "setpoint", capability="flow.set_setpoint", parameter="flow_rate_m3_s"),
    "flow.zeroed": _amc_slot({"type": "boolean"}, "1", "derived"),
    "ports.upstream_node_id": _amc_slot(_AMC_NULL_STRING, "1", "scene"),
    "ports.downstream_node_id": _amc_slot(_AMC_NULL_STRING, "1", "scene"),
    "simulation.dynamic_model_configured": _amc_slot({"type": "boolean"}, "1", "derived"),
    "measurement.standard_flow_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "derived"),
    "measurement.actual_flow_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "derived"),
    "measurement.mass_flow_kg_s": _amc_slot(_amc_num("kg/s", 0, nullable=True), "kg/s", "derived"),
    "measurement.pressure_Pa": _amc_slot(_amc_num("Pa", 0, nullable=True), "Pa", "derived"),
    "measurement.temperature_K": _amc_slot(_amc_num("K", 0, nullable=True), "K", "derived"),
    "measurement.total_standard_volume_m3": _amc_slot(_amc_num("m^3", 0, nullable=True), "m^3", "derived"),
    "measurement.standard_accuracy_bound_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "derived"),
    "measurement.accuracy_conditions_met": _amc_slot({"type": "boolean"}, "1", "derived"),
    "truth.standard_flow_m3_s": _amc_slot(_amc_num("m^3/s", 0, nullable=True), "m^3/s", "process_truth", "debug"),
    "truth.total_standard_volume_m3": _amc_slot(_amc_num("m^3", 0, nullable=True), "m^3", "process_truth", "debug"),
    "truth.upstream_pressure_Pa": _amc_slot(_amc_num("Pa", 0, nullable=True), "Pa", "process_truth", "debug"),
    "truth.downstream_pressure_Pa": _amc_slot(_amc_num("Pa", 0, nullable=True), "Pa", "process_truth", "debug"),
    "truth.temperature_K": _amc_slot(_amc_num("K", 0, nullable=True), "K", "process_truth", "debug"),
    "truth.equilibrium": _amc_slot({"type": "boolean"}, "1", "process_truth", "debug"),
    "truth.no_flow": _amc_slot({"type": "boolean"}, "1", "process_truth", "debug"),
    "truth.response_time_constant_s": _amc_slot(_amc_num("s", 0, nullable=True), "s", "process_truth", "debug"),
}
_AMC_MEASUREMENTS = [key for key in _AMC_SLOTS if key.startswith("measurement.")]
_AMC_LIMITATIONS = [
    "仅为合成过程与接口实现，无硬件驱动、机器人或真实阀动作。",
    "气体兼容性由可信宿主登记；厂家98种气体和20个混气定义的数据库未实现。未知摩尔质量及性质保持null。",
    "动态没有缺省时间常数。宿主必须给出明确假设及证据；最快T63 30 ms不是稳定时间，也不自动作为tau。",
    "简化一阶模型仅在有效供压/背压和温度条件下使用。阀特性、压降曲线、PID、真实稳定判据和非理想气体性质未实现。",
    "模型读数均为estimated；压力使用上游边界代理，温度使用宿主气温代理。不是实测传感器或厂家精度验证。",
    "精度界限仅在归零且宿主声明平衡时计算 max(0.006|Q|,0.001FS)；不生成误差分布，不从固定时长推断平衡。",
    "标准流量和累计标准体积使用当前参考条件；改气体/参考条件使归零和读数失效并开启新累计量区间。",
    "参考条件配置采用仿真定义：1 SLPM数值量程按当前参考条件解释；不声称复现厂商改变标准温压后内部标度处理。",
    "气路节点引用由宿主保管。本适配器不做气源库存、守恒物质转移或压力网络求解，后续气路宿主需接入这些过程。",
    "重放去重仅当前宿主内存有效，不声称跨重启恰好一次。API过滤不是Python或文件系统隔离。",
]


def export_alicat_mc_contract_metadata():
    """Type-only contract inputs; excludes all instance gas/boundary values."""
    slots = _amc_copy.deepcopy(_AMC_SLOTS)
    for sid, slot in slots.items():
        slot["read_binding"] = "AlicatMCSession.read(instance_id=..., state_ids=['%s'])" % sid
        if "standard" in sid or sid == "flow.setpoint_m3_s":
            slot["reference_conditions"] = "Standard volume at gas.reference_temperature_K and gas.reference_pressure_Pa; defaults 298.15 K and 101325 Pa"
        elif "pressure" in sid:
            slot["reference_conditions"] = "Absolute pressure"
        elif sid == "measurement.actual_flow_m3_s":
            slot["reference_conditions"] = "Ideal-gas conversion at host upstream absolute pressure and gas temperature; no real gas correction"
    arguments = {
        "power.set": {"enabled": {"type": "boolean"}},
        "gas.select": {"gas_id": {"type": "string", "description": "An explicitly host-registered compatible gas ID; no bundled gas library"}},
        "gas.set_reference_conditions": {"temperature_K": {"type": "number", "exclusiveMinimum": 0, "x-unit": "K"}, "pressure_Pa": {"type": "number", "exclusiveMinimum": 0, "x-unit": "Pa"}},
        "flow.set_setpoint": {"flow_rate_m3_s": {"type": "number", "minimum": 0, "maximum": ALICAT_MC_FS_M3_S, "x-unit": "m^3/s", "x-reference-conditions": "gas.reference_temperature_K, gas.reference_pressure_Pa", "description": "0 stops the target; positive values must be >= 0.0001 full scale"}},
        "flow.zero": {}, "flow.reset_total": {}, "flow.read": {}, "control.set_pid": {},
    }
    direct = {"power.set": ["power.enabled"], "gas.select": ["gas.selected_id"],
              "gas.set_reference_conditions": ["gas.reference_temperature_K", "gas.reference_pressure_Pa"],
              "flow.set_setpoint": ["flow.setpoint_m3_s", "truth.equilibrium"], "flow.zero": ["flow.zeroed", "truth.standard_flow_m3_s"],
              "flow.reset_total": ["truth.total_standard_volume_m3"], "flow.read": [], "control.set_pid": []}
    summaries = {"power.set": "设置仿真供电；断电令测量不可用且归零失效，未假设失电阀位。",
        "gas.select": "零目标下选用宿主登记气体；尚未接气路或宿主确认无流；清除归零和累计量区间。",
        "gas.set_reference_conditions": "零目标下设置标准体积的参考温压；尚未接气路或宿主确认无流；清除归零和累计量区间。",
        "flow.set_setpoint": "写入0或0.01–100%FS目标；成功不代表达到目标或平衡。",
        "flow.zero": "只在宿主提供无流、平衡与有效边界条件时记录一次合成归零；不推进时间。",
        "flow.reset_total": "清零仿真累计标准体积的显示区间，不转移或删除气体。",
        "flow.read": "读取当前估计观测，不推进时间。",
        "control.set_pid": "PID及真实阀曲线没有实现；没有编造增益单位或参数结构。"}
    examples = {"power.set": {"enabled": True}, "gas.select": {"gas_id": "host_registered_compatible_gas"},
        "gas.set_reference_conditions": {"temperature_K": 298.15, "pressure_Pa": 101325.0},
        "flow.set_setpoint": {"flow_rate_m3_s": ALICAT_MC_FS_M3_S / 2},
        "flow.zero": {}, "flow.reset_total": {}, "flow.read": {}, "control.set_pid": {}}
    preconditions = {"power.set": [], "gas.select": ["已上电；目标为0；尚未接气路或宿主确认无流；gas_id由宿主登记兼容证据"],
        "gas.set_reference_conditions": ["已上电；目标为0；尚未接气路或宿主确认无流；参考温压有限且大于0"],
        "flow.set_setpoint": ["已上电且已选宿主登记气体；正目标须>=0.0001FS；写入目标不要求过程已就绪"],
        "flow.zero": ["已上电且已选气体；目标为0；进出气节点已接入；有效温压；宿主no_flow及equilibrium均为true"],
        "flow.reset_total": ["已上电"], "flow.read": [], "control.set_pid": []}
    caps = {}
    for cid, props in arguments.items():
        effects = list(direct[cid])
        if cid == "power.set":
            effects += ["flow.zeroed", "truth.standard_flow_m3_s", "truth.total_standard_volume_m3"]
        if cid in ("gas.select", "gas.set_reference_conditions"):
            effects += ["flow.zeroed", "truth.standard_flow_m3_s", "truth.total_standard_volume_m3"]
        if cid != "flow.read" and cid != "control.set_pid":
            effects += _AMC_MEASUREMENTS
        effect_entries = [{"state_id": sid, "timing": "immediate", "description_zh": "同步变更设定或刷新明确标注的观测代理"} for sid in dict.fromkeys(effects)]
        if cid == "flow.set_setpoint":
            immediate = ["flow.setpoint_m3_s", "truth.equilibrium", "measurement.accuracy_conditions_met", "measurement.standard_accuracy_bound_m3_s"]
            future = ["truth.standard_flow_m3_s", "truth.total_standard_volume_m3", "measurement.standard_flow_m3_s", "measurement.actual_flow_m3_s", "measurement.mass_flow_kg_s", "measurement.total_standard_volume_m3"]
            effect_entries = [{"state_id": sid, "timing": "immediate", "description_zh": "写入目标且撤销上次平衡状态的精度条件"} for sid in immediate]
            effect_entries += [{"state_id": sid, "timing": "on_step", "description_zh": "仅在宿主显式配置动态及有效气路边界后，随共同时间推进简化流量响应和积分"} for sid in future]
        result_ids = _AMC_MEASUREMENTS if cid == "flow.read" else direct[cid]
        result_ids = [sid for sid in result_ids if slots[sid]["visibility"] == "agent"]
        if cid == "flow.reset_total":
            result_ids = ["measurement.total_standard_volume_m3"]
        caps[cid] = {"availability": "unsupported" if cid == "control.set_pid" else "implemented", "visibility": "agent",
            "kind": "query" if cid == "flow.read" else "configuration" if cid.startswith("gas.") else "device_command",
            "description_zh": summaries[cid], "input_schema": _amc_obj(props),
            "output_schema": _amc_obj({sid: _amc_copy.deepcopy(slots[sid]["value_schema"]) for sid in result_ids}),
            "state_effects": effect_entries,
            "observes": result_ids, "preconditions_zh": preconditions[cid], "example_arguments": [examples[cid]],
            "binding": None if cid == "control.set_pid" else "basic40_runtime_automation.AlicatMCSession.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % cid,
            "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "未实现屏幕跨层同步；内阀行程与机构未知，未用归一化参数假冒机械关节"}}
    return {"asset_types": {ALICAT_MC_TYPE: {"catalog_id": "CHEM-180", "model": "Alicat Scientific MC, 1 SLPM, standard accuracy, Rev6 (2025-07)",
        "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps,
        "parameters": {
            "full_scale_standard_flow_m3_s": {"value": ALICAT_MC_FS_M3_S, "unit": "m^3/s", "evidence": "catalog"},
            "minimum_positive_control_m3_s": {"value": ALICAT_MC_MIN_M3_S, "unit": "m^3/s", "evidence": "catalog"},
            "fastest_control_t63_s": {"value": 0.03, "unit": "s", "evidence": "catalog", "note_zh": "配置与调整相关的最快T63，非稳定时间且不设为默认tau"},
            "default_reference_temperature_K": {"value": 298.15, "unit": "K", "evidence": "catalog"},
            "default_reference_pressure_Pa": {"value": 101325.0, "unit": "Pa", "evidence": "catalog"},
            "default_dynamic_time_constant_s": {"value": None, "unit": "s", "evidence": "unknown"},
            "gas_molar_mass_kg_mol": {"value": None, "unit": "kg/mol", "evidence": "unknown"},
            "valve_travel_m": {"value": None, "unit": "m", "evidence": "unknown"},
            "pid_gains": {"value": None, "unit": "1", "evidence": "unknown", "note_zh": "实际参数结构及单位未知；1仅为此未知占位项的合同单位"}},
        "limitations": list(_AMC_LIMITATIONS)}},
        "host_capabilities": {
            "host.register_gas": {"binding": "AlicatMCRuntime.host_register_gas", "scope": "Trusted gas IDs and compatibility evidence; optional host molar mass evidence"},
            "host.connect_ports": {"binding": "AlicatMCRuntime.host_connect_ports", "scope": "instance upstream inlet node -> MFC -> downstream outlet node; does not solve the network"},
            "host.set_boundary": {"binding": "AlicatMCRuntime.host_set_boundary", "scope": "Trusted absolute pressures, temperature, no-flow and equilibrium assertions"},
            "host.configure_dynamics": {"binding": "AlicatMCRuntime.host_configure_dynamics", "scope": "Explicit positive first-order tau and assumption evidence; no default tau"},
            "sim.step": {"binding": "AlicatMCRuntime.host_step(dt_s)", "scope": "All registered MFC instances use one host clock; scheduling bound 0..3600s, not a vendor limit"}},
        "result_schema_scope": "output_schema validates response.result; public observations never include truth.*"}


class AlicatMCRuntime:
    """Trusted host; state and deduplication are isolated per runtime/instance."""
    def __init__(self):
        self.time_s = 0.0
        self._instances = {}
        self._gases = {}
        self._principals = {}
        self._requests = {}
        self._logs = {}
        self._lock = _amc_threading.RLock()

    def create_instance(self, instance_id, asset_type_id=ALICAT_MC_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != ALICAT_MC_TYPE:
                raise ValueError("Unique nonempty instance ID and CHEM-180 asset type required")
            values = {sid: None for sid in _AMC_SLOTS}
            values.update({"power.enabled": False, "gas.reference_temperature_K": 298.15, "gas.reference_pressure_Pa": 101325.0,
                "flow.setpoint_m3_s": 0.0, "flow.zeroed": False, "simulation.dynamic_model_configured": False,
                "measurement.accuracy_conditions_met": False, "truth.equilibrium": False, "truth.no_flow": False})
            self._instances[instance_id] = {"instance_id": instance_id, "values": values, "revision": 0, "dynamics_evidence": None}

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

    def _require(self, instance_id):
        if not isinstance(instance_id, str) or instance_id not in self._instances:
            raise ValueError("Unknown instance")
        return self._instances[instance_id]

    def _commit(self, inst, values):
        self._refresh(values)
        if inst["values"] != values:
            inst["values"] = values
            inst["revision"] += 1

    @staticmethod
    def _boundaries_valid(s):
        lower, upper = 11.5 * ALICAT_MC_PSI_PA, 160 * ALICAT_MC_PSI_PA
        return (s["ports.upstream_node_id"] is not None and s["ports.downstream_node_id"] is not None
            and all(_amc_number(s[key]) for key in ("truth.upstream_pressure_Pa", "truth.downstream_pressure_Pa", "truth.temperature_K"))
            and lower <= s["truth.upstream_pressure_Pa"] <= upper and lower <= s["truth.downstream_pressure_Pa"] <= upper
            and 263.15 <= s["truth.temperature_K"] <= 333.15)

    @staticmethod
    def _stopped_for_configuration(s):
        unconnected = s["ports.upstream_node_id"] is None and s["ports.downstream_node_id"] is None
        return s["flow.setpoint_m3_s"] == 0 and ((unconnected and s["truth.standard_flow_m3_s"] is None)
            or (s["truth.no_flow"] and s["truth.standard_flow_m3_s"] in (None, 0)))

    def _refresh(self, s):
        for sid in _AMC_MEASUREMENTS:
            s[sid] = False if sid == "measurement.accuracy_conditions_met" else None
        if not s["power.enabled"] or s["gas.selected_id"] is None or not self._boundaries_valid(s):
            return
        s["measurement.pressure_Pa"] = s["truth.upstream_pressure_Pa"]
        s["measurement.temperature_K"] = s["truth.temperature_K"]
        q = s["truth.standard_flow_m3_s"]
        if not s["flow.zeroed"] or q is None:
            return
        s["measurement.standard_flow_m3_s"] = q
        s["measurement.actual_flow_m3_s"] = _amc_finite_result(q * (s["truth.temperature_K"] / s["gas.reference_temperature_K"]) * (s["gas.reference_pressure_Pa"] / s["truth.upstream_pressure_Pa"]))
        molar_mass = self._gases[s["gas.selected_id"]]["molar_mass_kg_mol"]
        if molar_mass is not None:
            s["measurement.mass_flow_kg_s"] = _amc_finite_result(q * s["gas.reference_pressure_Pa"] * molar_mass / (ALICAT_MC_R * s["gas.reference_temperature_K"]))
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
                "truth.upstream_pressure_Pa": None, "truth.downstream_pressure_Pa": None, "truth.temperature_K": None,
                "truth.no_flow": False, "truth.equilibrium": False})
            self._commit(inst, s)

    def host_set_boundary(self, instance_id, *, upstream_pressure_Pa, downstream_pressure_Pa, temperature_K, equilibrium, no_flow):
        with self._lock:
            inst = self._require(instance_id)
            if any(not _amc_number(v) or v <= 0 for v in (upstream_pressure_Pa, downstream_pressure_Pa, temperature_K)) or type(equilibrium) is not bool or type(no_flow) is not bool:
                raise ValueError("Positive finite SI boundaries and boolean host assertions required")
            s = _amc_copy.deepcopy(inst["values"])
            s.update({"truth.upstream_pressure_Pa": float(upstream_pressure_Pa), "truth.downstream_pressure_Pa": float(downstream_pressure_Pa),
                "truth.temperature_K": float(temperature_K), "truth.equilibrium": equilibrium, "truth.no_flow": no_flow})
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
                if ready and s["truth.no_flow"]:
                    s["truth.standard_flow_m3_s"] = 0.0
                elif ready and tau is not None and old is not None and s["truth.upstream_pressure_Pa"] > s["truth.downstream_pressure_Pa"]:
                    target = s["flow.setpoint_m3_s"]
                    response = -_amc_math.expm1(-dt_s / tau)
                    s["truth.standard_flow_m3_s"] = max(0.0, old + (target - old) * response)
                    integral = old * dt_s + (target - old) * (dt_s - tau * response)
                    integral = min(max(integral, min(old, target) * dt_s), max(old, target) * dt_s)
                    if s["truth.total_standard_volume_m3"] is not None:
                        s["truth.total_standard_volume_m3"] += integral
                else:
                    s["truth.standard_flow_m3_s"] = None
                    s["truth.total_standard_volume_m3"] = None
                self._refresh(s)
                pending.append((inst, s))
            self.time_s += dt_s
            for inst, s in pending:
                self._commit(inst, s)

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
        metadata = export_alicat_mc_contract_metadata()["asset_types"][ALICAT_MC_TYPE]["capabilities"]
        if cid not in metadata:
            return fail("UNKNOWN_CAPABILITY")
        if cid == "control.set_pid":
            return fail("UNSUPPORTED", "unsupported")
        props = metadata[cid]["input_schema"]["properties"]
        if set(args) != set(props):
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
        if cid == "power.set":
            s["power.enabled"] = args["enabled"]
            if not args["enabled"]:
                s.update({"flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None})
        elif cid == "gas.select":
            if args["gas_id"] not in self.__host._gases:
                return fail("PRECONDITION_FAILED")
            s["gas.selected_id"] = args["gas_id"]
            s.update({"flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None})
        elif cid == "gas.set_reference_conditions":
            s["gas.reference_temperature_K"], s["gas.reference_pressure_Pa"] = float(args["temperature_K"]), float(args["pressure_Pa"])
            s.update({"flow.zeroed": False, "truth.standard_flow_m3_s": None, "truth.total_standard_volume_m3": None})
        elif cid == "flow.set_setpoint":
            value = args["flow_rate_m3_s"]
            if 0 < value < ALICAT_MC_MIN_M3_S:
                return fail("INVALID_ARGUMENT")
            if s["gas.selected_id"] is None:
                return fail("PRECONDITION_FAILED")
            if s["flow.setpoint_m3_s"] != value:
                s["truth.equilibrium"] = False
            s["flow.setpoint_m3_s"] = float(value)
        elif cid == "flow.zero":
            if not (s["gas.selected_id"] is not None and s["flow.setpoint_m3_s"] == 0 and s["truth.no_flow"] and s["truth.equilibrium"] and self.__host._boundaries_valid(s)):
                return fail("PRECONDITION_FAILED")
            s["flow.zeroed"] = True
            s["truth.standard_flow_m3_s"] = 0.0
        elif cid == "flow.reset_total":
            s["truth.total_standard_volume_m3"] = 0.0
        self.__host._commit(inst, s)
        result = {key: inst["values"][key] for key in metadata[cid]["output_schema"]["properties"]}
        return self._envelope(sid, request_id, result=result)

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
# END CHEM-180 ALICAT MC APPEND-ONLY RUNTIME

# CHEM-181 P-702CV batch-scope runtime; appended as a self-contained namespace.
import copy as _p702cv_copy
import json as _p702cv_json
import math as _p702cv_math
import threading as _p702cv_threading

P702CV_ASSET_TYPE_ID = 'instrument.bronkhorst_p702cv_5bar_gauge_n2'
P702CV_SENSOR_FULL_SCALE_PA = 500000.0
P702CV_MEASUREMENT_MIN_PA = 10000.0
P702CV_CONTROL_MIN_PA = 100000.0
P702CV_GAS_CONSTANT = 8.31446261815324


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


def export_p702cv_contract_metadata():
    """Type metadata and executable input/output schemas; no live state values."""
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
        self._metadata = export_p702cv_contract_metadata()

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

    def _observe(self, state):
        pg = state['process.upstream_absolute_pressure_pa'] - self._config['pressure_reference_Pa']
        valid = state['power.enabled'] and P702CV_MEASUREMENT_MIN_PA <= pg <= P702CV_SENSOR_FULL_SCALE_PA
        state['pressure.reading_pa'] = pg if valid else None
        target = state['pressure.active_setpoint_pa']
        state['pressure.deviation_pa'] = pg - target if valid and target is not None else None
        state['control.status'] = 'power_off' if not state['power.enabled'] else 'manual' if state['control.mode'] == 'manual' else 'regulating' if valid else 'sensor_unavailable'
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
            target = s['pressure.setpoint_pa']
            active = s['pressure.active_setpoint_pa']
            if target is not None and active is not None:
                rate = s['control.ramp_rate_pa_s']
                s['pressure.active_setpoint_pa'] = target if rate is None else active + max(-rate * dt_s, min(rate * dt_s, target - active))
            if s['power.enabled'] and s['control.mode'] == 'automatic' and s['pressure.reading_pa'] is not None:
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
            self._observe(s)
            if not all(v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or _p702cv_number(v) for v in s.values()):
                raise ValueError('Host step outside numerical support')
            self._state = s
            self._time_s = float(world_time_s)
            self._revision += 1
            return {'world_time_s': self._time_s}


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
            meta['state_slots'] = {k: v for k, v in meta['state_slots'].items() if self._visible(v)}
            meta['capabilities'] = {k: v for k, v in meta['capabilities'].items() if self._visible(v)}
            for capability in meta['capabilities'].values():
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
                reason = ('power_off' if not r._state['power.enabled'] else 'outside_selected_measurement_range' if r._state['pressure.reading_pa'] is None else 'setpoint_unconfigured') if unavailable and sensor else 'not_configured' if unavailable else None
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
            result = dict(arguments)
            if capability_id.startswith('simulation.'):
                try:
                    result = r.step(**arguments) if capability_id == 'simulation.step' else r.set_boundary(**arguments)
                except (ValueError, OverflowError, ZeroDivisionError):
                    return self._error('INVALID_ARGUMENT', request_id)
            else:
                if capability_id not in ('power.set', 'scene.set_valve_position') and not s['power.enabled']:
                    return self._error('PRECONDITION_FAILED', request_id)
                if capability_id == 'power.set':
                    s['power.enabled'] = arguments['enabled']
                    if not arguments['enabled']:
                        s['valve.opening'] = 0.0
                elif capability_id == 'pressure.set_setpoint':
                    s['pressure.setpoint_pa'] = arguments['pressure_Pa']
                    if s['pressure.active_setpoint_pa'] is None:
                        s['pressure.active_setpoint_pa'] = arguments['pressure_Pa']
                elif capability_id == 'control.set_mode':
                    if arguments['mode'] == 'automatic' and (s['pressure.active_setpoint_pa'] is None or s['pressure.reading_pa'] is None):
                        return self._error('PRECONDITION_FAILED', request_id)
                    s['control.mode'] = arguments['mode']
                elif capability_id == 'control.set_gain':
                    s['control.gain_per_pa_s'] = arguments['gain_per_Pa_s']
                elif capability_id == 'control.set_ramp_rate':
                    s['control.ramp_rate_pa_s'] = arguments['rate_Pa_s']
                elif capability_id == 'valve.set_opening':
                    if s['control.mode'] != 'manual':
                        return self._error('PRECONDITION_FAILED', request_id)
                    s['valve.opening'] = arguments['opening']
                elif capability_id == 'scene.set_valve_position':
                    if r._scene_adapter is None:
                        return self._error('UNSUPPORTED', request_id)
                    s['scene.valve_position'] = arguments['position']
                    try:
                        r._scene_adapter(r.instance_id, {'scene.valve_position': arguments['position']})
                    except Exception:
                        failed = self._error('DEVICE_ERROR', request_id, 'unknown')
                        r._requests[key] = (digest, _p702cv_copy.deepcopy(failed))
                        return failed
                r._observe(s)
                if not all(v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or _p702cv_number(v) for v in s.values()):
                    return self._error('INVALID_ARGUMENT', request_id)
                r._state = s
                r._revision += 1
            response = self._envelope(result=result, request_id=request_id)
            r._requests[key] = (digest, _p702cv_copy.deepcopy(response))
            return response


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
    return {'asset_type_id': VICI_EUTA_ASSET_TYPE_ID, 'asset_version': '1.0.0', 'catalog_id': 'CHEM-182',
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
    return {'asset_type_id': ZIATH_DP5_TYPE, 'asset_version': '1.0.0', 'catalog_id': 'CHEM-179', 'state_slots': slots, 'capabilities': caps,
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

    def _response(self, instance_id, request_id=None, *, status='succeeded', result=None, operation_id=None, code=None, side_effects='none'):
        item = self._get(instance_id)
        return {'protocol_version': '1.0.0', 'instance_id': instance_id, 'request_id': request_id, 'operation_id': operation_id,
            'status': status, 'state_revision': item['revision'] if item else None, 'clock': {'domain': 'simulation', 'time_s': self.time_s},
            'result': _dp5_copy.deepcopy(result), 'observations': {},
            'error': None if code is None else {'code': code, 'message': 'Operation not permitted or could not be confirmed: ' + code,
                'retryable': False, 'side_effects': side_effects, 'known_progress': None}}

    @staticmethod
    def _operation_result(op):
        return {'operation_id': op['id'], 'status': op['status'], 'elapsed_s': op['elapsed_s'],
            'progress': op['elapsed_s'] / ZIATH_DP5_SCAN_S, 'results_available': op['status'] == 'succeeded'}

    def _invoke(self, principal_id, instance_id, cid, args, request_id):
        with self._lock:
            reject = lambda code, status='rejected', side='none': self._response(instance_id, request_id, status=status, code=code, side_effects=side)
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
            if cid in ('configuration.set', 'acquisition.start') and not s['power.enabled']:
                return reject('PRECONDITION_FAILED')
            if cid in ('configuration.set', 'acquisition.start', 'scene.set_rack_lift') and s['acquisition.status'] == 'running':
                return reject('BUSY')
            if cid == 'configuration.set' and s['rack.loaded'] and args['rack_format'] != item['rack_format']:
                return reject('PRECONDITION_FAILED')
            if cid == 'acquisition.start' and (not s['rack.loaded'] or s['scene.rack_lift_m'] != 0.0 or s['configuration.rack_format'] != item['rack_format']):
                return reject('PRECONDITION_FAILED')
            if cid == 'scene.set_rack_lift' and not s['rack.loaded']:
                return reject('PRECONDITION_FAILED')
            op = None
            if cid in ('acquisition.poll', 'acquisition.cancel'):
                op = self._operations.get(args['operation_id'])
                if op is None or op['instance_id'] != instance_id or op['principal_id'] != principal_id:
                    return reject('UNKNOWN_OPERATION')
            if cid == 'results.export' and s['results.data'] is None:
                return reject('PRECONDITION_FAILED')
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


def export_intellixcap_s1_contract_metadata():
    """Complete slot/capability fragments using artifact runtime and stable IDs."""
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
        caps[cid] = {"function_zh": function, "kind": "query" if query else "configuration" if cid in ("consumable.select", "cycle.set_mode") else "device_command",
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
    return {"asset_type_id": INTELLIXCAP_S1_TYPE, "asset_version": "1.0.0", "catalog_id": "CHEM-177", "state_slots": slots, "capabilities": caps,
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
            values.update({"power.enabled": False, "cycle.mode": "decap", "simulation.motion_configured": False, "cycle.status": "idle", "cycle.motion_completed": False})
            self._instances[instance_id] = {"instance_id": instance_id, "values": values, "revision": 0, "motion": None, "sample": None, "active_operation_id": None, "events": []}

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
                duration = m[{"clamping": "clamp_duration_s", "rotating": "rotation_duration_s", "releasing": "release_duration_s"}[phase]]
                fraction = min(1.0, max(0.0, (next_time - op["phase_start_s"]) / duration))
                if phase == "clamping":
                    s["jaw_gap_m"] = m["open_gap_m"] * (1-fraction) + m["closed_gap_m"] * fraction
                elif phase == "rotating":
                    s["rotation_rad"] = op["rotation_start_rad"] + op["signed_rotation_delta_rad"] * fraction
                else:
                    s["jaw_gap_m"] = m["closed_gap_m"] * (1-fraction) + m["open_gap_m"] * fraction
                if fraction == 1.0:
                    phase = {"clamping": "awaiting_attach", "rotating": "awaiting_thread_result", "releasing": "succeeded" if s["cycle.host_confirmed"] else "failed"}[phase]
                    op["phase"] = s["cycle.status"] = phase
                    if phase == "awaiting_thread_result":
                        s["cycle.motion_completed"] = True
                    if phase in ("succeeded", "failed"):
                        op["status"] = phase
                        inst["active_operation_id"] = None
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

    def _execute(self, inst, cid, args, request_id):
        iid = inst["instance_id"]
        fail = lambda code: self._error(iid, code, request_id)
        caps = export_intellixcap_s1_contract_metadata()["capabilities"]
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
        if inst["active_operation_id"] is not None or s["attachment.constraint_id"] is not None:
            return fail("BUSY")
        if cid == "power.set":
            s["power.enabled"] = args["enabled"]
        elif cid == "cycle.set_mode":
            s["cycle.mode"] = args["mode"]
        elif cid == "consumable.select":
            if args["consumable_id"] not in self.__host._consumables:
                return fail("PRECONDITION_FAILED")
            s["sample.consumable_id"] = args["consumable_id"]
        elif cid == "cycle.start":
            m, sample = inst["motion"], inst["sample"]
            if not s["power.enabled"] or m is None or sample is None or sample["consumable_id"] != s["sample.consumable_id"] or s["jaw_gap_m"] != m["open_gap_m"]:
                return fail("PRECONDITION_FAILED")
            cap_diameter = self.__host._consumables[sample["consumable_id"]]["cap_diameter_m"]
            if not m["closed_gap_m"] <= cap_diameter < m["open_gap_m"]:
                return fail("PRECONDITION_FAILED")
            delta = m["rotation_delta_rad"] * (1 if s["cycle.mode"] == "decap" else -1)
            if not _ixs_number(s["rotation_rad"] + delta):
                return fail("INVALID_ARGUMENT")
            operation_id = "ixs_op_" + _ixs_uuid.uuid4().hex
            s.update({"cycle.status": "clamping", "cycle.motion_completed": False, "cycle.host_confirmed": None, "truth.thread_state": None})
            self.__host._commit(inst, s)
            op = {"operation_id": operation_id, "instance_id": iid, "principal_id": self.__principal_id, "request_id": request_id, "status": "running", "phase": "clamping", "phase_start_s": self.__host.time_s, "rotation_start_rad": s["rotation_rad"], "signed_rotation_delta_rad": delta, "mode": s["cycle.mode"], "pending_event_id": None, "result": self.__host._progress(inst)}
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
    'thermal.setpoint_K': _ahs_slot(_ahs_nullable(_AHS_TEMP), 'K', 'setpoint', via=('thermal.set_temperature', 'temperature_K')),
    'measurement.temperature_K': _ahs_slot(_ahs_nullable(_ahs_num('K')), 'K', 'derived'),
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
    'truth.temperature_K': _ahs_slot(_ahs_nullable(_ahs_num('K')), 'K', 'process_truth', 'debug'),
    'truth.thermal_rate_K_s': _ahs_slot(_ahs_nullable(_ahs_num('K/s')), 'K/s', 'process_truth', 'debug'),
}
_AHS_OPERATION = _ahs_obj({'operation_id': _AHS_STR, 'status': _AHS_STATUS, 'elapsed_s': _ahs_num('s', 0, 10),
    'progress': _ahs_num('1', 0, 1), 'simulated_sealed': _AHS_BOOL})
_AHS_READ_IDS = ['measurement.temperature_K', 'thermal.target_reached', 'film.remaining_seals', 'plate.simulated_sealed', 'sealing.status', 'sealing.elapsed_s', 'sealing.progress']
_AHS_COMPONENTS = {'power.set': ['power_data_panel'], 'thermal.set_temperature': ['press_head'],
    'configuration.set': ['plate_adapter', 'roll_support'], 'scene.set_press_displacement': ['press_head', 'guide_rails'],
    'sealing.start': ['press_head', 'film_path', 'external_plate'], 'sealing.poll': ['status_display'],
    'sealing.cancel': ['press_head'], 'sealing.read': ['status_display'], 'sealing.set_pressure': ['press_head']}
_AHS_LIMITATIONS = [
    '温度100–195°C、时间0.1–10s、板高3–60mm、膜宽75–85mm来自CHEM-176三表；SI接口采用K、s、m。',
    '热初始值和热速率没有缺省值；温度推进只采用宿主明确给定且附证据的线性接近设定值模型，无热容、功率、冷却曲线或PID推断。',
    'measurement.temperature_K为理想化过程代理且quality=estimated；target_reached只比较模型目标，不是实测稳定性或厂家互锁。±1K均匀性只作为规格，不作为目标公差。',
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
        'power.set': ({'enabled': _AHS_BOOL}, _ahs_obj({'enabled': _AHS_BOOL}), {'enabled': True}, ['power.enabled', 'thermal.target_reached', 'measurement.temperature_K', 'sealing.status'], 'device_command', '设置仿真供电；断电取消封膜并保留压头、时间和已消耗膜。'),
        'thermal.set_temperature': ({'temperature_K': _AHS_TEMP}, _ahs_obj({'setpoint_K': _AHS_TEMP}), {'temperature_K': 423.15}, ['thermal.setpoint_K', 'thermal.target_reached'], 'device_command', '写入封膜温度目标；只在宿主推进时才更新已配置的温度过程。'),
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
            entries += [{'state_id': k, 'timing': 'on_step', 'description_zh': '仅宿主已给出初值、线性热速率及证据时，随共同时间更新。'} for k in ['truth.temperature_K', 'measurement.temperature_K', 'thermal.target_reached']]
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
    return {'asset_type_id': AZENTA_HEAT_SEALER_TYPE, 'asset_version': '1.0.0', 'catalog_id': 'CHEM-176', 'state_slots': slots, 'capabilities': caps,
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
        s['measurement.temperature_K'] = s['truth.temperature_K'] if s['power.enabled'] else None
        s['thermal.target_reached'] = bool(s['power.enabled'] and s['truth.temperature_K'] is not None and s['thermal.setpoint_K'] is not None and _ahs_math.isclose(s['truth.temperature_K'], s['thermal.setpoint_K'], rel_tol=0, abs_tol=1e-9))

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
            item['state']['truth.temperature_K'] = float(temperature_K)
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
            item['state'].update({'truth.thermal_rate_K_s': float(thermal_rate_K_s), 'simulation.thermal_rate_configured': True})
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
                temp, target, rate = s['truth.temperature_K'], s['thermal.setpoint_K'], s['truth.thermal_rate_K_s']
                if s['power.enabled'] and temp is not None and target is not None and rate is not None:
                    # Clamp before multiplication so even a huge finite rate cannot overflow.
                    span = abs(target - temp)
                    change = span if dt_s >= span / rate else dt_s * rate
                    s['truth.temperature_K'] = target if change >= span else temp + _ahs_math.copysign(change, target - temp)
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
                s['thermal.setpoint_K'] = float(args['temperature_K'])
                self._refresh(s)
                result = {'setpoint_K': s['thermal.setpoint_K']}
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
    return {'asset_type_id': OPENTRONS_FLEX_TYPE, 'catalog_id': 'CHEM-175', 'asset_version': '1.0.0',
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
                    response['observations'][sid]['reason'] = 'No current attached tip or prior slot event.'
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
    '门为宿主确认的展示滑移关节，几何行程由宿主带证据配置；不声称是原厂门行程、速度或安全互锁认证。',
    'attach/release仅在外部约束回调明确确认后登记；没有机器人规划、接触抓取或硬件驱动。',
    '只有可信宿主管理库存、物质转移账本、身份和时间；API过滤不是Python或共享文件系统隔离。请求去重仅当前内存有效。',
]


def export_quantos_q2_contract_metadata():
    slots = _qt_copy.deepcopy(_QT_SLOTS)
    for sid, slot in slots.items():
        slot['read_binding'] = 'QuantosQ2Session.read(instance_id=..., state_ids=[%r])' % sid
    slots['scene.door_opening_m']['reference_conditions'] = 'quantos__door_slide.location.y=q; +Y opening from authored closed pose; host-configured geometric upper bound'
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
    desc = {'power.set': '设置仿真供电，断电取消任务并保留已投料。', 'dosing.set_target': '设置目标质量。', 'dosing.set_tolerance': '设置绝对允许偏差。', 'head.select': '选择宿主装入的粉末头。', 'container.select_position': '选择宿主已经放置容器的位置。', 'door.set_opening': '设置门展示滑移位置并等待宿主场景确认。', 'balance.tare': '记录显式理想读数模型的容器质量基线。', 'dosing.start': '启动有源库存守恒的异步仿真投料。', 'dosing.read': '读取投料结果且不推进时间。', 'dosing.poll': '读取当前身份拥有的投料进度。', 'dosing.cancel': '取消投料且保留部分转移。', 'autosampler.advance': '可选自动进样器未装配，不支持转位。'}
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
            'model_effects': {'mode': 'host_required' if scene else 'none', 'binding_ids': [], 'description_zh': 'scene_adapter(instance_id,{scene.door_opening_m:q})返回True后提交，apply_quantos_q2_scene_patch映射quantos__door_slide.location.y=q。' if scene else '逻辑装载、称量和投料读数不驱动额外几何；粒子粉末流和屏幕同步未实现。'},
            'execution_notes': {'mode': 'asynchronous' if cid == 'dosing.start' else 'synchronous', 'time_domain': 'simulation', 'cancellable': cid == 'dosing.start', 'completion_zh': '理想观测达到目标即停止，缺料则failed保留部分转移。' if cid == 'dosing.start' else '返回succeeded表示本次操作完成。', 'partial_failure_zh': '取消、断电和缺料保留已转移的质量和已用时间；外部场景回调失败的副作用为unknown。', 'retry_zh': '当前内存中相同请求ID及参数返回原响应；不同参数拒绝；不保证跨重启。'},
            'errors': [{'code': c, 'description_zh': d, 'side_effects': s} for c, d, s in [('INVALID_ARGUMENT', '参数类型、范围或额外字段无效。', 'none'), ('UNKNOWN_INSTANCE', '实例不存在或不在身份范围。', 'none'), ('UNKNOWN_CAPABILITY', '能力不存在。', 'none'), ('ACCESS_DENIED', '槽不可写或身份不能访问。', 'none'), ('PRECONDITION_FAILED', '供电、装载、门、去皮、配置或库存条件不满足。', 'none'), ('BUSY', '投料占用设备。', 'none'), ('REQUEST_CONFLICT', '请求ID参数冲突。', 'none'), ('UNKNOWN_OPERATION', '操作不属于当前身份与实例。', 'none'), ('UNSUPPORTED', '能力未装配或门场景未绑定。', 'none'), ('DEVICE_ERROR', '场景回调失败或未确认。', 'unknown'), ('SOURCE_DEPLETED', '粉末源在达到目标前耗尽；保留部分投料。', 'partial')]]}
    return {'asset_type_id': QUANTOS_Q2_TYPE, 'asset_version': '1.0.0', 'catalog_id': 'CHEM-174', 'state_slots': slots, 'capabilities': caps,
        'parameters': {k: {'value': v, 'unit': u, 'evidence': e} for k,v,u,e in [('target_range_kg', [1e-6,.005], 'kg', 'catalog'), ('caco3_10mg_typical_time_s',22,'s','catalog'), ('caco3_10mg_rsd',.0115,'1','catalog'), ('caco3_50mg_rsd',.0026,'1','catalog'), ('caco3_1000mg_typical_time_s',71,'s','catalog'), ('optional_autosampler_positions',30,'1','catalog'), ('powder_rate_kg_s',None,'kg/s','unknown'), ('balance_readability_kg',None,'kg','unknown'), ('balance_capacity_kg',None,'kg','unknown'), ('door_manufacturer_travel_m',None,'m','unknown')]},
        'host_api': {k: {'entrypoint': 'QuantosQ2Runtime.'+k, 'description_zh': d} for k,d in {
            'create_instance':'独立实例，可选场景回调；所有未给出的物料和参数保持null。', 'register_principal':'可信宿主固定身份、权限与实例范围。', 'host_configure_geometry':'显式几何门行程和证据；关闭位置必须由回调确认。', 'host_load_head':'门开时装入唯一自由流动粉末头，源库存仅debug。', 'host_load_container':'门开时登记容器、位置和内容质量；不会移动几何。', 'host_configure_dosing':'显式正速率和理想质量观察证据；无厂家默认速率。', 'host_step':'dt_s与world_time_s必须匹配共同世界时钟，守恒转移并保存带供体/受体/量的账本。', 'host_attach':'外部attach事件与唯一约束ID确认，robot absent。', 'host_release':'外部release确认后清除约束，不移出容器。', 'host_unload_container':'门开且无附着无任务时移除容器引用，返回已保留内容质量给宿主。', 'host_unload_head':'门开且空闲时卸头，返回剩余粉末质量给宿主。', 'host_transfer_ledger':'仅可信宿主读取带事务ID的物质转移账本。'}.items()},
        'limitations': list(_QT_LIMITATIONS), 'output_schema_scope': 'capabilities.output_schema validates response.result; no process truth/evidence in agent response'}


def apply_quantos_q2_scene_patch(scene, patch):
    """Blender scene bridge; caller must separately verify evaluated geometry."""
    if not isinstance(patch, dict) or set(patch) != {'scene.door_opening_m'} or not _qt_number(patch['scene.door_opening_m']) or patch['scene.door_opening_m'] < 0:
        raise ValueError('Only a finite nonnegative door patch is accepted')
    door = scene.objects.get('quantos__door_slide')
    if door is None:
        raise ValueError('Quantos door target missing')
    if door.animation_data and (door.animation_data.action or door.animation_data.drivers):
        raise ValueError('Door must have a single host authority without animation/driver override')
    if len(door.constraints):
        raise ValueError('Door constraints require explicit authority reconciliation')
    door.location.y = float(patch['scene.door_opening_m'])
    return True


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
                remaining = max(0.0, s['dosing.target_mass_kg'] - s['truth.actual_dosed_kg'])
                amount = min(remaining, s['truth.source_mass_kg'], rate * dt_s)
                if amount == 0 and remaining > 0 and s['truth.source_mass_kg'] > 0:
                    raise ValueError('Transfer below numerical resolution; no instance advanced')
                elapsed = min(dt_s, amount / rate)
                if not all(_qt_number(v) for v in [amount, elapsed, s['truth.container_mass_kg'] + amount, s['dosing.elapsed_s'] + elapsed]):
                    raise ValueError('Transfer would overflow; no instance advanced')
                if amount > 0 and (s['truth.container_mass_kg'] + amount == s['truth.container_mass_kg'] or s['truth.source_mass_kg'] - amount == s['truth.source_mass_kg']):
                    raise ValueError('Mass change below numerical resolution; no instance advanced')
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
                if s['truth.actual_dosed_kg'] >= s['dosing.target_mass_kg']:
                    self._stop(i, 'succeeded')
                elif s['truth.source_mass_kg'] <= 0:
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

# CHEM-176 canonical contract state IDs are lowercase; preserve legacy SI-suffix labels.
_AHS_CONTRACT_STATE_ALIASES = {sid.lower(): sid for sid in _AHS_SLOTS}
_ahs_session_read_legacy = AzentaHeatSealerSession.read
_ahs_session_write_legacy = AzentaHeatSealerSession.write
_ahs_session_describe_legacy = AzentaHeatSealerSession.describe
def _ahs_session_read_contract(self, *, instance_id, state_ids=None):
    if state_ids is None or not isinstance(state_ids, list) or any(not isinstance(sid,str) for sid in state_ids):
        return _ahs_session_read_legacy(self, instance_id=instance_id, state_ids=state_ids)
    actual = [_AHS_CONTRACT_STATE_ALIASES.get(sid,sid) for sid in state_ids]
    response = _ahs_session_read_legacy(self, instance_id=instance_id, state_ids=actual)
    if response['status'] == 'succeeded':
        response['observations'] = {requested: _ahs_copy.deepcopy(response['observations'][source]) for requested,source in zip(state_ids,actual)}
    return response
def _ahs_session_write_contract(self, *, instance_id, state_id, value, request_id):
    actual = _AHS_CONTRACT_STATE_ALIASES.get(state_id,state_id) if isinstance(state_id,str) else state_id
    return _ahs_session_write_legacy(self, instance_id=instance_id, state_id=actual, value=value, request_id=request_id)
def _ahs_session_describe_contract(self, *, asset_type_id=None, instance_id=None):
    metadata = _ahs_session_describe_legacy(self, asset_type_id=asset_type_id, instance_id=instance_id)
    if 'state_slots' in metadata:
        metadata['state_slots'] = {sid.lower():slot for sid,slot in metadata['state_slots'].items()}
        for cap in metadata['capabilities'].values():
            cap['observes'] = [sid.lower() for sid in cap['observes']]
            for effect in cap['state_effects']: effect['state_id'] = effect['state_id'].lower()
        metadata['limitations'].append('合同状态ID采用小写_k/_k_s；显式read/write接受这些规范ID。省略state_ids的旧便捷read保留原实现的SI后缀标签；标准lab.read应显式传入合同状态ID。')
    return metadata
AzentaHeatSealerSession.read = _ahs_session_read_contract
AzentaHeatSealerSession.write = _ahs_session_write_contract
AzentaHeatSealerSession.describe = _ahs_session_describe_contract

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
    return {'asset_type_id': PRECISEFLEX400_TYPE, 'asset_version': '1.0.0', 'catalog_id': 'CHEM-178', 'state_slots': slots, 'capabilities': caps,
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
