"""Mettler Toledo Quantos powder-dosing labware (2026-10-23).

* labware.mt_quantos_qh016_bnmw  - QH016-BNMW powder dosing head: geometry REUSED from the mt_quantos_q2 instrument asset's
  `dosing_head` part (mesh objects + collision proxies), re-based to the outlet tip; real model data from the Quantos operating
  instructions (p.102 technical data, p.107 part number, p.21 installation, p.29 filling, p.94 storage).
* labware.mt_quantos_head_container - the plastic container each head is delivered and stored in (manual p.27, p.94); used as the
  head's holder on the bench shelf (Mettler lists no head rack among the Quantos accessories, manual p.107-110).
* target vial: the Agilent 20 mL screw-top vial already in the catalog (real model, fits the Quantos sample-container limits of
  manual p.103); the instrument asset's `sample_container` part is an authored placeholder (its contract says the size is not a
  maker dimension), so it is not exported as a type.
Fit checks against the instrument asset (head_slot, container seat, powder outlet) are computed here and stored in
labware.instrument_fit; tests/test_labware_assets.py asserts them.
"""
from __future__ import annotations

import json
import math

import numpy as np
import trimesh

from labware_kit import (FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, ROOT, LabwareAsset, Link, Prim, frame, obb_overlap, revolve, ring_boxes,
                         top_down_frames, transform_prim)
from sources import SOURCES as _GLOBAL_SOURCES

QDIR = ROOT / "assets" / "basic40_batch02" / "t5_automation" / "mt_quantos_q2"
MAN = "references/manuals/mt_quantos_q2_xpe206dr/operating_manual__quantos_operating_instructions.pdf"
TID = "instrument.mt_quantos_q2_xpe206dr"

SRC = {
    "quantos_manual": {"kind": "manufacturer", "reference": f"METTLER TOLEDO Quantos operating instructions ({MAN}, local copy, page references only)",
                       "locator": "p.21 installing a dosing head (slide onto the dosing head support until it stops, press down into the holding pins); p.27 heads are "
                                  "supplied in a plastic container; p.29 preparing a head (unscrew the powder container, fill, screw back, insert); p.94 store heads in the "
                                  "delivered plastic container; p.102 QH016-BNMW: powder container thread DIN 18 mm, volume 16 mL, target range 0.1-250 mg, 250 dosings, "
                                  "typical offset 0.2 mg and sd 0.2 mg (CaCO3, 50 mg, 1 %); p.103 sample containers (QB1/QB3/QB7): height 31-178 mm, opening >= 6 mm, "
                                  "diameter up to 100 mm, weighing platform 78 x 73 mm; p.107 accessories: QH016-BNMW part no. 11141503 (no head rack listed p.107-110)",
                       "note": "Real-model data for the dosing head, its storage and the target container limits.", "licence": "facts"},
    "quantos_asset": {"kind": "code", "reference": "Chemistry-Sim assets/basic40_batch02/t5_automation/mt_quantos_q2 (geometry_manifest.json, collision_proxies.json, meshes/dosing_head.obj, ports head_slot / powder_outlet / container_seat)",
                      "locator": "components dosing_head, head_slot, balance_pan, sample_container; ports head_slot (0, -0.039, 0.245), powder_outlet (0, -0.031, 0.200), container_seat (0, -0.031, 0.069)",
                      "note": "Reused head geometry and the instrument interfaces for the fit check (authored geometry; the asset marks its part sizes as not maker CAD).", "licence": "project"},
    "quantos_assumption": {"kind": "assumption", "reference": "head mass, head-container size", "locator": "engineering value",
                           "note": "Head mass from the modelled volume x 1200 kg/m3 (plastic body, steel pin; Mettler gives no mass). The delivered plastic container's size is not "
                                   "published: an open PP cup O70 x 60 mm sized to hold the head upright with its upper 50 mm free for a top-down grasp.",
                           "licence": "n/a"},
    "robot_access": _GLOBAL_SOURCES["robot_access"],
}
for _k, _v in SRC.items():
    _GLOBAL_SOURCES.setdefault(_k, _v)

AMBER = (0.55, 0.30, 0.10, 0.85)
BLACK = (0.06, 0.07, 0.08, 1.0)
SILVER = (0.70, 0.74, 0.78, 1.0)
PP_NATURAL = (0.93, 0.93, 0.90, 0.8)
TIP = np.array([0.0, -0.031, 0.200])            # outlet tip in the instrument frame = head origin (powder_outlet port)


def _proxies(part: str) -> list[Prim]:
    cp = json.loads((QDIR / "collision_proxies.json").read_text(encoding="utf-8"))["components"][part]
    out = []
    for p in cp:
        if p["shape"] == "cylinder":
            out.append(Prim("cylinder", [p["radius_m"], p["length_m"]], list(p["origin_m"]), list(p["rpy_rad"])))
        elif p["shape"] == "box":
            out.append(Prim("box", list(p["dimensions_m"]), list(p["origin_m"]), list(p["rpy_rad"])))
    return out


def _shift(prims, d):
    return [Prim(p.shape, list(p.size_m), list(np.asarray(p.origin_m) + d), list(p.rpy_rad)) for p in prims]


def build_head() -> LabwareAsset:
    a = LabwareAsset("mt_quantos_qh016_bnmw", "labware.mt_quantos_qh016_bnmw", "METTLER TOLEDO Quantos 粉末加样头 QH016-BNMW（16 mL，11141503）",
                     "METTLER TOLEDO Quantos powder dosing head QH016-BNMW, 16 mL", "consumables", "labware.consumable.dosing_head", "passive",
                     description_zh="Quantos 粉末加样头 QH016-BNMW（零件号 11141503）：16 mL 粉末容器（DIN 18 mm 螺纹）、目标 0.1–250 mg、建议 250 次加样（手册 p.102、p.107）。"
                                    "几何直接取自 mt_quantos_q2 实例的 dosing_head 部件（4 个网格对象 + 4 个碰撞基元），原点移到出粉口下端。",
                     sources={k: _GLOBAL_SOURCES[k] for k in ("quantos_manual", "quantos_asset", "quantos_assumption", "robot_access")})
    sc = trimesh.load(QDIR / "meshes" / "dosing_head.obj", split_object=True, group_material=False, force="scene", process=False)
    geoms = sorted(sc.geometry.values(), key=lambda g: g.bounds[0][2])           # tip, outlet body, cap, powder bottle (by height)
    names_cols = [("outlet_tip", BLACK), ("outlet_body", SILVER), ("bottle_cap", BLACK), ("powder_bottle", AMBER)]
    visual = []
    for (nm, col), g in zip(names_cols, geoms):
        m = g.copy()
        m.apply_translation(-TIP)
        visual.append((m, col, {"name": nm} if visual else {}))
    col = _shift(_proxies("dosing_head"), -TIP)
    vol = sum(float(abs(g.volume)) for g in geoms)
    a.add(Link("body", "加样头（含粉末瓶）", "装粉并按质量目标出粉；RFID 记录物质与剩余量", "塑料机身 + 钢制计量销 + 琥珀色粉末瓶（倒装在头上）",
               visual=visual, collision=col, mass_kg=vol * 1200.0, mass_evidence="assumption",
               mass_note_zh=f"网格体积 {vol*1e6:.1f} cm3 × 1200 kg/m3（Mettler 未公布质量）", material="pp_pom_steel",
               geometry_evidence="documented", geometry_note_zh="网格与碰撞基元复用 mt_quantos_q2 的 dosing_head 部件（作者几何，非厂家 CAD）"))
    top = float(max(g.bounds[1][2] for g in geoms) - TIP[2])
    # top-down grasp on the inclined powder bottle (24 deg about X), fingers closing along X across its 24-26 mm
    z_g = top - HAND_ABOVE_TCP_M + 0.004
    ax = np.array([0.0, -math.sin(0.4188790204786391), math.cos(0.4188790204786391)])
    c0 = np.array([0.0, -0.056, 0.278]) - TIP
    y_g = float(c0[1] + ax[1] * (z_g - c0[2]) / ax[2])
    fr = top_down_frames(z_g, top, 0.026, yaw_free=False, xy=(0.0, y_g), contact_z=(z_g - 0.009, z_g + 0.009),
                         note_zh="俯抓倾斜的粉末瓶上部（宽 26 mm），手指沿 X 合拢；手掌碰撞面高于瓶顶")
    fr["grasp"]["rot_xyzw"] = [0.70710678, 0.70710678, 0.0, 0.0]
    # installed pose and insertion path (manual p.21: slide onto the support until it stops, then press down into the pins)
    seat = [float(v) for v in TIP]
    fr["instrument_seat"] = frame(seat, "instrument:" + TID, rot=[0, 0, 0, 1],
                                  note_zh="头原点（出粉口下端）装好后在 Quantos 坐标中的位置 = 实例 powder_outlet 端口")
    fr["insert_pre"] = frame([seat[0], seat[1] - 0.030, seat[2] + 0.003], "instrument:" + TID, rot=[0, 0, 0, 1],
                             note_zh="插入前：在支架前方 30 mm、高 3 mm；沿 +Y 滑入到底，再下压 3 mm 进定位销（手册 p.21）")
    a.labware = {"type_id": a.asset_type_id, "family": "dosing_head", "labware_class": "quantos_powder_head", "fits": ["quantos_head_slot", "quantos_head_container"],
                 "maker": "METTLER TOLEDO", "model": "Quantos powder dosing head QH016-BNMW (16 mL, DIN 18 mm container thread)", "catalogue_no": "11141503",
                 "nominal_capacity_m3": 1.6e-5, "nominal_capacity_evidence": "documented (manual p.102: volume 16 mL)",
                 "dosing": {"target_range_kg": [1e-7, 2.5e-4], "min_dose_kg": 1e-7, "recommended_dosings": 250,
                            "typical_offset_kg_at_50mg": 2e-7, "typical_sd_kg_at_50mg": 2e-7, "source_id": "quantos_manual"},
                 "rfid_zh": "头内 RFID 存物质名、批号、装填量与剩余量（手册 p.28）；运行层 head.set_content / head.select 对应",
                 "frames": fr, "empty_mass_kg": round(vol * 1200.0, 5), "franka_grasp_ok": True, "stands_upright": False,
                 "storage_note_zh": "存放在随头附带的塑料容器中（labware.mt_quantos_head_container，手册 p.94）；吸湿粉末（如 KBr）长期存放放干燥柜"}
    a.prop("body", "volume_m3", 1.6e-5, "m3", "粉末容器容积 16 mL", "documented", ["quantos_manual"])
    a.prop("body", "container_thread", "DIN 18 mm", "1", "粉末容器螺纹", "documented", ["quantos_manual"])
    a.prop("body", "authored_size_m", [0.026, 0.0589, round(top, 4)], "m", "复用网格外形（作者几何）", "assumption", ["quantos_asset"])
    a.contract_ports = {"powder_outlet": {"component_id": "body", "medium": "mechanical", "direction": "out", "frame": "frame:instrument_seat",
                                          "meaning_zh": "出粉口（装在 Quantos 上时位于样品容器正上方）",
                                          "connection_constraints": ["加样由 Quantos 运行层 dosing.* 完成；粉末量由宿主账本记录（头减少 = 容器增加）"]}}
    a.limitations_zh = ["几何复用 Quantos 实例的 dosing_head 作者网格与碰撞基元（非厂家 CAD）；容积、螺纹、加样范围与零件号按 Mettler 手册。",
                        "质量为按体积估计的假设；粉末质量由账本记录，不计入空头质量。",
                        "插入按手册是水平滑入支架再下压（不是竖直插入）；锁紧由仪器在开始加样或写头数据时完成（手册 p.22）。"]
    a.labware["instrument_fit"] = {TID: head_fit(col)}
    return a


def head_fit(col_local: list[Prim]) -> dict:
    """Head collision proxies at the installed pose vs the instrument's head_slot proxies; port inside the head; outlet over the seat."""
    T = trimesh.transformations.translation_matrix(TIP)
    head_w = [transform_prim(p, T) for p in col_local]
    slot = _proxies("head_slot")
    overlaps = [(i, j) for i, h in enumerate(head_w) for j, s in enumerate(slot) if obb_overlap(h, s, tol=1e-4)]
    # insertion path of the manual p.21 (slide on along +Y at +3 mm, then press down 3 mm) against the open support (instrument 1.2.2)
    path = [(0.0, -0.030 * k / 10, 0.003) for k in range(10, -1, -1)] + [(0.0, 0.0, 0.003 * k / 6) for k in range(5, -1, -1)]
    path_hits = [list(off) for off in path for h in col_local for s in slot
                 if obb_overlap(transform_prim(h, trimesh.transformations.translation_matrix(TIP + np.array(off))), s, tol=1e-4)]
    man = json.loads((QDIR / "geometry_manifest.json").read_text(encoding="utf-8"))
    ports = dict(man["ports"]) if isinstance(man["ports"], list) else man["ports"]
    hb = np.array(next(c for k, c in man["components"].items() if k == "dosing_head")["bbox_m"])
    port = np.array(ports["head_slot"]["position_m"])
    port_in = bool(np.all(port >= hb[0] - 0.002) and np.all(port <= hb[1] + 0.002))
    outlet = np.array(ports["powder_outlet"]["position_m"])
    seat = np.array(ports["container_seat"]["position_m"])
    head_names = {0: "powder_bottle", 1: "bottle_cap", 2: "outlet_body", 3: "outlet_tip"}
    interface_ok = port_in and abs(outlet[0] - seat[0]) < 1e-6 and abs(outlet[1] - seat[1]) < 1e-6
    return {"slot": "head_slot", "seat_pose_m": [float(v) for v in TIP], "insert_direction": [0.0, 1.0, 0.0], "press_down_m": 0.003,
            "head_slot_port_inside_head": port_in,
            "outlet_above_container_seat_xy": bool(abs(outlet[0] - seat[0]) < 1e-6 and abs(outlet[1] - seat[1]) < 1e-6),
            "ok": bool(interface_ok),
            "collision_free_against_instrument_proxies": not overlaps,
            "proxy_overlaps": [{"head": head_names.get(i, i), "instrument_head_slot_proxy_index": j} for i, j in overlaps],
            "insertion_path_clear": not path_hits, "insertion_path_hits_m": path_hits[:5],
            "note_zh": "安装位与仪器接口一致（head_slot 端口在头内、出粉口在样品座正上方）。仪器 1.2.2 起 head_slot 是开放式投料头支架（背板、上下叉臂、"
                       "四颗定位销；说明书 p.11、p.21）：装好的头与支架代理不相交，沿 +Y 滑入（高 3 mm）再下压 3 mm 的路径也不相交；头的出粉体落在下叉臂上。"
                       "仿真里仍按 touch + intent 把头运动学挂接到 seat_pose。",
            "method": "OBB separating-axis test of the head's collision proxies at the installed pose and along the manual's insertion path against the instrument's head_slot proxies"}


def build_head_container() -> LabwareAsset:
    a = LabwareAsset("mt_quantos_head_container", "labware.mt_quantos_head_container", "Quantos 加样头存放容器（随头附带的塑料容器，开口放置）",
                     "Quantos dosing-head storage container (supplied with each head)", "consumables", "labware.consumable.head_container", "passive",
                     description_zh="每个 Quantos 加样头随附的塑料容器（手册 p.27 运输/存放，p.94 防吸潮存放）。Mettler 附件表（p.107–110）没有加样头架，"
                                    "所以加样头放在各自容器里、容器放在台面阶梯架上（机器人俯抓可达）。容器尺寸未公布，按头的外形取假设。",
                     sources={k: _GLOBAL_SOURCES[k] for k in ("quantos_manual", "quantos_assumption", "robot_access")})
    R, H, t = 0.035, 0.060, 0.0015
    outer = [(0.0, 0.0), (R - 0.003, 0.0), (R, 0.003), (R, H)]
    inner = [(R - t, H), (R - t, t + 0.002), (R - t - 0.002, t), (0.0, t)]
    cup = revolve(outer + inner, sections=48)
    col = [Prim("cylinder", [R - 0.001, t], [0, 0, t / 2])] + ring_boxes(R, 0.002, R, H, 0.002, 16)
    a.add(Link("body", "存放容器", "竖直容纳一个加样头（出粉口朝下）", "PP 开口杯（盖在默认实验室里取下，便于机器人取放）",
               visual=[(cup, PP_NATURAL)], collision=col, mass_kg=abs(cup.volume) * 900.0, mass_evidence="assumption",
               mass_note_zh="建模体积 × PP 900 kg/m3", material="pp", geometry_evidence="assumption"))
    head_y = 0.0195                       # head origin offset so that the inclined bottle is centred in the cup
    a.ports = [{"name": "head", "kind": "holder", "part": "body", "position_m": [0.0, head_y, t], "insert_axis": [0.0, 0.0, -1.0], "capture_m": 0.01,
                "radius_m": 0.03, "moves_with": "body", "fits": ["quantos_powder_head"],
                "note_zh": "加样头原点（出粉口下端）放到杯底这里；头上部约 50 mm 露出杯口供俯抓"}]
    fr = top_down_frames(H * 0.6, H, 2 * R, yaw_free=True, contact_z=(H * 0.6 - 0.009, H * 0.6 + 0.009), note_zh="杯外径 70 mm（≤74 mm，可抓）")
    fr["head_seat"] = frame([0.0, head_y, t], "base_link", rot=[0, 0, 0, 1], note_zh="加样头在杯中的位置（头坐标原点）")
    a.labware = {"type_id": a.asset_type_id, "family": "head_container", "labware_class": "quantos_head_container", "fits": ["bottle", "jar"],
                 "maker": "METTLER TOLEDO (supplied with each dosing head)", "model": "Plastic container delivered with Quantos dosing heads (manual p.27, p.94)",
                 "catalogue_no": None, "frames": fr, "empty_mass_kg": round(abs(cup.volume) * 900.0, 5), "franka_grasp_ok": True, "stands_upright": True,
                 "holds": "labware.mt_quantos_qh016_bnmw", "storage_place_zh": "台面阶梯架 bench_shelf（T2 后层），每杯一个加样头"}
    a.prop("body", "size_m", [2 * R, H], "m", "外径 × 高（假设：厂家未公布）", "assumption", ["quantos_assumption"])
    a.limitations_zh = ["容器为 Mettler 随头附带的实物（手册 p.27/p.94），尺寸未公布：Ø70×60 mm 为按加样头外形取的假设。",
                        "实际容器带盖；默认实验室里盖已取下（机器人直接取头），吸湿粉末长期存放仍应盖好或放干燥柜。"]
    return a


def target_vial_with_fit():
    """The Agilent 20 mL screw-top vial (already built) + its fit check against the Quantos sample-container limits (manual p.103)."""
    import containers
    a = containers.build_screwcap(containers.vial_spec("20ml"))
    man = json.loads((QDIR / "geometry_manifest.json").read_text(encoding="utf-8"))
    ports = dict(man["ports"]) if isinstance(man["ports"], list) else man["ports"]
    seat_z = float(ports["container_seat"]["position_m"][2])
    outlet_z = float(ports["powder_outlet"]["position_m"][2])
    lw = a.labware
    h_open, h_capped = lw["outer"]["height_body_m"], lw["outer"]["height_with_cap_m"]
    opening = 2 * lw["interior"]["neck_radius_m"]
    od = lw["outer"]["diameter_m"]
    limits = {"height_m": [0.031, 0.178], "min_opening_m": 0.006, "max_diameter_m": 0.100, "platform_m": [0.078, 0.073]}
    ok = limits["height_m"][0] <= h_open <= limits["height_m"][1] and opening >= limits["min_opening_m"] and od <= limits["max_diameter_m"]
    lw.setdefault("instrument_fit", {})[TID] = {
        "slot": "container_seat (balance pan)", "limits_source": "quantos_manual p.103 (QB1/QB3/QB7 column)", "limits": limits,
        "vial_height_open_m": h_open, "vial_opening_m": round(opening, 5), "vial_diameter_m": od,
        "seat_z_m": seat_z, "outlet_z_m": outlet_z, "outlet_gap_open_vial_m": round(outlet_z - seat_z - h_open, 4),
        "note_zh": "开盖后上机；出粉口在瓶口正上方（实例 powder_outlet 与 container_seat 同 XY）；Quantos 用头高调节把出粉口降到瓶口上 0.5–1 mm（手册 p.23），实例未建该运动",
        "ok": bool(ok)}
    a.sources.setdefault("quantos_manual", _GLOBAL_SOURCES["quantos_manual"])
    a.sources.setdefault("quantos_asset", _GLOBAL_SOURCES["quantos_asset"])
    return a


BUILDERS = {
    "mt_quantos_qh016_bnmw": build_head,
    "mt_quantos_head_container": build_head_container,
    "agilent_hs_vial_20ml": target_vial_with_fit,
}
