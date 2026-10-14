"""expansion_batch03/04 共享物理注释生成器。

沿用 basic40 两批合同的写法：状态槽 meaning_zh 用一到两句中文说明这个量是什么、从哪里来、不代表什么；
压力槽 reference_conditions 写明绝压/表压/压差及参考基准，气体体积流量写参考温压；组件 physical_meaning_zh
写该件在模型里的物理作用（承载、容纳、运动、接口、显示）及哪些是估计；机理参数按 value/unit/meaning_zh/evidence
登记，宿主显式输入的系数标为 assumption。生成的文字来自各运行层的 SLOTS/CAPS/PLANT 定义与几何清单，
不新增任何厂家数据。各运行层的 export_<slug>_contract_metadata() 在返回前调用 annotate_export()，
各台 build_contract.py 在写盘前调用 finalize_contract()。
"""
from __future__ import annotations

import glob
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------------------
# 状态槽
# --------------------------------------------------------------------------------------
KEY_ZH = {
    # 通用
    'power.enabled': '主电源开关状态（true=通电）',
    'airflow.enabled': '抽气风机/干燥气流是否开启',
    'plant.configured': '装置边界（外接气源/液源/容器/系数）是否已由宿主一次性配置',
    'sample.configured': '样品是否已按模板装入并通过身份/相态检查',
    'method.configured': '运行方法是否已配置',
    'kinetics.configured': '显式条件速率/传质系数是否已输入',
    'cassette.configured': '膜盒材质与截留分子量是否已选定',
    'calibration.configured': '用户标线是否已录入',
    'optics.configured': '光源波长与功率组合是否已配置',
    'thermal.configured': '风冷/温控参数是否已配置',
    'isotherm.configured': '产品吸附等温线是否已提供（无则不输出含水率）',
    'queue.configured': '树脂/序列队列是否已配置',
    'cycle.phase': '运行阶段：idle/running/succeeded/cancelled（failed）',
    'cycle.stage': '运行中的子阶段名称，由过程状态机推进',
    'cycle.fault': '最近一次故障或拒绝的原因文字，空串表示无',
    'cycle.error': '面板错误代码代理，空串表示无',
    'cycle.elapsed_s': '本次运行自 acquisition.start 起的仿真时间',
    'cycle.stage_elapsed_s': '当前子阶段已进行的仿真时间',
    'cycle.operation': '当前进行的操作类型（none/measurement/cleaning）',
    'cycle.index': '当前萃取周期序号',
    'cycle.residue_count': '已完成偶联的残基计数',
    'warmup.elapsed_s': '通电后光源/电子学暖机已用时间',
    'warmup.ready': '暖机是否达到允许读数的时长（工程阈值）',
    'timer.mode': '计时模式（timed/continuous）',
    'timer.continuous': '是否连续运行（无定时）',
    'timer.duration_s': '定时时长',
    'timer.remaining_s': '定时剩余时间，连续模式为 null',
    'timer.elapsed_s': '本次运行已计时',
    'timer.paused': '开罩/联锁导致计时暂停',
    'method.mode': '方法模式选择',
    'method.duration_s': '方法总时长/终止时间',
    'method.selected': '当前选中的方法名',
    'method.count': '已存储方法数',
    'method.basis': '含水率计算基准（wet=湿基 MC，dry=干基 DC）',
    'method.size': '本次方法包含的柱位数',
    'method.average_count': '每次读数平均的采样次数',
    'method.ratio_enabled': '比率（多探测器）模式是否启用',
    'method.purge_time_s': 'PURGE 阶段设定的置换时间',
    'method.target_k': '方法目标温度',
    'method.power_limit_w': '微波输出功率上限',
    'method.pressure_limit_pa': '安全压力上限（模型绝压），超过即切断微波',
    'method.stir_rpm': '磁力搅拌转速（rpm，厂家面板单位；SI 换算 rad/s=rpm×2π/60）',
    'method.current_a': '恒电流模式的电流设定',
    'method.cell_voltage_v': '恒槽压模式的两极槽压设定',
    'method.voltage_limit_v': '槽压上限档（10 V 或 30 V）',
    'method.stop_by': '终止条件类型（time/charge）',
    'method.charge_target_c': '按电量终止时的目标电量',
    'attachment.container_id': '宿主登记在样品位上的容器 ID，null 表示空位',
    'sample.loaded': '样品位是否已装入容器/样品',
    'sample.loaded_count': '已装入样品的位数',
    'sample.filled': '样品井是否已注满到可测体积',
    'load.present': '清洗篓/平台上是否登记有负载',
    'load.count': '平台上登记的容器数',
    'fixture.installed': '可选夹具是否已安装',
    'fixture.mass_kg': '夹具质量（宿主输入，计入平台负载）',
    'filter.installed': '是否已装入（场景登记）',
    'electrodes.connected': '两支电极是否已接入电路',
    'gas.connected': '外接冷却气是否接通',
    'gas.ready': '冷却气压力与流量是否满足运行条件（5.5–6 bar，>50 L/min）',
    'rack.mode': '当前安装的样品架规格（8×4 mL 或 4×20 mL）',
    'queue.capacity': 'HT 模块允许的队列容量（none/HT4/HT12 → 1/4/12）',
    'queue.size': '已排入队列的合成任务数',
    'queue.active_index': '当前正在处理的队列序号',
    'panel.mode': '模式指示灯状态',
    # 热学/温度
    'thermal.target_k': '温控目标温度',
    'thermal.temperature_k': '被控对象（腔体/样品/棱镜）的集中参数温度',
    'thermal.indicated_temperature_k': '面板显示的量化温度，由过程温度经一阶传感器响应和分辨率量化得到',
    'thermal.indicated_k': '面板显示温度，由过程模型经传感器响应量化得到',
    'thermal.inlet_k': '干燥气入口温度（加热器出口测点）',
    'thermal.outlet_k': '干燥气出口温度（旋风前测点），不等于颗粒温度',
    'thermal.preheat_enabled': '预热是否开启',
    'thermal.heater_power_w': '加热器瞬时输出功率（占空/比例控制结果）',
    'thermal.block_k': '样品恒温块温度',
    'thermal.sample_k': '样品自身温度（与恒温块有热时间常数差）',
    'temperature.target_k': '温控目标温度',
    'temperature.indicated_k': '显示温度，经传感器响应与量化',
    'heater.enabled': '加热器是否允许工作',
    'heater.lift_m': '沿 +Z 自动升降的位移（密封萃取池）',
    'ultrasound.mode': '超声模式（normal/sweep/degas）',
    'ultrasound.active': '换能器当前是否发声（degas 为占空间歇）',
    'ultrasound.waiting': '是否处于 degas 间歇等待',
    'ultrasound.effective_power_w': '有效声功率（厂家 150 W 有效值，600 W 为峰值不积分）',
    'microwave.output_w': '磁控管当前输出功率（受方法上限与切断逻辑约束）',
    'safety.speed_limit_rad_s': '允许的最高转速（按负载分档保守取值）',
    'safety.temperature_limit_k': '独立安全探头的超温切断阈值',
    # 机械/场景关节
    'clamp.lift_m': '沿关节轴提升的位移',
    'vessel.lower_m': '沿 −Z 拆卸下移的位移',
    'lid.lift_m': '沿 +Z 提升的位移（竖直教学自由度，不复现真实开合机构）',
    'lid.position_m': '沿 +Z 开合的位移（真实为后铰链连杆开合，此处按垂直升降简化）',
    'lid.angle_rad': '绕铰链的开启角',
    'latch.angle_rad': '的转角（OPEN/READ 锁紧）',
    'cover.lift_m': '沿 +Z 提升的位移',
    'cap.lift_m': '沿 +Z 提升的位移',
    'water_lid.lift_m': '沿 +Z 提升的位移',
    'shield.lift_m': '沿 +Z 上推的位移',
    'drawer.extension_m': '沿 −Y 抽出的位移',
    'rack.lift_m': '沿 +Z 自动升降的位移（针刺入隔膜）',
    'holder.lift_m': '沿 +Z 提升的位移',
    'hood.angle_rad': '绕后铰链的开启角',
    'drain.angle_rad': '的转角（0 关，π/2 开）',
    'lock.travel_m': '的横向行程',
    'indicator.lift_m': '机械压力指示器标杆位移（截留侧表压的线性教学映射）',
    'orbit.x_m': '承载的轨道运动在 X 方向的分量',
    'orbit.y_m': '承载的轨道运动在 Y 方向的分量',
    'orbit.phase_rad': '轨道圆周运动的相位角',
    'rotor.angle_rad': '转子视觉相位角，0–2π 循环，不是机械限位',
    'rotor.speed_rad_s': '转子角速度',
    'rotor.rcf_m_s2': '柱内相对离心加速度 ω²r（按标称半径）',
    'shaking.target_rad_s': '轨道摇动目标角速度',
    'shaking.actual_rad_s': '轨道摇动实际角速度（一阶加减速）',
    'shaking.indicated_rad_s': '显示转速（量化）',
    'stir.target_rad_s': '磁力搅拌目标角速度',
    'stir.actual_rad_s': '磁力搅拌实际角速度（一阶响应）',
    'stir.actual_rpm': '磁力搅拌实际转速（rpm，面板单位）',
    'collector.x_m': '收集头 X 滑台位置',
    'collector.y_m': '收集头 Y 滑台位置',
    'collector.tube_index': '当前收集管序号',
    'column.position': '当前选中的柱位（0=旁路）',
    'column.valve_angle_rad': '柱位选择阀转角（教学 0–2.5 rad 对应旁路与柱 1–5）',
    'recirculation.enabled': '泵 B 回流是否开启',
    'recirculation.valve_angle_rad': '回流阀转角（教学 0/π/2）',
    'inlet.route': '入口阀路由（solvent/sample）',
    'outlet.route': '出口阀路由（waste/product）',
    'inlet.angle_rad': '入口阀视觉转角（0/π/2 对应两路）',
    'outlet.angle_rad': '出口阀视觉转角（0/π/2 对应两路）',
    'injection.valves': '两路六通阀的 LOAD/INJECT 状态',
    'fan.fraction': '风扇转速比例（0–1）',
    # 流量/压力/流体
    'flow.drying_m3_s': '干燥气体积流量',
    'flow.feed_m3_s': '蠕动泵进料液体体积流量',
    'flow.circulation_m3_s': '外接蠕动泵的循环液体积流量',
    'flow.permeate_m3_s': '透过液体积流量（膜通量×面积）',
    'flow.targets_m3_s': '两路泵的目标体积流量',
    'flow.actual_m3_s': '两路泵的实际体积流量',
    'flow.target_m3_s': '外接 HPLC 泵的液体目标流量',
    'flow.indicated_m3_s': '显示的液体流量',
    'hydrogen.target_reference_m3_s': '电解产氢目标流量（参考状态体积）',
    'hydrogen.indicated_reference_m3_s': '显示的产氢流量（参考状态体积）',
    'evaporation.kg_s': '水蒸发质量流率（D² 蒸发与热/携湿限幅后的结果）',
    'filter.delta_pa': '尾气滤器相对洁净状态的压差增量（≥2000 Pa 提示维护）',
    'pressure.max_gauge_pa': '六池中最高的池内表压',
    'pressure.gauge_pa': '柱入口表压（液压阻力×流量，含转速相关项）',
    'pressure.feed_gauge_pa': '膜盒进料口表压',
    'pressure.retentate_gauge_pa': '膜盒截留出口表压（机械指示器所接）',
    'pressure.tmp_pa': '跨膜压差 TMP=(进料+截留)/2−透过侧',
    'pressure.indicated_gauge_pa': '压力表显示表压（绝压减宿主环境压）',
    'pressure.indicated_abs_pa': '腔内绝压（EOS 求得）',
    'pressure.indicated_pa': '显示压力',
    'pressure.backpressure_pa': '背压调节器设定表压',
    'pressure.vapor_pa': '当前温度下载液的饱和蒸气压（NIST 拟合，仅原温区）',
    'pressure.vapor_margin_pa': '系统绝压与饱和蒸气压之差，≤0 表示汽化风险',
    'pressure.target_abs_pa': '反应压力目标（绝压）',
    'system.pressure_gauge_pa': '系统表压（泵出口，Ergun 柱阻+管阻）',
    'uv.pressure_gauge_pa': 'UV 流通池处表压',
    'volume.retentate_m3': '截留侧当前液体体积',
    'volume.permeate_m3': '累计透过液体积',
    'process.diavolumes': '换液倍数（累计缓冲液/初始体积）',
    'water.volume_m3': '水箱当前水量',
    'reactor.volume_m3': '反应器/芯片当前液体体积',
    'waste.volume_m3': '累计废液体积',
    'collection.total_m3': '累计收集体积',
    'collection.waste_m3': '切换废液期间的累计体积',
    'collection.product_m3': '产品出口累计体积',
    'bath.volume_m3': '槽内浴液体积',
    'co2.phase': 'CO2 相态（liquid/gas/supercritical/two_phase）由 EOS 判定',
    'critical.physical': '腔内 T、P 是否同时超过 CO2 临界点（物理判据）',
    'critical.controller_pass': '控制器是否判定跨临界（先达 1200 psi 表压再到 34–38 ℃ 的控制判据）',
    # 测量量
    'optical.indicated_index': '显示折射率 nD（钠 D 线，经温度修正与量化）',
    'concentration.indicated_mass_fraction': '按用户标线内插的质量分数，标线外为 null',
    'density.indicated_kg_m3': '显示密度（振动管代理，经温度差与气泡偏差修正后量化）',
    'fill.detected': '填充传感器是否检测到液体',
    'bubble.detected': '相机代理是否检测到气泡',
    'turbidity.indicated_ntu': '显示浊度 NTU（90° 散射通道，比率模式含透射修正）',
    'turbidity.stable': '读数是否在稳定阈值内',
    'calibration.gain': '20 NTU 单点增益校准系数',
    'calibration.status': '校准状态文字',
    'calibration.offset_aw': '水活度线性偏置校准',
    'water_activity.indicated': '显示水活度 aw（露点法代理）',
    'dewpoint.indicated_k': '冷镜露点温度',
    'equilibrium.stable': '头空间水活度是否达到平衡判据',
    'balance.indicated_kg': '内置天平显示质量（0.1 mg 可读性量化）',
    'detector.total_kg_m3': '检测器处总溶质质量浓度（理想信号，不是 UV 吸光度）',
    'column.stationary_fraction': '柱内固定相体积分数（随转速/流量变化）',
    'conductivity.indicated_s_m': '柱后电导率显示',
    'membrane.material': '膜材质（PES/RC）',
    'membrane.mwco_kg_mol': '标称截留分子量',
    'optics.source_wavelengths_m': '四个光源的中心波长',
    'optics.intensities': '四个光源的相对强度设定',
    'optics.emitted_energy_j': '光源累计发射能量',
    'optics.incident_energy_j': '样品位累计入射能量（捕获矩阵）',
    'electrochem.indicated_current_a': '显示电流（0.1 mA 量化）',
    'electrochem.indicated_cell_voltage_v': '显示两极槽压（0.1 V 量化）',
    'electrochem.charge_c': '累计通过电量',
    'sample.temperature_k': '样品温度',
    'sample.reference_ntu': '样品的参考浊度（模板值）',
    'sample.bubble_excess_ntu': '气泡带来的额外浊度（指数衰减）',
    'detector.normalized_90': '90° 散射归一化信号',
    'detector.normalized_transmitted': '透射归一化信号',
    'sample.cell_kg': '振动管内样品质量',
    'sample.waste_kg': '排入废液的样品质量',
    'sample.total_input_kg': '累计注入样品质量',
    'cleaner.input_kg': '累计注入清洗液质量',
    'cleaner.remaining_kg': '池内残留清洗液质量',
    'cleaner.waste_kg': '排出清洗液质量',
    'sample.remaining_m3': '样品井内剩余样液体积',
    'sample.input_m3': '累计装入样品体积',
    'sample.waste_m3': '累计排出样品体积',
    'cleaner.input_m3': '累计清洗液体积',
    'cleaner.remaining_m3': '井内残留清洗液体积',
    'cleaner.waste_m3': '排出清洗液体积',
    'sample.input_kg': '累计装入样品质量',
    'sample.retained_kg': '当前留在仪器内的样品质量',
    'sample.removed_kg': '累计取出样品质量',
    'sample.degraded_kg': '分解暗化计入的质量（身份不变）',
    'sample.retained_m3': '当前留在仪器内的样品体积',
    'sample.removed_m3': '累计取出样品体积',
    'sample.initial_kg': '装样时的样品质量',
    'sample.remaining_kg': '当前样品盘上剩余质量',
    'sample.evaporated_kg': '累计失重（合成失重代理）',
    'water.input_kg': '装入样品所含水质量',
    'water.sample_kg': '样品中当前水质量',
    'water.headspace_kg': '头空间水汽质量',
    'water.removed_kg': '随样品取出的水质量',
    'water.ambient_in_kg': '开盖时从环境进入头空间的水汽',
    'water.ambient_out_kg': '开盖时逸出头空间的水汽',
    'mass.input_kg': '累计装入负载质量',
    'mass.retained_kg': '当前负载质量',
    'mass.removed_kg': '累计取出负载质量',
    'energy.heater_input_j': '加热器累计输入能量',
    'energy.ultrasound_input_j': '换能器累计输入能量',
    'energy.acoustic_loss_j': '声能未转化为热的部分',
    'energy.loading_input_j': '装入负载带入的热能',
    'energy.stored_j': '相对初始状态的储热变化',
    'energy.ambient_loss_j': '向环境散失的热量',
    'energy.loss_j': '向环境散失的热量',
    'energy.unloading_output_j': '取出负载带走的热能',
}

# truth.* 账本项的词表
PLACE_ZH = {
    'initial': '初始装入', 'input': '累计输入', 'retained': '仍在仪器内', 'removed': '已取出', 'waste': '排入废液', 'product': '进入产品/收集瓶',
    'vapor': '已蒸发为气相', 'wall': '粘附在腔壁', 'filter': '滞留在滤器', 'exhaust': '随尾气排出', 'wet_reject': '未干即被剔除', 'reservoir': '在储液瓶',
    'cell': '在池/管内', 'extract': '进入萃取液', 'loop': '在定量环', 'column': '在柱内', 'collected': '已收集', 'tank': '在气瓶', 'chamber': '在腔内',
    'bath': '在样品浴液', 'pore': '在孔隙内', 'sorbed': '吸附态', 'dissolved': '溶解态', 'degraded': '已降解', 'remaining': '剩余', 'bound': '已结合到载体',
    'free': '脱保护游离', 'protected': '仍带保护基', 'correct_free': '正确序列且游离', 'correct_protected': '正确序列且带保护基', 'residual': '残留',
    'vent': '经排气排出', 'stock': '库存', 'working': '工作电极侧', 'counter': '对电极侧', 'permeate': '透过侧', 'retentate': '截留侧', 'adsorbed': '膜吸附',
    'buffer': '缓冲液带入', 'headspace': '头空间', 'generated': '电解生成', 'reacted': '已参与反应', 'vented': '已放空', 'formal': '形式产物', 'sample': '样品侧',
    'side': '副反应', 'target': '目标', 'chamber_co2': '腔内', 'thermal': '热', 'stored': '储存', 'ambient': '环境', 'emitted': '发射', 'absorbed': '吸收',
    'container': '容器', 'transmitted': '透射', 'uncaptured': '未捕获', 'nonthermal': '非热', 'electrical': '电', 'ohmic': '欧姆', 'redox': '氧化还原',
    'heat_out': '散热',
}
SPECIES_ZH = {
    'solute': '溶质', 'liquid': '液体', 'protein': '蛋白', 'osmoles': '渗透活性物质', 'co2': 'CO2', 'solvent': '溶剂', 'solid': '固体', 'target': '目标物',
    'nitrogen': '氮气', 'aa': '氨基酸', 'monomer': '单体', 'tracer': '示踪物', 'substrate': '底物', 'hydrogen': '氢气', 'oxygen': '氧气', 'water': '水',
    'mass': '物料', 'volume': '液体', 'sites': '结合位点', 'base': '碱', 'product': '产物', 'reduced': '还原态', 'oxidized': '氧化态', 'energy': '能量',
    'charge': '电量', 'protected': '保护基', 'free': '游离位点', 'thermal': '热能',
}
QTY_ZH = {'kg': '质量', 'm3': '体积', 'mol': '摩尔数', 'j': '能量', 'pa': '压力', 'k': '温度', 's': '时间', 'w': '功率', 'a': '电流', 'v': '电压', 'c': '电量', 'ohm': '电阻'}

# 某些 truth/特殊槽的整句描述
TRUTH_ZH = {
    'truth.temperature_k': '样品瓶内液体温度（能量守恒求解）',
    'truth.pressure_pa': '瓶内绝压（饱和蒸气压+不凝气分压）',
    'truth.liquid_kg': '瓶内液相质量', 'truth.vapor_kg': '瓶内气相质量',
    'truth.stored_energy_j': '相对初始状态的内能变化', 'truth.absorbed_energy_j': '累计吸收的微波能量', 'truth.heat_out_j': '累计向环境散失的热量',
    'truth.fouling_resistance_ratio': '膜污染附加阻力与洁净阻力之比', 'truth.catalyst_activity': '催化剂相对活性（0–1，随处理量衰减）',
    'truth.cells': '按床层段划分的物种库存（mol）', 'truth.samples': '各样品位的物种与能量账本', 'truth.stock_volumes_m3': '各试剂/单体入口的剩余库存体积',
    'truth.chamber_temperature_k': '反应仓空气温度', 'truth.thermal_input_j': '光源与风扇带入的热', 'truth.stored_thermal_j': '仓内储热变化',
    'truth.emitted_energy_j': '光源累计发射能量', 'truth.absorbed_energy_j_photo': '', 'truth.container_loss_j': '被容器壁吸收/反射的光能',
    'truth.transmitted_energy_j': '穿过样品未被吸收的光能', 'truth.uncaptured_energy_j': '未落到任何样品位的光能', 'truth.nonthermal_energy_j': '进入光化学而未转为热的能量',
    'truth.extent_mol': '目标反应进度（mol 电子当量）', 'truth.sample_charge_c': '流向目标反应的电量', 'truth.target_charge_c': '按目标转化需要的电量',
    'truth.side_charge_c': '流向副反应的电量', 'truth.resistance_ohm': '电解池等效欧姆电阻', 'truth.current_a': '实际电流', 'truth.cell_voltage_v': '实际两极槽压',
    'truth.electrical_energy_j': '累计电功', 'truth.ohmic_energy_j': '欧姆损耗热', 'truth.redox_work_j': '用于氧化还原的电功',
    'truth.bed_pressure_drop_pa': '催化剂床层 Ergun 压降', 'truth.loop_remaining_m3': '定量环中尚未推出的体积',
    'truth.cell_tracer_mol': '芯片各段 CSTR 内的示踪物摩尔数', 'truth.free_sites_mol': '树脂上已脱保护的游离位点', 'truth.protected_sites_mol': '树脂上仍带 Fmoc 的位点',
    'truth.correct_free_mol': '序列正确且游离的链', 'truth.correct_protected_mol': '序列正确且带保护基的链', 'truth.residual_base_mol': '洗涤后残留的碱',
    'truth.working_reduced_mol': '工作电极侧还原态物质', 'truth.working_oxidized_mol': '工作电极侧氧化态物质',
    'truth.counter_reduced_mol': '对电极侧还原态物质', 'truth.counter_oxidized_mol': '对电极侧氧化态物质',
}

# 压力/流量参考条件（按资产、按槽），未列出的按 _reference_generic 推断
REFERENCE = {
    ('buchi_minispray', 'flow.drying_m3_s'): '气体体积流量；参考温压取 plant.configure 的 gas_reference_k/gas_reference_pa（示例 298.15 K、101325 Pa）。厂家 35 m³/h 未注明参考状态，不能直接换算质量流量。',
    ('buchi_minispray', 'flow.feed_m3_s'): '液体实际体积流量，等温不可压缩；按进料温度下的体积计。',
    ('buchi_minispray', 'evaporation.kg_s'): '质量流率，无参考状态依赖。',
    ('buchi_minispray', 'filter.delta_pa'): '压差；滤器相对洁净状态的压升，不是腔室绝压或表压。',
    ('buchi_speedextractore', 'pressure.max_gauge_pa'): '表压；相对 plant.configure 的 ambient_pa（示例 101325 Pa）。厂家 50–150 bar 按表压解释，这是本资产的假设。',
    ('gilson_cpc250', 'pressure.gauge_pa'): '表压；相对环境大气压。厂家 100 bar 上限按表压解释，这是本资产的假设。',
    ('sartorius_vivaflow200', 'pressure.feed_gauge_pa'): '表压；相对环境大气压。厂家进料上限 4 bar。',
    ('sartorius_vivaflow200', 'pressure.retentate_gauge_pa'): '表压；相对环境大气压。厂家截留出口上限 3.5 bar；机械指示器接此口。',
    ('sartorius_vivaflow200', 'pressure.tmp_pa'): '压差；跨膜压 (P_feed+P_retentate)/2 − P_permeate，透过侧背压由宿主 permeate_backpressure_pa 给定。',
    ('sartorius_vivaflow200', 'flow.circulation_m3_s'): '液体实际体积流量，等温不可压缩；由外接蠕动泵在 method.configure 中给定。',
    ('sartorius_vivaflow200', 'flow.permeate_m3_s'): '液体实际体积流量；Darcy 通量×200 cm² 有效面积。',
    ('tousimis_autosamdri815', 'pressure.indicated_gauge_pa'): '表压；绝压减 plant.configure 的 ambient_pa（示例 101325 Pa）。厂家压力表 0–2000 psi 为表压。',
    ('tousimis_autosamdri815', 'pressure.indicated_abs_pa'): '绝压；CoolProp/Span-Wagner 纯 CO2 状态方程求得，用于物理临界判据。',
    ('antonpaar_monowave400', 'method.pressure_limit_pa'): '绝压；模型内压力=饱和蒸气压+不凝气分压。厂家 30 bar 未注明基准，本资产按绝压解释。',
    ('antonpaar_monowave400', 'pressure.indicated_pa'): '绝压；显示值由 truth.pressure_pa 经量化得到。厂家压力传感器基准未明。',
    ('antonpaar_monowave400', 'truth.pressure_pa'): '绝压；恒定头空间理想气体。',
    ('cytiva_ktaoligosynt', 'system.pressure_gauge_pa'): '表压；相对环境大气压。厂家 25 bar g。',
    ('cytiva_ktaoligosynt', 'uv.pressure_gauge_pa'): '表压；相对环境大气压。UV 模块独立 20 bar 额定。',
    ('syrris_asiadiscovery', 'pressure.backpressure_pa'): '表压；BPR 相对环境大气压，厂家 1–20 bar；受供气压力约束。',
    ('syrris_asiadiscovery', 'pressure.indicated_pa'): '表压；各泵出口 = 背压 + 支路阻力×流量，相对环境大气压。',
    ('syrris_asiadiscovery', 'pressure.vapor_pa'): '绝压；载液饱和蒸气压（Antoine/NIST 拟合，仅原温区有效）。',
    ('syrris_asiadiscovery', 'pressure.vapor_margin_pa'): '压差；(背压表压 + 101325 Pa) − 饱和蒸气压，正值表示保持液相。',
    ('syrris_asiadiscovery', 'flow.targets_m3_s'): '液体实际体积流量，等温不可压缩，两路各自给定。',
    ('syrris_asiadiscovery', 'flow.actual_m3_s'): '液体实际体积流量，等温不可压缩。',
    ('thalesnano_hcube', 'flow.target_m3_s'): '液体实际体积流量，等温不可压缩；外接 HPLC 泵 0.3–3 mL/min。',
    ('thalesnano_hcube', 'flow.indicated_m3_s'): '液体实际体积流量，等温不可压缩。',
    ('thalesnano_hcube', 'truth.bed_pressure_drop_pa'): '压差；催化剂床层两端 Ergun 压降，不是系统绝压。',
    ('specac_atlasmanual', 'truth.hydraulic_pressure_pa'): '表压；相对油箱/环境大气压的液压油压真值，由载荷除以活塞面积代理换算，不是表盘吨数。',
    ('buchi_encapsulatorb', 'air.target_pa'): '表压；相对环境大气压（101325 Pa），厂家产品瓶加压 0–1.5 bar 按表压解释。',
}


def _humanize(token: str) -> str:
    return token.replace('_', ' ')


def _ledger_desc(key: str) -> str | None:
    """truth.<place>_<species>_<qty> 或 truth.<place>_<qty> → 中文账本描述。"""
    if key in TRUTH_ZH and TRUTH_ZH[key]:
        return TRUTH_ZH[key]
    leaf = key.split('.', 1)[1]
    m = re.fullmatch(r'([a-z0-9_]+?)_(kg|m3|mol|j|pa|k|s|w|a|v|c|ohm)', leaf)
    if not m:
        return None
    body, qty = m.groups()
    parts = body.split('_')
    place = species = None
    for n in (2, 1):
        if len(parts) >= n and '_'.join(parts[:n]) in PLACE_ZH:
            place = '_'.join(parts[:n]); rest = parts[n:]; break
    if place is None:
        return None
    if rest:
        sp = '_'.join(rest)
        species = SPECIES_ZH.get(sp, _humanize(sp))
    q = QTY_ZH[qty]
    if species:
        return f'{PLACE_ZH[place]}的{species}{q}'
    return f'{PLACE_ZH[place]}{q}'


def _range_text(slot: dict) -> str:
    vs = slot.get('value_schema') or {}
    lo, hi = vs.get('minimum'), vs.get('maximum')
    unit = slot.get('unit')
    if lo is None and hi is None:
        return ''
    def f(x):
        if x is None:
            return '…'
        if isinstance(x, float) and abs(x - round(x)) < 1e-9 and abs(x) < 1e6:
            return str(int(round(x)))
        return f'{x:g}'
    u = '' if unit == '1' else f' {unit}'
    if hi is None:
        return f'，≥{f(lo)}{u}'
    if lo is None:
        return f'，≤{f(hi)}{u}'
    return f'，范围 {f(lo)}–{f(hi)}{u}'


def _writer_of(key: str, slot: dict, caps: dict) -> str | None:
    wv = slot.get('write_via')
    if wv and wv.get('capability_id'):
        return wv['capability_id']
    for cid, cap in caps.items():
        for e in cap.get('state_effects', []):
            if e.get('state_id') == key:
                return cid
    return None


def slot_meaning(slug: str, key: str, slot: dict, caps: dict, comp_names: dict) -> str:
    origin = slot.get('origin')
    prefix, _, leaf = key.partition('.')
    comps = slot.get('component_ids') or []
    cname = comp_names.get(comps[0]) if comps else None
    desc = None
    if prefix == 'truth':
        desc = _ledger_desc(key)
    if desc is None and prefix in ('panel',):
        m = re.fullmatch(r'(time|temperature|mode)_(\d+)', leaf)
        if m:
            what = {'time': '时间 LED 条', 'temperature': '温度 LED 条', 'mode': '模式指示灯'}[m.group(1)]
            desc = f'{what}第 {m.group(2)} 位点亮状态（设定常亮、当前值闪烁为占空代理）'
    if desc is None and re.fullmatch(r'channel_\d+', prefix):
        ch = prefix.split('_')[1]
        desc = {'onset_k': f'第 {ch} 路毛细管的熔化起点温度（透光率首次越过阈值）', 'clear_k': f'第 {ch} 路毛细管的完全透明温度',
                'transmission': f'第 {ch} 路毛细管的合成透光率（0–1）'}.get(leaf)
    if desc is None and prefix == 'uv' and leaf.startswith('channel_'):
        desc = f'UV 第 {leaf.split("_")[1]} 波长通道吸光度（未标定摩尔吸光系数的工程信号）'
    if desc is None:
        desc = KEY_ZH.get(key)
    if desc is None:
        desc = _ledger_desc(key.replace(prefix, 'truth', 1)) if prefix in ('sample', 'water', 'mass', 'energy', 'cleaner') else None
    if desc is None:
        desc = _humanize(key)
    rng = _range_text(slot)
    unit = slot.get('unit')
    is_joint = origin == 'scene' and unit in ('m', 'rad')
    if is_joint:
        who = f'{cname}（{comps[0]}）' if cname else '该部件'
        return f'{who}{desc}{rng}；场景机械状态，关节坐标 0 为闭合/初始位，行程为教学假设而非厂家实测；同步权威与方向见 model_bindings。'
    if origin == 'setpoint':
        writer = _writer_of(key, slot, caps)
        via = f'由 {writer} 写入' if writer else '随方法/配置整组写入'
        return f'{desc}{rng}；设定值，{via}，只记录目标，不等于实际值，也不推进过程。'
    if origin == 'process_truth':
        return f'{desc}{rng}；过程真值，仅 evaluator/debug 可见，无传感器噪声与量化，用于守恒与评测，不是厂家实测。'
    if origin == 'scene':
        return f'{desc}{rng}；场景状态，由宿主登记，不由过程模型推导。'
    if origin == 'sensor':
        return f'{desc}{rng}；传感器读数代理，含一阶响应与分辨率量化，未按厂家精度标定。'
    # derived
    shown = cname and any(t in comps[0] for t in ('display', 'panel', 'led', 'indicator'))
    where = f'显示于{cname}' if shown else '面向 agent 的观测值'
    return f'{desc}{rng}；由运行层模型推导，{where}；未经实机标定，不代表厂家传感器精度。'


def _reference_generic(key: str, slot: dict) -> str | None:
    unit = slot.get('unit')
    if unit == 'Pa':
        if 'gauge' in key:
            return '表压；相对环境大气压（宿主给定，默认 101325 Pa）。'
        if 'abs' in key:
            return '绝压。'
        if any(t in key for t in ('delta', 'tmp', 'drop', 'margin')):
            return '压差；两点压力之差，不是绝压或表压。'
        return None
    if unit == 'm^3/s':
        if 'hydrogen' in key or 'gas' in key:
            return None
        return '液体实际体积流量，等温不可压缩。'
    if unit in ('kg/s', 'mol/s'):
        return '质量/摩尔流率，无参考状态依赖。'
    if unit in ('m', 'rad') and slot.get('origin') == 'scene':
        return '资产坐标系（+X 右、+Y 向后、+Z 上，原点台面投影中心）；关节零位为闭合/初始位，正向沿 URDF 关节轴。'
    if unit == 'K':
        return None
    return None


def slot_reference(slug: str, key: str, slot: dict) -> str | None:
    if slot.get('reference_conditions'):
        return slot['reference_conditions']
    if (slug, key) in REFERENCE:
        return REFERENCE[(slug, key)]
    return _reference_generic(key, slot)


_MANIFEST_CACHE: dict[str, dict] = {}


def manifest_for(slug: str) -> dict:
    if slug not in _MANIFEST_CACHE:
        hits = glob.glob(str(ROOT / 'assets' / 'expansion_batch0*' / '*' / slug / 'geometry_manifest.json'))
        _MANIFEST_CACHE[slug] = json.loads(Path(hits[0]).read_text(encoding='utf-8')) if hits else {'components': []}
    return _MANIFEST_CACHE[slug]


def annotate_slots(slug: str, slots: dict, caps: dict) -> None:
    names = {c['id']: c['name_zh'] for c in manifest_for(slug).get('components', [])}
    for key, slot in slots.items():
        existing = slot.get('meaning_zh') or ''
        if existing.startswith(key + '；派生显示为未标定工程代理'):
            existing = ''
        gen = slot_meaning(slug, key, slot, caps, names)
        # 运行层附加的专门说明保留在生成句之后；若运行层的句子已被 KEY_ZH 吸收进生成句（21–40 号资产的
        # SLOT_ZH 同时喂给 KEY_ZH 和 meaning_zh），不要再重复追加一次
        core = existing.lstrip('；').rstrip('。')
        if core and core in gen:
            existing = ''
        slot['meaning_zh'] = gen if not existing else gen.rstrip('。') + '；' + core + '。'
        slot['reference_conditions'] = slot_reference(slug, key, slot)


# --------------------------------------------------------------------------------------
# 机理参数：宿主显式输入的系数按 assumption 登记
# --------------------------------------------------------------------------------------
PARAM_ZH = {
    'ambient_k': '环境温度', 'ambient_pa': '环境绝压（表压基准）', 'gas_reference_k': '气体体积流量参考温度', 'gas_reference_pa': '气体体积流量参考压力',
    'gas_density_kg_m3': '干燥气密度（参考状态）', 'gas_cp_j_kg_k': '干燥气比热容', 'heater_power_w': '加热器功率预算', 'auxiliary_power_w': '辅机功率预算',
    'thermal_tau_s': '温度一阶响应时间常数', 'pressure_tau_s': '压力一阶响应时间常数', 'chamber_volume_m3': '腔体体积', 'water_density_kg_m3': '水密度',
    'latent_j_kg': '水汽化潜热', 'max_humidity_gain_kg_kg': '干燥气最大携湿量', 'd2_rate_m2_s': 'D² 定律蒸发速率常数', 'wall_rate_s_inv': '壁面粘附速率常数',
    'cyclone_d50_m': '旋风分离切割粒径 d50', 'filter_capture_fraction': '滤器捕集分数', 'damage_rate_s_inv': '热损伤速率常数', 'reservoir_m3': '各溶剂储液体积',
    'nitrogen_gauge_pa': '氮气供气表压', 'nitrogen_mol': '氮气库存', 'nitrogen_mol_s_per_cell': '每池氮吹摩尔流率', 'discharge_m3_s_per_cell': '每池排液体积流量',
    'residual_holdup_fraction': '池内残留持液分数', 'vial_capacity_m3': '收集瓶容量', 'tank_mass_kg': 'CO2 气瓶可用质量', 'supply_gauge_pa': 'CO2 供气表压',
    'co2_purity': 'CO2 纯度', 'cool_target_k': '预冷目标温度', 'cooling_co2_kg_s': '预冷阶段 CO2 消耗速率', 'fill_m3_s': '填充液体流量', 'purge_m3_s': '置换流量',
    'buffer_volume_m3': '缓冲液体积', 'buffer_osmolarity_mol_m3': '缓冲液渗透摩尔浓度', 'permeate_capacity_m3': '透过液容器容量', 'circuit_holdup_m3': '外部管路持液',
    'pes_permeability_m_pa_s': 'PES 膜水力渗透率', 'rc_permeability_m_pa_s': 'RC 膜水力渗透率', 'film_transfer_m_s': '浓差极化膜传质系数', 'channel_resistance_pa_s_m3': '流道液压阻力',
    'permeate_backpressure_pa': '透过侧背压', 'chip_volume_m3': '芯片微通道体积', 'density_kg_m3': '载液密度', 'initial_temperature_k': '初始温度',
    'reservoir_volumes_m3': '两路储液体积', 'loop_volumes_m3': '两路定量环体积', 'loop_concentrations_mol_m3': '两路示踪物浓度', 'dead_volumes_m3': '各段死体积',
    'branch_resistances_pa_s_m3': '各支路液压阻力', 'gas_supply_pa': '供气表压', 'mix_volume_m3': '混合腔体积', 'return_volume_m3': '回流管体积',
    'prime_density_kg_m3': '预充液密度', 'viscosity_pa_s': '液体黏度', 'line_resistance_pa_s_m3': '管路液压阻力', 'outlet_resistance_pa_s_m3': '出口限流阻力',
    'dmtr_increment_kg_mol': 'DMT 基团摩尔质量增量', 'cap_increment_kg_mol': '封端基团摩尔质量增量', 'oxygen_increment_kg_mol': '氧化增加的摩尔质量',
    'high_flow_kit': '是否高流量套件', 'inert_gas_connected': '惰性气体是否接通', 'porosity': '催化剂床孔隙率', 'particle_diameter_m': '催化剂颗粒直径',
    'henry_mol_m3_pa': '氢气亨利常数', 'kla_s_inv': '气液传质系数 kLa', 'rate_constant_m3_mol_s': '形式反应速率常数', 'deactivation_s_inv': '催化剂衰减速率常数',
    'carrier_density_kg_m3': '载液密度', 'exhaust_connected': '尾气是否接通排风', 'dry_matrix_reviewed': '基质是否经宿主审查', 'coefficient_evidence': '系数证据说明',
    'external_peristaltic_pump_connected': '外接蠕动泵是否连接', 'ports_and_reservoirs_connected': '端口与容器是否连接', 'membrane_flushed_wet_confirmed': '膜是否已润湿冲洗',
    'compatible_aqueous_sample_reviewed': '样品水相兼容性是否审查', 'temperature_k': '操作温度', 'dip_tube_liquid_confirmed': '虹吸管液相 CO2 是否确认',
    'condenser_ready_drain_open': '冷凝器就绪且排液打开', 'recipe_reviewed': '配方是否审查', 'max_exhaust_kg_s': '最大排气质量流率',
    'pure_liquid_confirmed': '纯液体载液确认', 'pre_equilibrated': '是否已预平衡', 'low_temperature_available': '低温模块是否可用',
}


def _param_from_schema(name: str, prop: dict, example: dict, cap_name: str) -> dict | None:
    if not isinstance(prop, dict):
        return None
    unit = prop.get('x-unit')
    val = example.get(name)
    typ = prop.get('type')
    if typ == 'boolean':
        unit = '1'
    if unit is None and typ == 'array':
        unit = (prop.get('items') or {}).get('x-unit')
    if unit is None:
        return None
    lo, hi = prop.get('minimum'), prop.get('maximum')
    rng = f'schema 允许 {lo}–{hi} {unit}' if (lo is not None or hi is not None) else None
    zh = PARAM_ZH.get(name, _humanize(name))
    return dict(value=val, unit=unit, meaning_zh=f'{zh}；宿主在 {cap_name} 显式输入，此处登记示例值，非厂家标定或实测。',
                evidence='assumption', source_ids=['simulation_assumption'], uncertainty=rng)


def mechanism_parameters(inputs: dict) -> dict:
    """inputs = {'plant.configure': (SCHEMA, EXAMPLE), ...} → 机理 parameters 字典。"""
    out = {}
    for cap_name, (schema, example) in inputs.items():
        props = (schema or {}).get('properties') or {}
        for name, prop in props.items():
            if name in out:
                continue
            p = _param_from_schema(name, prop, example or {}, cap_name)
            if p is not None and isinstance(p['value'], (int, float, bool, list)) and not isinstance(p['value'], str):
                if isinstance(p['value'], list) and not all(isinstance(x, (int, float, bool)) for x in p['value']):
                    continue
                out[name] = p
    return out


def annotate_export(slug: str, caps: dict, slots: dict, mechanisms: dict, inputs: dict | None = None) -> None:
    annotate_slots(slug, slots, caps)
    if inputs:
        extra = mechanism_parameters(inputs)
        for m in mechanisms.values():
            if not m.get('parameters'):
                m['parameters'] = extra
            else:
                for k, v in extra.items():
                    m['parameters'].setdefault(k, v)


# --------------------------------------------------------------------------------------
# 组件物理意义与几何属性
# --------------------------------------------------------------------------------------
def _fmt(v: float) -> str:
    return f'{v:.3g}' if abs(v) < 1 else f'{v:.4g}'


def _prim_text(comp: dict) -> str:
    cols = comp.get('collisions') or []
    if not cols:
        return '无独立碰撞代理'
    p = cols[0]
    s = p.get('size_m') or []
    if p['shape'] == 'box' and len(s) == 3:
        t = f'{_fmt(s[0])}×{_fmt(s[1])}×{_fmt(s[2])} m 长方体'
    elif p['shape'] == 'cylinder' and len(s) >= 2:
        t = f'半径 {_fmt(s[0])} m、高 {_fmt(s[1])} m 圆柱'
    elif p['shape'] == 'sphere' and s:
        t = f'半径 {_fmt(s[0])} m 球'
    else:
        t = p['shape']
    if len(cols) > 1:
        t += f' 等 {len(cols)} 个基元'
    return f'碰撞代理为{t}'


AXIS_ZH = {(1, 0, 0): '+X', (-1, 0, 0): '−X', (0, 1, 0): '+Y', (0, -1, 0): '−Y', (0, 0, 1): '+Z', (0, 0, -1): '−Z'}


def _joint_text(comp: dict) -> str:
    j = comp.get('joint')
    if not j:
        return ''
    ax = tuple(int(round(a)) for a in j.get('axis', [0, 0, 1]))
    axz = AXIS_ZH.get(ax, str(j.get('axis')))
    lo, hi = j.get('limits', [0, 0])
    sid = j.get('state_id')
    if j['type'] == 'prismatic':
        mot = f'沿 {axz} 平移 {_fmt(lo)}–{_fmt(hi)} m'
    elif j['type'] in ('revolute', 'continuous'):
        mot = f'绕 {axz} 转动 {_fmt(math.degrees(lo))}°–{_fmt(math.degrees(hi))}°'
    else:
        mot = j['type']
    ev = {'engineering_assumption': '行程为教学假设', 'photo_estimate': '行程按照片估计', 'manufacturer_datasheet': '行程有厂家依据',
          'manufacturer_verified': '行程有厂家依据', 'task_envelope': '行程为取放包络'}.get(j.get('evidence'), '行程证据见几何清单')
    grav = ''
    if j['type'] == 'prismatic' and ax[2] != 0:
        grav = '，竖直平移件在物理引擎中受重力，需宿主保持或关闭重力'
    return f'可动件：{mot}，状态 {sid}，{ev}{grav}'


def _tokens(cid: str) -> set[str]:
    return {t for t in re.split(r'[_\d]+', cid) if t}


# (id 词元集合, 名称/功能正则, 角色句)；按顺序首个命中生效。名称正则同时匹配 name_zh；标 F: 的正则还匹配 function_zh
_ROLE_RULES = [
    ({'mark'}, r'定位标|验证点|方向标|定位点|定位子物体', '几何求值验证用标记点，无质量与接触意义，不参与过程'),
    ({'foot', 'feet'}, r'支脚|脚垫|底脚|^脚', '台面支撑点，把整机重力传到台面；摩擦系数与承载分配未标定'),
    (set(), r'过滤器|滤光片|滤片|密封|垫$|棱镜|观察窗|隔膜|隔垫|挡风|卷边|收口|玻璃边缘|透明.*罩|反射罩|隔热',
     '被动物理界面：隔离/过滤/透光/密封作用在运行层按集中参数处理，几何不参与求解'),
    (set(), r'收集管|样品管|毛细管|反应瓶|样品瓶|收集瓶|进液瓶|样品盘|样品杯', '容纳/承载件：容器或样品位，容量与持液由运行层账本记录，宿主 attach/release 登记占用；壁厚与内径为估计'),
    ({'sensor', 'detector', 'thermopile'}, r'振动管|传感器|探测器', '测量/电子件：产生观测量或执行控制逻辑的功能位置，响应、噪声与量化在运行层按代理处理，几何不参与求解'),
    ({'button', 'touch', 'control', 'key', 'switch', 'master'}, r'按键|按钮|触控|开关|功能键|START|STOP|模式按钮|电源外观|电源外形|暂停|继续',
     '操作输入面：接触/按压触发对应能力，Matterix 中为 1.5 mm 弹簧关节或触屏区代理；本身不承载过程物理'),
    ({'cap', 'grip', 'handle', 'nut', 'plug', 'fastener', 'needle', 'belt', 'stem', 'elbow', 'hinge', 'lock', 'latch', 'lever', 'stand', 'arm', 'pin', 'handler', 'pointer'},
     r'瓶盖|管盖|试剂盖|顶盖$|紧固|堵头|皮带|螺母|螺柱|抓手|把手|提手|铰链|锁',
     '被动机械件：紧固、导向、握持或连接，无独立过程作用；真实螺纹、接触与握持未仿真'),
    (set(), r'LED模块|光源|钨灯|照明灯', '有源/能量功能件：加热、泵送、驱动、光电或传质过程在运行层按集中参数模型求解，几何只标记作用位置与朝向，不解场'),
    ({'display', 'screen', 'digits', 'led', 'face', 'tick', 'indicator', 'light', 'gauge'}, r'显示|读数|数显|LED|灯$|表盘|刻线|指示器|指示灯|标尺|温度表|压力表|压力表\d',
     '视觉输出面：只承载显示/指示，运行状态通过 Matterix 段码或颜色映射，不产生力和热'),
    ({'knob', 'dial', 'encoder', 'meter', 'timer', 'selection'}, r'旋钮|计量阀|编码器|计时器',
     '旋转输入件：转角映射为设定值或档位，摩擦与止点未标定，不代表厂家刻度'),
    ({'tube', 'line', 'proxy', 'path', 'lane', 'turn', 'piece'}, r'管路|管线|示意管|流道|折返|连接段', '示意管路/流道：只表达连接拓扑，不从长度推导死体积、阻力或传热'),
    ({'valve', 'regulator', 'restrictor', 'bpr', 'protection', 'rupture', 'disc', 'backpressure', 'overflow'}, r'阀|限流|泄压|破裂盘|背压|调节器',
     '流路控制件：阀位、限流与泄压的状态在运行层，几何仅标记位置与指针方向；真实阀芯、密封与泄压未仿真'),
    ({'port', 'inlet', 'outlet', 'vent', 'usb', 'ethernet', 'lan', 'rs', 'rs232', 'rj', 'can', 'jack', 'socket', 'connector', 'ac', 'data', 'interface', 'connection', 'exhaust', 'drain', 'supply', 'profinet', 'asc', 'dsc', 'micro', 'production', 'system', 'waste', 'monomer', 'ruby', 'autosampler', 'sd', 'fuse', 'in', 'out', 'power'},
     r'接口|入口|出口|排气|插口|电源|通讯|供电|排液口|排液阀|散热孔|通风|收集探针|收集针|试剂物理入口',
     '接口点：流体/电气/数据连接的几何位置，只作拓扑端点；接头规格、密封与协议未实现'),
    ({'heater', 'peltier', 'magnetron', 'transducer', 'transducers', 'motor', 'pump', 'fan', 'cooler', 'solenoid', 'electrolyzer', 'electrolysis', 'source', 'aspirator', 'waveguide', 'nozzle', 'cyclone', 'mixer', 'divider', 'drive', 'servo', 'eccentric', 'head', 'stir', 'bar', 'electrode', 'condenser', 'lamp', 'climate', 'syringe', 'block', 'cavity', 'cooling', 'unit'},
     r'加热|电机|泵|风扇|风机|电磁阀|换能|磁控|光源|冷却|冷凝|电解|喷嘴|喷口|旋风|混合|分流|驱动|波导|偏心|搅拌|电极|抽气|控温|恒温块|注射器|微波',
     '有源/能量功能件：加热、泵送、驱动、光电或传质过程在运行层按集中参数模型求解，几何只标记作用位置与朝向，不解场'),
    ({'sensor', 'detector', 'probe', 'camera', 'monitor', 'dad', 'thermopile', 'mirror', 'pt', 'ir', 'fiber', 'optical', 'excitation', 'potentiostat', 'electronics', 'measuring', 'pressure', 'balance'},
     r'传感|探测|相机|探头|电导|压力模块|柱前压力|冷镜|电子学|振动管|激励|电流采样|电压采样|检测|称重',
     '测量/电子件：产生观测量或执行控制逻辑的功能位置，响应、噪声与量化在运行层按代理处理，几何不参与求解'),
    ({'membrane', 'filter', 'seal', 'gasket', 'prism', 'lens', 'window', 'glass', 'septum', 'mesh', 'shield', 'rim', 'back', 'insulation', 'reflector'}, r'滤|膜|密封|棱镜|透镜|隔垫|隔膜|垫|挡风|玻璃',
     '被动物理界面：隔离/过滤/透光/密封作用在运行层按集中参数处理，几何不参与求解'),
    ({'base', 'housing', 'cabinet', 'plate', 'frame', 'header', 'rail', 'panel', 'deck', 'shell', 'chassis', 'board', 'body', 'front', 'rear', 'upper', 'left', 'right', 'lip', 'neck', 'basin', 'box', 'pc', 'module', 'injector', 'collector', 'cover', 'lid', 'hood', 'top', 'case', 'terminal', 'floor', 'store', 'tray', 'holder', 'post'},
     r'底板|机座|外壳|机壳|柜|立柱|横梁|框|面板|台面|背板|机舱|机身|舱|电脑|模块|围壁|护边|置物台|盖$|罩$|终端|底面|法兰|台$|盘架|排液盘|支杆|支架|集水盘',
     '承载结构：固定连接于父件，只提供支撑、围护与安装面，不参与过程物理'),
    ({'vial', 'bottle', 'cup', 'flask', 'vessel', 'tank', 'reservoir', 'cell', 'cartridge', 'column', 'chamber', 'basket', 'rack', 'slot', 'well', 'capillary', 'loop', 'chip', 'reactor', 'aa', 'resin', 'die', 'cylinder', 'sample'},
     r'瓶|杯|池|腔|篓|架|槽|柱|定量环|收集管|毛细管|芯片|反应容器|氨基酸位|试剂位|树脂位|水箱|圆筒|分离瓶|插孔|方片位|样品井|样品位|样品盘|F:外接瓶|F:试剂瓶|F:进液瓶',
     '容纳/承载件：容器或样品位，容量与持液由运行层账本记录，宿主 attach/release 登记占用；壁厚与内径为估计'),
]

_MOVING_ZH = [
    ({'lid', 'cover', 'hood', 'shield', 'door', 'cap'}, r'盖|罩|屏|门', '开合边界件：开合状态参与运行层互锁（开盖停机/不允许启动），接触与握持未经机器人验证'),
    ({'clamp', 'lift', 'holder', 'rack'}, r'夹|升降|架', '夹持/升降件：位置决定装卸与密封是否允许，真实夹紧力与行程未测'),
    ({'drawer'}, r'托板|抽', '抽拉件：抽出位置决定装样是否允许，磁吸定位未仿真'),
    ({'valve'}, r'阀', '阀件：转角映射阀位状态，真实阀芯与密封未仿真'),
    ({'knob', 'encoder'}, r'旋钮|旋柄', '旋转输入件：转角映射为设定值，摩擦与止点未标定'),
    ({'rotor'}, r'转子', '转子：转角为视觉相位，角速度由运行层给出，不解转子动力学'),
    ({'collector', 'orbit', 'platform'}, r'滑台|滑块|平台|载架', '运动平台/滑台：位置为运动学映射，不解驱动动力学'),
    ({'plunger', 'indicator', 'pin', 'lock'}, r'标杆|锁销', '指示/锁定件：位移是过程量的线性教学映射'),
]


def _match(pat: str | None, comp: dict) -> bool:
    if not pat:
        return False
    name, fn = comp.get('name_zh', ''), comp.get('function_zh', '')
    for alt in pat.split('|'):
        if alt.startswith('F:'):
            if re.search(alt[2:], fn):
                return True
        elif re.search(alt, name):
            return True
    return False


def _role(comp: dict) -> str:
    cid, name = comp['id'], comp.get('name_zh', '')
    toks = _tokens(cid)
    if comp.get('joint'):
        for tset, pat, text in _MOVING_ZH:
            if toks & tset or (pat and re.search(pat, name)):
                return text
        return '可动件：运动学教学自由度，不解驱动动力学'
    for tset, pat, text in _ROLE_RULES:
        if (tset and toks & tset) or _match(pat, comp):
            return text
    return '功能几何分件：作用见 function_zh，几何不参与过程求解'


def component_meaning(slug: str, comp: dict) -> str:
    role = _role(comp)
    parent = comp.get('parent') or 'root'
    o = comp.get('origin_m') or [0, 0, 0]
    pos = f'装配原点 ({_fmt(o[0])}, {_fmt(o[1])}, {_fmt(o[2])}) m 相对 {parent}'
    jt = _joint_text(comp)
    ev = {'engineering_assumption': '局部尺寸为工程估计', 'photo_estimate': '局部尺寸按照片估计', 'manufacturer_datasheet': '尺寸有厂家依据',
          'manufacturer_verified': '尺寸有厂家依据'}.get(comp.get('geometry_evidence'), '尺寸证据见几何清单')
    mass = '质量/惯量/摩擦未知，URDF 数值占位' if comp.get('mass_kg') is None else f'质量 {comp["mass_kg"]} kg（{comp.get("mass_evidence")}）'
    pieces = [role, jt, f'{pos}，{_prim_text(comp)}', f'{ev}，{mass}']
    return '；'.join(p for p in pieces if p) + '。'


def _obj_bbox(path: Path):
    lo = [math.inf] * 3; hi = [-math.inf] * 3; n = 0
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                if line.startswith('v '):
                    p = line.split()
                    for i in range(3):
                        v = float(p[i + 1]); lo[i] = min(lo[i], v); hi[i] = max(hi[i], v)
                    n += 1
    except OSError:
        return None
    if n == 0:
        return None
    return [hi[i] - lo[i] for i in range(3)]


def component_property_extras(slug: str, comp: dict, asset_dir: Path) -> dict:
    """basic40 batch02 风格的几何属性：assembly_origin、modeled_bbox_dimensions、bbox_inertia_per_unit_mass、关节轴与行程。"""
    def prop(value, unit, meaning, evidence, sources, uncertainty=None):
        return dict(value=value, unit=unit, meaning_zh=meaning, evidence=evidence, source_ids=sources, uncertainty=uncertainty)
    geo_ev = 'photo_estimate' if comp.get('geometry_evidence') == 'photo_estimate' else 'assumption'
    out = {
        'assembly_origin': prop([float(x) for x in comp.get('origin_m', [0, 0, 0])], 'm',
                                f'相对父件 {comp.get("parent") or "root"} 的装配原点，资产坐标系 +X 右、+Y 向后、+Z 上；按厂家外廓与照片布局估计，不是厂家 CAD 坐标。', geo_ev, ['geometry']),
    }
    mesh = asset_dir / 'meshes' / f'{slug}__{comp["id"]}.obj'
    ext = _obj_bbox(mesh)
    if ext:
        out['modeled_bbox_dimensions'] = prop([round(e, 5) for e in ext], 'm', '当前网格在部件局部坐标下的包围盒 XYZ 边长；形状按照片和厂家外廓重建。', geo_ev, ['geometry'])
        out['bbox_inertia_per_unit_mass'] = prop([round((ext[1] ** 2 + ext[2] ** 2) / 12, 8), round((ext[0] ** 2 + ext[2] ** 2) / 12, 8), round((ext[0] ** 2 + ext[1] ** 2) / 12, 8)],
                                                 'm^2', '均匀实心包围盒的 Ixx/m、Iyy/m、Izz/m；URDF 惯量=该值×占位质量，圆柱件按其包围盒计，比真实圆柱偏大约 1/3。', 'assumption', ['geometry'],
                                                 '真实质量分布未知；不作为接触动力学已验证的依据。')
    j = comp.get('joint')
    if j:
        unit = 'm' if j['type'] == 'prismatic' else 'rad'
        out['joint_axis'] = prop([float(a) for a in j.get('axis', [0, 0, 1])], '1', f'{j["type"]} 关节轴（部件局部坐标，与 URDF 一致）。', geo_ev, ['geometry'])
        out['joint_travel'] = prop([float(x) for x in j.get('limits', [0, 0])], unit, f'关节下限/上限，对应状态槽 {j.get("state_id")}；0 为闭合/初始位。',
                                   'assumption' if j.get('evidence') in (None, 'engineering_assumption') else ('photo_estimate' if j.get('evidence') == 'photo_estimate' else 'documented'),
                                   ['geometry'], '教学行程，非厂家实测机械止点。' if j.get('evidence') in (None, 'engineering_assumption') else None)
    return out


def finalize_contract(c: dict, asset_dir: Path) -> None:
    """build_contract.py 写盘前调用：按几何清单重写各组件 physical_meaning_zh 并补几何属性。"""
    slug = c['asset_type_id'].split('.')[-1]
    man = manifest_for(slug)
    by_id = {m['id']: m for m in man.get('components', [])}
    for cid, comp in c['components'].items():
        m = by_id.get(cid)
        if not m:
            continue
        comp['physical_meaning_zh'] = component_meaning(slug, m)
        extras = component_property_extras(slug, m, Path(asset_dir))
        for k, v in extras.items():
            comp['properties'].setdefault(k, v)
    root = c['components'].get('root')
    if root and root.get('physical_meaning_zh') in (None, '', '厂家整机参数仅用于整机'):
        root['physical_meaning_zh'] = '装配根：承载整机厂家参数（外廓、质量、额定范围），不分摊到子件；场景放置以台面投影中心为原点。'


def annotate_manifest(slug: str) -> int:
    """把组件 physical_meaning_zh 写回 geometry_manifest.json，使清单与合同一致；返回改动数。"""
    hits = glob.glob(str(ROOT / 'assets' / 'expansion_batch0*' / '*' / slug / 'geometry_manifest.json'))
    if not hits:
        return 0
    p = Path(hits[0]); man = json.loads(p.read_text(encoding='utf-8')); n = 0
    for comp in man.get('components', []):
        new = component_meaning(slug, comp)
        if comp.get('physical_meaning_zh') != new:
            comp['physical_meaning_zh'] = new; n += 1
    if n:
        p.write_text(json.dumps(man, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        _MANIFEST_CACHE.pop(slug, None)
    return n
