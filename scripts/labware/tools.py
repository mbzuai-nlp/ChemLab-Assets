"""Powder tools and the gas cylinder: Globe Scientific 3620 weighing boat, Bochem 3340 micro spoon spatula, 50 L / 200 bar steel
cylinder (Rama / PWENT, ISO 9809-1) with a DIN 477 valve and a Linde REDLINE C 200/2 two-stage regulator."""
from __future__ import annotations

import math

import numpy as np
import trimesh

from labware_kit import FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, LabwareAsset, Link, Prim, frame, revolve, top_down_frames
from sources import pick

PS = 1050.0
STEEL18_10 = 7900.0
STEEL = 7850.0
BRASS = 8500.0
WHITE_PS = (0.97, 0.97, 0.97, 1.0)
STAINLESS = (0.78, 0.79, 0.81, 1.0)
CYL_GREY = (0.45, 0.47, 0.50, 1.0)
BRASS_C = (0.78, 0.65, 0.32, 1.0)
CHROME = (0.82, 0.83, 0.85, 1.0)
KNOB_BLACK = (0.10, 0.10, 0.10, 1.0)


def build_weighing_boat() -> LabwareAsset:
    src = pick("globe_3620", "pp_hdpe_density", "robot_access")
    src["boat_assumption"] = {"kind": "assumption", "reference": "weighing boat floor size and wall thickness", "locator": "engineering value",
                              "note": "Floor 31 x 31 mm (walls taper from the 41 x 41 mm rim over 8 mm), PS sheet 0.5 mm visual / 1.2 mm collision; mass from the sheet volume x 1050 kg/m3.",
                              "licence": "n/a"}
    a = LabwareAsset("globe_weighing_boat_3620", "labware.globe_weighing_boat_3620", "Globe Scientific 3620 防静电方形称量舟（41×41×8 mm，PS）",
                     "Globe Scientific 3620 antistatic square weighing boat 41 x 41 x 8 mm", "tools", "labware.tool.weighing_boat", "passive",
                     description_zh="小号防静电聚苯乙烯方形称量舟 Globe Scientific 3620（与 Heathrow HS1421A / Sigma Z708542 同尺寸）：口部 41×41 mm、深 8 mm，目录标称 20 mL。", sources=src)
    top, bot, h = 0.041, 0.031, 0.008
    tv, tc = 0.0005, 0.0012
    prims_v, prims_c = [Prim("box", [bot, bot, tv], [0, 0, tv / 2])], [Prim("box", [bot, bot, tc], [0, 0, tc / 2])]
    slope = math.atan2((top - bot) / 2, h)
    L = math.hypot((top - bot) / 2, h)
    rm = (top + bot) / 4
    for k in range(4):
        yaw = k * math.pi / 2
        for lst, t in ((prims_v, tv), (prims_c, tc)):
            rc, zc = rm - t / 2 * math.cos(slope), h / 2 + t / 2 * math.sin(slope)    # outer face through (bot/2, 0)-(top/2, h)
            lst.append(Prim("box", [t, top - 2 * t, L], [rc * math.cos(yaw), rc * math.sin(yaw), zc], [0.0, slope, yaw]))
    vis = trimesh.util.concatenate([p.mesh() for p in prims_v])
    vol = sum(p.mesh().volume for p in prims_v)
    a.add(Link("body", "称量舟", "称量粉末；放在天平秤盘上", "PS 薄片方舟（防静电）", visual=[(vis, WHITE_PS)], collision=prims_c, mass_kg=vol * PS,
               mass_evidence="assumption", mass_note_zh=f"0.5 mm PS 片体积 × {PS:.0f} kg/m3", material="ps", geometry_evidence="documented",
               geometry_note_zh="口部 41×41 mm、深 8 mm 按目录；底面 31×31 mm 与壁厚为假设"))
    cap = h / 3 * (top * top + bot * bot + top * bot)
    zg = h - 0.003
    fr = top_down_frames(zg, h, top, yaw_free=False, contact_z=(0.0, h),
                         note_zh="俯抓两侧壁（41 mm），手指沿 Y 合拢；装粉后保持水平")
    a.labware = {"type_id": a.asset_type_id, "family": "weighing_boat", "labware_class": "weighing_boat", "fits": ["weighing_boat", "balance_pan"],
                 "maker": "Globe Scientific", "model": "Weighing boat, square, antistatic PS, 41 x 41 x 8 mm, 20 mL", "catalogue_no": "3620",
                 "nominal_capacity_m3": round(cap, 9), "catalogue_capacity_label_m3": 2.0e-5, "brim_capacity_m3": round(cap, 9),
                 "nominal_capacity_evidence": "computed from the documented rim size and depth (the catalogue label says 20 mL, more than the 13.4 mL of a 41 x 41 x 8 mm box: label kept separately)",
                 "interior": {"shape": "frustum_square", "top_m": top, "bottom_m": bot, "floor_m": tv, "height_m": h, "radius_m": bot / 2},
                 "frames": fr, "empty_mass_kg": round(vol * PS, 6), "franka_grasp_ok": True, "stands_upright": True,
                 "powder_note_zh": "粉末量由宿主账本记录；称量舟放在天平秤盘（SPX2201 敞开秤盘）上读数"}
    a.prop("body", "rim_size_m", [top, top], "m", "口部尺寸", "documented", ["globe_3620"])
    a.prop("body", "depth_m", h, "m", "深度", "documented", ["globe_3620"])
    a.prop("body", "catalogue_capacity_m3", 2.0e-5, "m3", "目录标称容量 20 mL（大于几何容积，作标签保留）", "documented", ["globe_3620"])
    a.contract_ports = {"tray": {"component_id": "body", "medium": "mechanical", "direction": "bidirectional", "frame": "frame:grasp",
                                 "meaning_zh": "舟内放粉末/固体", "connection_constraints": ["加入/倒出属于机器人技能（药匙）与宿主记账"]}}
    a.limitations_zh = ["口部尺寸与深度按目录；底面尺寸与壁厚为假设；碰撞壁加厚到 1.2 mm 便于物理稳定。", "粉末不生成粒子，只记账。"]
    return a


def build_spatula() -> LabwareAsset:
    src = pick("bochem_3340", "robot_access")
    src["spatula_assumption"] = {"kind": "assumption", "reference": "spatula rod diameter, spoon depth, blade thickness", "locator": "engineering value",
                                 "note": "Rod O3.5 mm, spoon bowl O9 x 2.5 mm deep, flat blade 40 x 5 x 0.8 mm; 18/10 stainless 7900 kg/m3.", "licence": "n/a"}
    a = LabwareAsset("bochem_spatula_3340", "labware.bochem_spatula_3340", "Bochem 3340 微量药匙（18/10 不锈钢，150 mm，Ø9 mm 匙头 + 平铲）",
                     "Bochem 3340 micro spoon spatula, 18/10 stainless steel, 150 mm", "tools", "labware.tool.spatula", "tool",
                     description_zh="双头微量药匙 Bochem 3340：总长 150 mm、Ø9 mm 匙头、40 mm 平铲；平放在台面上（沿 X 轴）。", sources=src)
    r_rod = 0.00175
    rod = Prim("cylinder", [r_rod, 0.090], [-0.010, 0, r_rod], [0, math.pi / 2, 0])
    blade = Prim("box", [0.040, 0.005, 0.0008], [0.055, 0, 0.0004])
    neck = Prim("box", [0.006, 0.0035, 0.0012], [0.036, 0, 0.0006 + 0.0004])
    bowl_r, bowl_d = 0.0045, 0.0025
    prof = [(0.0, 0.0)] + [(bowl_r * math.sin(t), bowl_d * (1 - math.cos(t))) for t in np.linspace(0.15, math.pi / 2, 8)] + [(0.0, bowl_d)]
    bowl = revolve(prof, sections=24)                                 # solid half-ellipsoid spoon bowl, flat side up
    bowl.apply_translation([-0.0705, 0, 0])
    vis = trimesh.util.concatenate([rod.mesh(), blade.mesh(), neck.mesh(), bowl])
    col = [rod, blade, neck, Prim("cylinder", [bowl_r, bowl_d], [-0.0705, 0, bowl_d / 2])]
    vol = rod.mesh().volume + blade.mesh().volume + neck.mesh().volume + abs(bowl.volume) * 0.35
    a.add(Link("body", "药匙", "取粉末/固体（匙头）或刮平（平铲）", "18/10 不锈钢", visual=[(vis, STAINLESS)], collision=col, mass_kg=vol * STEEL18_10,
               mass_evidence="assumption", mass_note_zh="钢件体积 × 7900 kg/m3（匙头按 35 % 实心）", material="stainless_18_10", geometry_evidence="documented",
               geometry_note_zh="总长 150、匙头 Ø9、平铲 40×5 mm 按目录；杆径与厚度为假设"))
    bowl_vol = (2.0 / 3.0) * math.pi * bowl_r * bowl_r * bowl_d
    fr = top_down_frames(r_rod, 2 * r_rod, 2 * r_rod, yaw_free=False, xy=(-0.010, 0.0), contact_z=(0.0, 2 * r_rod),
                         note_zh="俯抓杆中部（Ø3.5 mm），手指沿 Y 合拢；平放时 TCP 贴近台面")
    fr["spoon"] = frame([-0.0705, 0, bowl_d], "base_link", rot=[0, 0, 0, 1], note_zh="匙头中心（取粉点）")
    fr["blade_tip"] = frame([0.075, 0, 0.0004], "base_link", rot=[0, 0, 0, 1], note_zh="平铲端")
    a.labware = {"type_id": a.asset_type_id, "family": "spatula", "labware_class": "spatula", "fits": ["spatula", "tool_holder"],
                 "maker": "Bochem", "model": "Micro spoon spatula, 18/10 stainless steel, 150 mm (spoon O9 mm, blade 40 mm)", "catalogue_no": "3340",
                 "spoon_capacity_level_m3": round(bowl_vol, 10), "spoon_capacity_heaped_m3": round(bowl_vol * 1.6, 10),
                 "frames": fr, "empty_mass_kg": round(vol * STEEL18_10, 5), "franka_grasp_ok": True, "stands_upright": False,
                 "powder_note_zh": "一匙平/堆取量 ≈ spoon_capacity × 粉末堆密度；转移量由宿主账本记录（powder handling）"}
    a.prop("body", "length_m", 0.150, "m", "总长", "documented", ["bochem_3340"])
    a.prop("body", "spoon_diameter_m", 0.009, "m", "匙头直径", "documented", ["bochem_3340"])
    a.prop("body", "blade_length_m", 0.040, "m", "平铲长", "documented", ["bochem_3340"])
    a.prop("body", "rod_diameter_m", 2 * r_rod, "m", "杆径", "assumption", ["spatula_assumption"])
    a.limitations_zh = ["总长、匙头直径、平铲长度按目录；杆径、匙深、厚度为假设。", "取粉量按匙头容积 × 堆密度由宿主计算，不模拟颗粒。"]
    return a


def build_gas_cylinder(size: str = "50l") -> LabwareAsset:
    big = size == "50l"
    src = pick("rama_50l_cylinder" if big else "rama_10l_cylinder", "linde_c200_2", "inventory_places")
    src["cylinder_assumption"] = {"kind": "assumption", "reference": "cylinder shoulder/foot profile, DIN 477 valve and handwheel, regulator body",
                                  "locator": "engineering value",
                                  "note": "Shoulder from z 1.32 m to the neck at 1.50 m; brass valve 50 x 50 x 95 mm with O60 mm handwheel (2 turns from closed to open) and a side outlet to the regulator; regulator body O62 x 50 mm with two O50 gauges and an O45 adjusting knob (5 turns over its outlet range) laid out to the 178 x 150 mm drawing; valve mass 1.2 kg.",
                                  "licence": "n/a"}
    cyl_src = "rama_50l_cylinder" if big else "rama_10l_cylinder"
    R, L, M, V = (0.1145, 1.50, 54.0, 0.050) if big else (0.070, 0.85, 14.9, 0.010)
    tag = "50l" if big else "10l"
    a = LabwareAsset(f"gas_cylinder_{tag}_200bar", f"labware.gas_cylinder_{tag}_200bar",
                     f"{V*1000:.0f} L / 200 bar 钢瓶（Ø{2*R*1000:.0f}×{L*1000:.0f} mm，DIN 477 瓶阀 + Linde C 200/2 两级减压器）",
                     f"Steel gas cylinder {V*1000:.0f} L 200 bar with DIN 477 valve and Linde REDLINE C 200/2 regulator", "containers", "labware.container.gas_cylinder", "passive",
                     description_zh=f"欧洲常用 {V*1000:.0f} L、200 bar 无缝钢瓶（Rama / PWENT，ISO 9809-1）：直径 {2*R*1000:.0f} mm、长 {L*1000:.0f} mm、约 {M} kg；装 DIN 477 瓶阀与 Linde REDLINE C 200/2 两级减压器（3.33 kg）。"
                                    "放在 cylinder_rack（DENIOS 气瓶架）上；气体量由宿主账本记录（清单的默认气源）。", sources=src)
    k = R / 0.1145                                                       # shoulder profile scaled from the 50 L shape
    prof = [(0.0, 0.0), (R - 0.0145 * k, 0.0), (R - 0.0045 * k, 0.004), (R, 0.020), (R, L - 0.18 * k), (0.105 * k, L - 0.12 * k), (0.085 * k, L - 0.07 * k),
            (0.055 * k, L - 0.03 * k), (max(0.030 * k, 0.027), L - 0.01), (0.026, L), (0.0, L)]
    shell = revolve(prof, sections=64)
    a.add(Link("body", "钢瓶", "储存压缩气体（200 bar）", "无缝钢瓶（ISO 9809-1）",
               visual=[(shell, CYL_GREY)], collision=[Prim("cylinder", [R, L - 0.14 * k], [0, 0, (L - 0.14 * k) / 2]), Prim("cylinder", [0.075 * k, 0.14 * k], [0, 0, L - 0.07 * k])],
               mass_kg=M, mass_evidence="documented", mass_note_zh=f"目录 {M} kg（未注明是否含阀；按空瓶质量）", material="steel",
               geometry_evidence="documented", geometry_note_zh=f"直径 {2*R*1000:.0f}、长 {L*1000:.0f} mm 按目录；肩部轮廓为假设"))
    a.properties["_mass_sources"] = {"body": [cyl_src]}
    valve_prims = [Prim("box", [0.050, 0.050, 0.095], [0, 0, 0.0475]), Prim("cylinder", [0.010, 0.040], [0, -0.045, 0.055], [math.pi / 2, 0, 0])]
    a.add(Link("valve", "DIN 477 瓶阀", "开关钢瓶；侧出口接减压器", "黄铜阀体", parent="body", origin_m=[0, 0, L],
               visual=[(trimesh.util.concatenate([p.mesh() for p in valve_prims]), BRASS_C)], collision=valve_prims, mass_kg=1.2,
               mass_evidence="assumption", mass_note_zh="黄铜阀 1.2 kg（假设）", material="brass", geometry_evidence="assumption"))
    hw = [Prim("cylinder", [0.030, 0.012], [0, 0, 0.006]), Prim("cylinder", [0.008, 0.012], [0, 0, -0.006])]
    a.add(Link("handwheel", "瓶阀手轮", "逆时针（俯视）开阀", "手轮；转角对应阀开度（宿主记录）", parent="valve", origin_m=[0, 0, 0.107],
               joint={"type": "revolute", "axis": [0, 0, 1], "limits": [0.0, round(4 * math.pi, 6)], "evidence": "assumption", "source_ids": ["cylinder_assumption"],
                      "note_zh": "正方向 = 俯视逆时针 = 开阀；0 = 关，2 圈 = 全开"},
               visual=[(trimesh.util.concatenate([p.mesh() for p in hw]), KNOB_BLACK)], collision=hw, mass_kg=0.12, mass_evidence="assumption",
               mass_note_zh="手轮 0.12 kg（假设）", material="pa", geometry_evidence="assumption"))
    reg = [Prim("cylinder", [0.031, 0.050], [0, 0, 0], [math.pi / 2, 0, 0]),
           Prim("cylinder", [0.025, 0.030], [-0.040, 0, 0.055], [math.pi / 2, 0, 0]), Prim("cylinder", [0.025, 0.030], [0.040, 0, 0.055], [math.pi / 2, 0, 0]),
           Prim("box", [0.060, 0.016, 0.016], [-0.060, 0, 0]), Prim("box", [0.070, 0.016, 0.016], [0.065, 0, 0])]
    a.add(Link("regulator", "Linde REDLINE C 200/2 两级减压器", "把 200 bar 降到工作压力（1–10 bar）", "镀铬黄铜，两只压力表，出口 6 mm 卡套",
               parent="valve", origin_m=[0, -0.095, 0.055],
               visual=[(trimesh.util.concatenate([p.mesh() for p in reg]), CHROME)], collision=reg, mass_kg=3.33, mass_evidence="documented",
               mass_note_zh="数据表最大 3.33 kg", material="brass_chrome", geometry_evidence="documented", detachable=False,
               geometry_note_zh="外形约 178×150 mm 按数据表图；部件布局为近似"))
    a.properties["_mass_sources"]["regulator"] = ["linde_c200_2"]
    kn = [Prim("cylinder", [0.0225, 0.030], [0, 0, 0], [math.pi / 2, 0, 0])]
    a.add(Link("regulator_knob", "减压器调节旋钮", "顺时针（从前看）升高出口压力", "调节弹簧预紧；转角对应出口设定压力（宿主记录）",
               parent="regulator", origin_m=[0, -0.040, 0],
               joint={"type": "revolute", "axis": [0, -1, 0], "limits": [0.0, round(10 * math.pi, 6)], "evidence": "assumption", "source_ids": ["cylinder_assumption"],
                      "note_zh": "正方向 = 从前看顺时针 = 加压；0 = 卸载，5 圈 = 出口满量程"},
               visual=[(trimesh.util.concatenate([p.mesh() for p in kn]), KNOB_BLACK)], collision=kn, mass_kg=0.08, mass_evidence="assumption",
               mass_note_zh="旋钮 0.08 kg（假设）", material="pa", geometry_evidence="assumption"))
    volume = V
    a.labware = {"type_id": a.asset_type_id, "family": "gas_cylinder", "labware_class": f"gas_cylinder_{tag}", "fits": [f"gas_cylinder_{tag}", "gas_cylinder"],
                 "maker": "Rama Cylinders (via PWENT) + Linde (regulator)", "model": f"Steel seamless cylinder {V*1000:.0f} L / 200 bar, ISO 9809-1; REDLINE C 200/2",
                 "catalogue_no": "regulator Art. 4461 (chrome-plated brass, 1-10 bar)",
                 "water_capacity_m3": volume, "service_pressure_pa": 200e5, "nominal_capacity_m3": volume,
                 "contents_note_zh": f"气体量由宿主账本记录：理想气体估计 n ≈ pV/RT（N2 在 200 bar、20 °C 约 {410*V/0.05:.0f} mol，考虑压缩因子约 {390*V/0.05:.0f} mol）",
                 "frames": {"handwheel": frame([0, 0, L + 0.107 + 0.012], "base_link", width_m=0.060, franka_ok=True, yaw_free=True, note_zh="瓶阀手轮（不在 Franka 可达范围）"),
                            "regulator_knob": frame([0, -0.095 - 0.040 - 0.015, L + 0.055], "base_link", rot=[0.70710678, 0, 0, 0.70710678],
                                                    note_zh="减压器旋钮，从前（−Y）接近"),
                            "outlet": frame([0.10, -0.095, L + 0.055], "base_link", rot=[0, 0, 0, 1], note_zh="减压器出口（6 mm 卡套）")},
                 "empty_mass_kg": round(M + 1.2 + 0.12 + 3.33 + 0.08, 3), "franka_grasp_ok": False, "stands_upright": True,
                 "storage_note_zh": "放在 DENIOS 气瓶架（cylinder_rack）并用链条固定；可燃气放可燃气体柜"}
    a.state_slots = {}
    a.contract_ports = {"gas_outlet": {"component_id": "regulator", "medium": "gas", "direction": "out", "frame": "frame:outlet",
                                       "meaning_zh": "减压后供气出口", "connection_constraints": ["瓶阀须打开；出口压力由减压器设定；流量与用量由宿主记账"]}}
    a.prop("body", "diameter_m", 2 * R, "m", "外径", "documented", [cyl_src])
    a.prop("body", "length_m", L, "m", "长度", "documented", [cyl_src])
    a.prop("body", "water_capacity_m3", volume, "m3", f"水容积 {V*1000:.0f} L", "documented", [cyl_src])
    a.prop("body", "working_pressure_pa", 200e5, "Pa", "工作压力 200 bar", "documented", [cyl_src])
    a.prop("regulator", "max_inlet_pressure_pa", 230e5, "Pa", "最大进口压力 230 bar", "documented", ["linde_c200_2"])
    a.limitations_zh = ["钢瓶直径、长度、容积、工作压力与质量按目录；肩部轮廓、瓶阀与手轮为假设；减压器质量与外形按 Linde 数据表，布局为近似。",
                        "瓶阀开度与减压器设定只作为关节角给出，压力/流量过程由宿主记账；ISO 11117 护帽在装减压器时取下，未建模。",
                        "不在固定 Franka 可达范围内（房间设施）。"]
    return a


BUILDERS = {
    "globe_weighing_boat_3620": build_weighing_boat,
    "bochem_spatula_3340": build_spatula,
    "gas_cylinder_50l_200bar": build_gas_cylinder,
    "gas_cylinder_10l_200bar": lambda: build_gas_cylinder("10l"),
}
