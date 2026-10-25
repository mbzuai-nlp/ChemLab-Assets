"""SBS-footprint labware from the Opentrons labware definitions (Apache-2.0, local copies in references/labware/opentrons/):
Corning 96-well flat plate 360 uL, NEST 96 deep-well plate 2 mL, Opentrons Flex tip racks 50 / 200 / 1000 uL (tips included),
NEST 12-well reservoir 15 mL, and an SBS plate carrier with a robot handle (design on the ANSI/SLAS footprint).

Well positions, depths, diameters and volumes come straight from the definition JSON; the asset origin is the footprint centre
(Opentrons origin = front-left-bottom corner, so x_asset = x_def - X/2, y_asset = y_def - Y/2, z unchanged; row A at the back = +Y).
The 85.5 mm short side is wider than the Franka hand opens (80 mm): `sbs_grasp` (franka_ok false) is for wide plate grippers such as
the lab's Brooks PreciseFlex 400 plate gripper; the Franka moves SBS labware on the plate carrier.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import trimesh

from labware_kit import FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, ROOT, LabwareAsset, Link, Prim, frame, top_down_frames
from sources import pick

OT_DIR = ROOT / "references" / "labware" / "opentrons"
PS_CLEAR = (0.90, 0.93, 0.96, 0.55)
PP_NATURAL = (0.93, 0.93, 0.90, 0.85)
RACK_BLACK = (0.12, 0.12, 0.13, 1.0)
TIP_CLEAR = (0.92, 0.94, 0.96, 0.60)
ALU = (0.75, 0.77, 0.80, 1.0)
PS, PP, ALU_RHO = 1050.0, 900.0, 2700.0


def ot_def(name: str) -> dict:
    return json.loads((OT_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------ mesh helpers
def _tri(verts: list, faces: list, a, b, c):
    i = len(verts)
    verts += [a, b, c]
    faces.append([i, i + 1, i + 2])


def _quad(verts, faces, a, b, c, d):          # a-b-c-d counter-clockwise seen from the normal side
    _tri(verts, faces, a, b, c)
    _tri(verts, faces, a, c, d)


def _square_ring(cx, cy, hx, hy, n=16):
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        c, s = math.cos(t), math.sin(t)
        d = min(hx / abs(c) if abs(c) > 1e-12 else 1e9, hy / abs(s) if abs(s) > 1e-12 else 1e9)
        pts.append((cx + d * c, cy + d * s))
    return pts


def deck_with_wells(X, Y, Z, wells: list[dict], cells: tuple[float, float], border=None, n=16, skirt=True) -> trimesh.Trimesh:
    """Top deck at z=Z with one hole per well (circular or rectangular), well walls down to the well bottom, flat bottoms,
    the deck border out to the footprint edge and the outer skirt walls (open underneath, like a real plate)."""
    V, F = [], []
    px, py = cells
    xs = [w["x"] for w in wells]
    ys = [w["y"] for w in wells]
    gx0, gx1 = min(xs) - px / 2, max(xs) + px / 2
    gy0, gy1 = min(ys) - py / 2, max(ys) + py / 2
    for w in wells:
        cx, cy, zb = w["x"], w["y"], w["z"]
        if w["shape"] == "circular":
            r = w["d"] / 2
            sq = _square_ring(cx, cy, px / 2, py / 2, n)
            ci = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        else:
            ax, ay = w["xd"] / 2, w["yd"] / 2
            sq = _square_ring(cx, cy, px / 2, py / 2, n)
            ci = _square_ring(cx, cy, ax, ay, n)
        for k in range(n):
            k1 = (k + 1) % n
            s0, s1, c0, c1 = sq[k], sq[k1], ci[k], ci[k1]
            _quad(V, F, (*s0, Z), (*s1, Z), (*c1, Z), (*c0, Z))
            _quad(V, F, (*c0, zb), (*c0, Z), (*c1, Z), (*c1, zb))            # wall, normal towards the well axis
            _tri(V, F, (cx, cy, zb), (*c0, zb), (*c1, zb))                    # bottom, normal up
    # deck border (frame between the cell grid and the footprint edge)
    ex0, ex1, ey0, ey1 = -X / 2, X / 2, -Y / 2, Y / 2
    _quad(V, F, (ex0, ey0, Z), (ex1, ey0, Z), (gx1, gy0, Z), (gx0, gy0, Z))
    _quad(V, F, (ex1, ey0, Z), (ex1, ey1, Z), (gx1, gy1, Z), (gx1, gy0, Z))
    _quad(V, F, (ex1, ey1, Z), (ex0, ey1, Z), (gx0, gy1, Z), (gx1, gy1, Z))
    _quad(V, F, (ex0, ey1, Z), (ex0, ey0, Z), (gx0, gy0, Z), (gx0, gy1, Z))
    if skirt:
        _quad(V, F, (ex0, ey0, 0), (ex1, ey0, 0), (ex1, ey0, Z), (ex0, ey0, Z))
        _quad(V, F, (ex1, ey0, 0), (ex1, ey1, 0), (ex1, ey1, Z), (ex1, ey0, Z))
        _quad(V, F, (ex1, ey1, 0), (ex0, ey1, 0), (ex0, ey1, Z), (ex1, ey1, Z))
        _quad(V, F, (ex0, ey1, 0), (ex0, ey0, 0), (ex0, ey0, Z), (ex0, ey1, Z))
    m = trimesh.Trimesh(vertices=np.array(V), faces=np.array(F), process=True)
    return m


def wells_from_def(d: dict) -> list[dict]:
    X, Y = d["dimensions"]["xDimension"] / 1000, d["dimensions"]["yDimension"] / 1000
    out = []
    for name in [w for col in d["ordering"] for w in col]:
        w = d["wells"][name]
        e = {"name": name, "x": w["x"] / 1000 - X / 2, "y": w["y"] / 1000 - Y / 2, "z": w["z"] / 1000, "depth": w["depth"] / 1000,
             "shape": w["shape"], "volume_m3": w["totalLiquidVolume"] * 1e-9}
        if w["shape"] == "circular":
            e["d"] = w["diameter"] / 1000
        else:
            e["xd"], e["yd"] = w["xDimension"] / 1000, w["yDimension"] / 1000
        out.append(e)
    return out


def _sbs_common(a: LabwareAsset, d: dict, X, Y, Z, wells, grip_z, family, kind_zh, defname, src_id):
    a.labware.update({
        "type_id": a.asset_type_id, "family": family, "labware_class": "sbs_" + family, "fits": ["sbs", f"sbs_{family}"],
        "opentrons_load_name": defname, "maker": d["brand"]["brand"], "model": d["metadata"]["displayName"],
        "catalogue_no": ", ".join(d["brand"].get("brandId", [])[:4]) or None,
        "footprint_m": [X, Y], "height_m": Z,
        "wells": {w["name"]: {"pos_m": [round(w["x"], 5), round(w["y"], 5), round(w["z"], 5)], "depth_m": round(w["depth"], 5),
                              "shape": w["shape"], **({"diameter_m": round(w["d"], 5)} if w["shape"] == "circular" else {"size_m": [round(w["xd"], 5), round(w["yd"], 5)]}),
                              "volume_m3": w["volume_m3"]} for w in wells},
        "n_wells": len(wells), "well_volume_m3": wells[0]["volume_m3"],
        "nominal_capacity_m3": wells[0]["volume_m3"] * len(wells), "nominal_capacity_evidence": "documented (Opentrons definition totalLiquidVolume x wells)",
        "nominal_source_id": src_id, "stands_upright": True, "franka_grasp_ok": False,
    })
    g = frame([0, 0, grip_z], "base_link", width_m=Y, franka_ok=False, yaw_free=False, finger_axis=[0.0, 1.0, 0.0],
              note_zh=f"SBS 夹持：手指沿 Y 夹短边（{Y*1000:.2f} mm），高度 {grip_z*1000:.1f} mm；宽于 Franka 张开 80 mm，"
                      "供宽口板夹爪使用（如本实验室 Brooks PreciseFlex 400 的板夹爪）；Franka 用 sbs_plate_carrier 搬运")
    clear = Z + HAND_ABOVE_TCP_M + 0.05
    a.labware["frames"] = {"sbs_grasp": g, "grasp": dict(g), "pre_grasp": frame([0, 0, clear], "base_link"),
                           "place": frame([0, 0, grip_z], "base_link", note_zh="放置时 TCP（= sbs_grasp）"),
                           "pre_place": frame([0, 0, clear], "base_link"),
                           "a1": frame([round(wells[0]["x"], 5), round(wells[0]["y"], 5), Z], "base_link", rot=[0, 0, 0, 1], note_zh="A1 孔口中心")}
    a.prop("body", "footprint_m", [X, Y], "m", f"{kind_zh}外形（Opentrons 定义 / ANSI SLAS 1-2004）", "documented", [src_id, "ansi_slas_1_2004"])
    a.prop("body", "height_m", Z, "m", "高度（Opentrons 定义）", "documented", [src_id])
    a.prop("body", "well_volume_m3", wells[0]["volume_m3"], "m3", "单孔总液体体积（Opentrons totalLiquidVolume）", "documented", [src_id])
    a.contract_ports = {"wells": {"component_id": "body", "medium": "liquid", "direction": "bidirectional", "frame": "frame:a1",
                                  "meaning_zh": f"{len(wells)} 个孔（位置见 labware.wells）；移液由移液器/宿主记账",
                                  "connection_constraints": ["孔位坐标与体积来自 Opentrons 定义（Apache-2.0）"]}}


def _material_volume(X, Y, Z, wells, t_deck=0.0012, t_wall=0.0009, t_skirt=0.0012) -> float:
    v = X * Y * t_deck
    for w in wells:
        if w["shape"] == "circular":
            per, area = math.pi * w["d"], math.pi * w["d"] ** 2 / 4
        else:
            per, area = 2 * (w["xd"] + w["yd"]), w["xd"] * w["yd"]
        v += -area * t_deck + per * (Z - w["z"]) * t_wall + area * t_deck
    v += 2 * (X + Y) * Z * t_skirt
    return v


def build_plate(defname: str, slug: str, name_zh: str, src_id: str, family: str, rgba, material_rho, grip_z=None) -> LabwareAsset:
    d = ot_def(defname)
    X, Y, Z = (d["dimensions"][k] / 1000 for k in ("xDimension", "yDimension", "zDimension"))
    wells = wells_from_def(d)
    pitch = (abs(wells[1]["x"] - wells[0]["x"]) or 0.009, abs(wells[1]["y"] - wells[0]["y"]) or 0.009) if len(wells) > 1 else (X * 0.9, Y * 0.9)
    if family == "reservoir":
        pitch = (0.009, wells[0]["yd"] + 0.004)
    mesh = deck_with_wells(X, Y, Z, wells, pitch, n=16 if wells[0]["shape"] == "circular" else 8)
    src = pick(src_id, "ansi_slas_1_2004", "plate_ps_mass", "robot_access")
    a = LabwareAsset(slug, f"labware.{slug}", name_zh, d["metadata"]["displayName"], "sbs", f"labware.sbs.{family}", "passive",
                     description_zh=f"{name_zh}：按 Opentrons 定义 {defname}（Apache-2.0）建模，外形 {X*1000:.2f}×{Y*1000:.2f}×{Z*1000:.2f} mm，{len(wells)} 孔。", sources=src)
    vol = _material_volume(X, Y, Z, wells)
    a.add(Link("body", name_zh, "盛放样品/试剂；SBS 标准外形可放入仪器板位", f"{'PS' if material_rho == PS else 'PP'} 注塑件",
               visual=[(mesh, rgba)], collision=[Prim("box", [X, Y, Z], [0, 0, Z / 2])], mass_kg=vol * material_rho, mass_evidence="assumption",
               mass_note_zh=f"由壁厚 ~1 mm 的材料体积 {vol*1e6:.1f} cm3 × {material_rho:.0f} kg/m3 估算", material="ps" if material_rho == PS else "pp",
               geometry_evidence="documented", geometry_note_zh="外形与孔位/孔深/孔径来自 Opentrons 定义；壁厚为假设"))
    _sbs_common(a, d, X, Y, Z, wells, grip_z if grip_z is not None else min(Z / 2, 0.010), family, name_zh, defname, src_id)
    a.limitations_zh = ["外形、孔位、孔深、孔径/孔尺寸和单孔体积来自 Opentrons labware 定义（Apache-2.0）；壁厚与质量为估计。",
                        "碰撞体为实心盒（板作刚体搬运）；孔内液体只记账，不生成粒子。",
                        f"短边 {Y*1000:.1f} mm 宽于 Franka 张开 80 mm：Franka 用 labware.sbs_plate_carrier 搬运；sbs_grasp 供宽口板夹爪（PreciseFlex 400）。"]
    return a


def build_tiprack(volume_ul: int) -> LabwareAsset:
    defname = f"opentrons_flex_96_tiprack_{volume_ul}ul"
    src_id = f"ot_flex_tiprack_{volume_ul}"
    d = ot_def(defname)
    X, Y, Z = (d["dimensions"][k] / 1000 for k in ("xDimension", "yDimension", "zDimension"))
    wells = wells_from_def(d)
    tip_len = d["parameters"]["tipLength"] / 1000
    rack = deck_with_wells(X, Y, Z, [dict(w, z=Z - 0.004) for w in wells], (0.009, 0.009), n=12)
    tips = []
    r_top = wells[0]["d"] / 2 + 0.0008
    for w in wells:
        prof = [(0.0, Z - tip_len), (0.0004, Z - tip_len), (0.0012, Z - tip_len + 0.006), (r_top - 0.0012, Z - 0.012), (r_top - 0.0006, Z - 0.004),
                (r_top, Z - 0.004), (r_top, Z + 0.001), (0.0, Z + 0.001)]
        m = trimesh.creation.revolve(np.array(prof), sections=12)
        m.apply_translation([w["x"], w["y"], 0])
        tips.append(m)
    tips_mesh = trimesh.util.concatenate(tips)
    src = pick(src_id, "ansi_slas_1_2004", "plate_ps_mass", "robot_access")
    slug = f"opentrons_flex_tiprack_{volume_ul}ul"
    a = LabwareAsset(slug, f"labware.{slug}", f"Opentrons Flex 96 吸头盒 {volume_ul} µL（满盒）", d["metadata"]["displayName"], "sbs",
                     "labware.sbs.tiprack", "passive",
                     description_zh=f"Opentrons Flex 96 吸头盒 {volume_ul} µL（{defname}）：外形 {X*1000:.2f}×{Y*1000:.2f}×{Z*1000:.0f} mm，吸头长 {tip_len*1000:.2f} mm（含 96 支吸头）。",
                     sources=src)
    rack_vol = 2 * (X + Y) * Z * 0.0015 + X * Y * 0.002
    tip_vol = sum(abs(t.volume) for t in tips) * 0.25           # hollow tips: ~25 % of the solid envelope (wall ~0.5 mm)
    a.add(Link("body", "吸头盒（含吸头）", "存放 96 支一次性吸头；移液器从孔位取头", "PP 盒体 + PP 吸头",
               visual=[(rack, RACK_BLACK), (tips_mesh, TIP_CLEAR, {"name": "tips"})], collision=[Prim("box", [X, Y, Z], [0, 0, Z / 2])],
               mass_kg=rack_vol * PP + tip_vol * PP, mass_evidence="assumption", mass_note_zh="盒体壁厚 1.5 mm 与吸头壁 ~0.5 mm 的 PP 体积估算",
               material="pp", geometry_evidence="documented", geometry_note_zh="外形、孔位、吸头长度按 Opentrons 定义；吸头外形为近似"))
    _sbs_common(a, d, X, Y, Z, wells, d.get("gripHeightFromLabwareBottom", 23.9) / 1000, "tiprack", "吸头盒", defname, src_id)
    a.labware.update({"tip_length_m": tip_len, "tip_overlap_m": d["parameters"].get("tipOverlap", 0) / 1000, "is_tiprack": True,
                      "nominal_capacity_m3": None, "tip_volume_m3": volume_ul * 1e-9, "grip_force_n": d.get("gripForce")})
    a.labware["frames"]["sbs_grasp"]["note_zh"] += f"；Opentrons gripHeightFromLabwareBottom = {d.get('gripHeightFromLabwareBottom')} mm，gripForce {d.get('gripForce')} N"
    a.labware["frames"]["grasp"] = dict(a.labware["frames"]["sbs_grasp"])
    a.limitations_zh = ["外形、孔位、吸头长度/重叠、夹持高度来自 Opentrons 定义（Apache-2.0）；吸头外形与质量为近似。",
                        "吸头的取用（哪个孔还有吸头）由移液器运行层/宿主记录，几何中 96 支吸头始终显示。",
                        "短边宽于 Franka 张开 80 mm：Franka 用 labware.sbs_plate_carrier 搬运。"]
    return a


def build_carrier() -> LabwareAsset:
    src = pick("ansi_slas_1_2004", "thermo_75006449", "robot_access")
    src["carrier_design"] = {"kind": "assumption", "reference": "SBS plate carrier with a robot handle (project design)", "locator": "engineering value",
                             "note": "Pocket = ANSI/SLAS footprint + 0.5 mm clearance per side, 4 mm walls 6 mm high, 3 mm floor, anodised aluminium; handle post 30 x 30 mm, 130 mm tall on a 45 mm arm at +X so the Franka hand (palm 37 mm above the TCP) clears a full Flex tip rack (102 mm) on the carrier.",
                             "licence": "n/a"}
    a = LabwareAsset("sbs_plate_carrier", "labware.sbs_plate_carrier", "SBS 板托架（带机器人抓手柱）", "SBS plate carrier with robot handle (design)",
                     "sbs", "labware.sbs.carrier", "accessory",
                     description_zh="SBS 板托架：按 ANSI/SLAS 外形的托盘 + +X 端 30 mm 抓手柱，使 Franka（最大 80 mm）能搬运孔板/吸头盒/储液槽。"
                                    "未找到带 ≤74 mm 机器人抓手的目录托架；最接近的实物是 Thermo Scientific 75006449 带提手的微孔板托架（离心机附件）。", sources=src)
    X, Y = 0.12776, 0.08548
    cl, wall, h_wall, floor = 0.0005, 0.004, 0.006, 0.003
    ix, iy = X + 2 * cl, Y + 2 * cl
    ox, oy = ix + 2 * wall, iy + 2 * wall
    prims = [Prim("box", [ox, oy, floor], [0, 0, floor / 2]),
             Prim("box", [ox, wall, h_wall], [0, -(iy + wall) / 2, floor + h_wall / 2]), Prim("box", [ox, wall, h_wall], [0, (iy + wall) / 2, floor + h_wall / 2]),
             Prim("box", [wall, iy, h_wall], [-(ix + wall) / 2, 0, floor + h_wall / 2]), Prim("box", [wall, iy, h_wall], [(ix + wall) / 2, 0, floor + h_wall / 2])]
    arm_len, post, post_h = 0.045, 0.030, 0.130
    x_post = ox / 2 + arm_len - post / 2
    prims += [Prim("box", [arm_len, post, floor + h_wall], [ox / 2 + arm_len / 2, 0, (floor + h_wall) / 2]),
              Prim("box", [post, post, post_h], [x_post, 0, post_h / 2])]
    vol = sum(p.mesh().volume for p in prims)
    a.add(Link("body", "托架（托盘 + 抓手柱）", "承托 SBS 板件；机器人抓柱搬运", "阳极氧化铝",
               visual=[(trimesh.util.concatenate([p.mesh() for p in prims]), ALU)], collision=prims, mass_kg=vol * ALU_RHO, mass_evidence="assumption",
               mass_note_zh=f"铝件体积 {vol*1e6:.0f} cm3 × 2700 kg/m3", material="aluminium", geometry_evidence="assumption",
               geometry_note_zh="设计件：托盘内腔按 ANSI/SLAS 外形 + 0.5 mm 间隙"))
    zg = 0.110
    fr = top_down_frames(zg, post_h, post, yaw_free=False, xy=(x_post, 0.0), contact_z=(zg - 0.009, zg + 0.009),
                         note_zh="抓手柱（30×30 mm），手指沿 Y 合拢；手掌在 TCP 上方 37 mm，高于满载吸头盒（102 mm）")
    fr["plate_seat"] = frame([0, 0, floor], "base_link", rot=[0, 0, 0, 1], note_zh="SBS 板件底面中心放在这里（托盘内腔）")
    a.labware = {"type_id": a.asset_type_id, "family": "carrier", "labware_class": "sbs_carrier", "fits": ["sbs_carrier"],
                 "maker": "project design (closest real: Thermo Scientific 75006449)", "model": "SBS plate carrier with 30 mm handle post", "catalogue_no": None,
                 "frames": fr, "pocket_m": [ix, iy], "seat_z_m": floor, "accepts": ["sbs_plate", "sbs_tiprack", "sbs_reservoir"],
                 "franka_grasp_ok": True, "max_payload_height_m": 0.102,
                 "usage_zh": "Franka 抓 grasp（柱）→ 托架连同板件一起移动；在仪器板位处由宿主/场景把板件从托架转到板位（touch + intent），或使用宽口板夹爪。"}
    a.ports = [{"name": "plate_seat", "kind": "plate_slot", "part": "body", "position_m": [0.0, 0.0, floor], "insert_axis": [0.0, 0.0, -1.0],
                "capture_m": 0.01, "radius_m": 0.06, "moves_with": "body", "fits": ["sbs"], "note_zh": "SBS 板件底面中心"}]
    a.prop("body", "pocket_m", [ix, iy], "m", "托盘内腔（ANSI/SLAS 外形 + 0.5 mm）", "documented", ["ansi_slas_1_2004"])
    a.prop("body", "handle_post_m", [post, post, post_h], "m", "抓手柱尺寸（设计）", "assumption", ["carrier_design"])
    a.limitations_zh = ["设计件，不对应目录产品：托盘按 ANSI/SLAS 外形，抓手柱尺寸按 Franka 约束设计；最接近的实物（Thermo 75006449）是离心机用带提手托架。",
                        "托架外缘比 SBS 外形大 4.5 mm/边，不能直接放进仪器的 SBS 板位：板件从托架到板位的转移由宿主（touch + intent）或宽口板夹爪完成。"]
    return a


BUILDERS = {
    "corning_96_wellplate_360ul_flat": lambda: build_plate("corning_96_wellplate_360ul_flat", "corning_96_wellplate_360ul_flat",
                                                           "Corning 96 孔平底板 360 µL", "ot_corning_96_flat", "plate", PS_CLEAR, PS),
    "nest_96_wellplate_2ml_deep": lambda: build_plate("nest_96_wellplate_2ml_deep", "nest_96_wellplate_2ml_deep", "NEST 96 深孔板 2 mL",
                                                      "ot_nest_96_deep", "deepwell", PP_NATURAL, PP),
    "nest_12_reservoir_15ml": lambda: build_plate("nest_12_reservoir_15ml", "nest_12_reservoir_15ml", "NEST 12 槽储液槽 15 mL",
                                                  "ot_nest_12_reservoir", "reservoir", PP_NATURAL, PP),
    "opentrons_flex_tiprack_50ul": lambda: build_tiprack(50),
    "opentrons_flex_tiprack_200ul": lambda: build_tiprack(200),
    "opentrons_flex_tiprack_1000ul": lambda: build_tiprack(1000),
    "sbs_plate_carrier": build_carrier,
}
