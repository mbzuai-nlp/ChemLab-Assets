"""Bench racks (2026-10-23: a real-model bench rack for the 20 mL headspace vials, inventory type `vial_rack_20ml`).

* labware.mn_702503_vial_rack_23mm - MACHEREY-NAGEL vial rack 702503 (36 positions, alpha-numeric index, max. vial diameter 23.1 mm,
  323 x 91 x 30 mm, blue polypropylene, stackable when empty; the same 36-position 23.1 mm rack is sold as WHEATON/DWK 064088 and
  Restek 22643).  It holds the Agilent 20 mL headspace vial (O 22.75 mm, labware.agilent_hs_vial_20ml).
  The maker does not publish the hole pitch: the 3 x 12 grid is laid out evenly inside a 3 mm rim (26.42 x 28.33 mm, assumption);
  deck height, floor and stacking pins follow the product photo.
* Robot access (Franka Hand, top-down): fingers close along Y.  The finger sizes come from the franka_description collision mesh
  (fingertip 17.8 mm wide x 16.4 mm thick over its last 19 mm, finger base 21 x 26.2 mm from 24 mm above the tip; tip 8.9 mm below
  the TCP).  At 26.42 mm pitch the fingers do not fit between neighbours along the closing direction, so only rows A and C are robot
  slots (24) and row B stays empty (manual use only); the finger clearance to every occupied neighbour is computed here.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh

from labware_kit import FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, LabwareAsset, Link, Prim, frame
from sources import SOURCES as _GLOBAL_SOURCES

SRC = {
    "mn_702503": {"kind": "manufacturer",
                  "reference": "MACHEREY-NAGEL vial rack REF 702503 product page "
                               "(https://www.mn-net.com/Vial-racks-max.-diameter-23.1-mm-36-pos.-stackable-if-empty-702503)",
                  "locator": "Technical data: positions 36, alpha-numerical index, dimensions L x W x H 323 x 91 x 30 mm, polypropylene (PP), blue, "
                             "stackable (only when empty), maximal vial diameter 23 mm / for headspace vials with a diameter of 23.1 mm, 5 pieces per pack; "
                             "product photo 6506_702503 (3 rows x 12 holes, raised rim, stacking pins at the corners and mid sides)",
                  "note": "Real model of the bench rack for 20 mL headspace vials.", "licence": "facts"},
    "dwk_064088": {"kind": "manufacturer",
                   "reference": "DWK Life Sciences WHEATON 36-Position Rack for 23.1 mm Open ID, cat. 064088 "
                                "(https://www.thermofisher.com/order/catalog/product/kr/en/064088; Fisher 11571325)",
                   "locator": "36 positions, 23.1 mm open ID, for 6/10/20 mL headspace 23 mm OD vials; 12.6 x 3.5 x 1.12 in (320 x 89 x 28 mm)",
                   "note": "Same rack design from a second supplier (corroborates the footprint and the hole size).", "licence": "facts"},
    "restek_22643": {"kind": "manufacturer", "reference": "Restek vial storage rack cat. 22643 (https://www.restek.com/p/22643)",
                     "locator": "polypropylene rack for 6, 10 or 20 mL headspace vials, holds up to 36, autoclavable 121 C 20 min",
                     "note": "Same 36-position headspace-vial rack class.", "licence": "facts"},
    "franka_finger": {"kind": "code", "reference": "franka_description (Franka Emika, Apache-2.0): meshes/collision/finger.stl, hand.stl, panda_arm_hand.urdf "
                                                   "(copy shipped with the Isaac Sim URDF importer)",
                      "locator": "finger.stl: last 19 mm 17.8 mm wide (x) x 16.4 mm thick (closing axis); base part 21 x 26.2 mm from 24 mm above the tip; "
                                 "length 53.9 mm; panda_finger_joint 0..0.04 m; hand_tcp 0.1034 m -> fingertip end 8.9 mm below the TCP; "
                                 "hand.stl 63 x 204 x 92 mm, lowest point 37 mm above the TCP",
                      "note": "Finger and hand sizes for the clearance check.", "licence": "Apache-2.0"},
    "rack_assumption": {"kind": "assumption", "reference": "MN 702503 details not in the data sheet", "locator": "engineering value",
                        "note": "Hole pitch not published: 12 x 3 cells laid out evenly inside a 3 mm rim of the documented 323 x 91 mm footprint "
                                "(26.42 x 28.33 mm); deck top 22 mm (3 mm thick) 4 mm below the rim top (26 mm), 2 mm floor, six 3 x 4 mm stacking pins "
                                "on the rim (30 mm overall) after the product photo; mass = modelled PP volume x 905 kg/m3.",
                        "licence": "n/a"},
    "robot_access": _GLOBAL_SOURCES["robot_access"],
}
for _k, _v in SRC.items():
    _GLOBAL_SOURCES.setdefault(_k, _v)

PP_BLUE = (0.09, 0.20, 0.62, 1.0)
PP_RHO = 905.0
# Franka finger / hand (franka_description collision meshes)
FINGER_TIP = {"width": 0.0178, "thick": 0.0165, "z0": 0.0, "z1": 0.019}          # z above the fingertip end
FINGER_BASE = {"width": 0.021, "thick": 0.0262, "z0": 0.024, "z1": 0.0539}
TIP_BELOW_TCP = 0.0089
VIAL_D, VIAL_H = 0.02275, 0.0775            # Agilent 20 mL headspace vial (labware.agilent_hs_vial_20ml, with cap)
VIAL_GRASP_Z = 0.0445                        # its grasp frame above the vial bottom
OPEN_W = 0.031                               # finger opening for the approach (4.1 mm per side around the vial)


def _cell_grid(L=0.323, W=0.091, rim=0.003, nx=12, ny=3):
    px, py = (L - 2 * rim) / nx, (W - 2 * rim) / ny
    xs = [-(L - 2 * rim) / 2 + px * (i + 0.5) for i in range(nx)]
    ys = [-(W - 2 * rim) / 2 + py * (j + 0.5) for j in range(ny)]
    return px, py, xs, ys


def finger_clearance(target, occupied, w=OPEN_W, seat_z=0.002) -> dict:
    """Smallest XY gap between the Franka fingers (closing along Y, top-down) around the vial in `target` and the occupied neighbours."""
    tcp_z = seat_z + VIAL_GRASP_Z
    tip_z = tcp_z - TIP_BELOW_TCP
    v0, v1 = seat_z, seat_z + VIAL_H
    worst = (1.0, None)
    for part in (FINGER_TIP, FINGER_BASE):
        z0, z1 = tip_z + part["z0"], tip_z + part["z1"]
        if z1 < v0 or z0 > v1:
            continue
        for s in (-1, 1):
            y_in, y_out = target[1] + s * w / 2, target[1] + s * (w / 2 + part["thick"])
            ylo, yhi = min(y_in, y_out), max(y_in, y_out)
            xlo, xhi = target[0] - part["width"] / 2, target[0] + part["width"] / 2
            for n in occupied:
                if abs(n[0] - target[0]) < 1e-9 and abs(n[1] - target[1]) < 1e-9:
                    continue
                dx = max(xlo - n[0], 0.0, n[0] - xhi)
                dy = max(ylo - n[1], 0.0, n[1] - yhi)
                gap = math.hypot(dx, dy) - VIAL_D / 2
                if gap < worst[0]:
                    worst = (gap, [round(n[0], 5), round(n[1], 5)])
    return {"min_gap_m": round(worst[0], 5), "nearest_vial_m": worst[1], "hand_bottom_above_vial_tops_m": round(tcp_z + HAND_ABOVE_TCP_M - (seat_z + VIAL_H), 5)}


def build_vial_rack_23mm() -> LabwareAsset:
    L, W, H = 0.323, 0.091, 0.030
    rim, rim_top, deck_top, deck_t, floor_t, hole_d = 0.003, 0.026, 0.022, 0.003, 0.002, 0.0231
    px, py, xs, ys = _cell_grid(L, W, rim)
    rows = ["A", "B", "C"]
    a = LabwareAsset("mn_702503_vial_rack_23mm", "labware.mn_702503_vial_rack_23mm",
                     "MACHEREY-NAGEL 顶空瓶架 702503（36 位，孔径 23.1 mm，PP 蓝）", "MACHEREY-NAGEL vial rack 702503, 36 positions, max. vial O 23.1 mm",
                     "racks", "labware.rack.vial", "passive",
                     description_zh="MN 702503 顶空瓶架：36 位（3 行 × 12 列，字母数字索引），外形 323×91×30 mm，蓝色聚丙烯，空架可叠放；放 O23 mm 的 6/10/20 mL "
                                    "顶空瓶（Agilent 20 mL 顶空瓶 O22.75 mm）。同款 36 位 23.1 mm 架另有 WHEATON 064088、Restek 22643。"
                                    "孔距厂家未公布：在 3 mm 边框内均分（26.42×28.33 mm，假设）。机器人（Franka 俯抓、手指沿 Y 合拢）只用 A、C 两行 24 位，B 行留空。",
                     sources={k: _GLOBAL_SOURCES[k] for k in SRC})
    # deck with 36 holes (visual by boolean), rim, floor, pins
    deck = trimesh.creation.box([L - 2 * rim, W - 2 * rim, deck_t])
    deck.apply_translation([0, 0, deck_top - deck_t / 2])
    cutters = []
    for y in ys:
        for x in xs:
            c = trimesh.creation.cylinder(radius=hole_d / 2, height=deck_t * 3, sections=40)
            c.apply_translation([x, y, deck_top - deck_t / 2])
            ch = trimesh.creation.cone(radius=hole_d / 2 + 0.0012, height=0.0024, sections=40)   # 1.2 mm entry chamfer (photo: rounded hole edges)
            ch.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
            ch.apply_translation([x, y, deck_top + 0.0012])
            cutters += [c, ch]
    deck_v = deck.difference(trimesh.util.concatenate(cutters))
    walls = [Prim("box", [L, rim, rim_top], [0, -(W - rim) / 2, rim_top / 2]), Prim("box", [L, rim, rim_top], [0, (W - rim) / 2, rim_top / 2]),
             Prim("box", [rim, W - 2 * rim, rim_top], [-(L - rim) / 2, 0, rim_top / 2]), Prim("box", [rim, W - 2 * rim, rim_top], [(L - rim) / 2, 0, rim_top / 2])]
    floor = Prim("box", [L - 2 * rim, W - 2 * rim, floor_t], [0, 0, floor_t / 2])
    pin_xy = [(sx * (L / 2 - 0.006), sy * (W / 2 - 0.0015 - 0.0)) for sx in (-1, 1) for sy in (-1, 1)] + [(0.0, -(W / 2 - 0.0015)), (0.0, W / 2 - 0.0015)]
    pins = [Prim("cylinder", [0.0015, H - rim_top], [x, y, rim_top + (H - rim_top) / 2]) for x, y in pin_xy]
    # deck collision: border strips + strips between the holes (square openings of the hole diameter)
    deck_c = []
    gx, gy = px - hole_d, py - hole_d
    zc = deck_top - deck_t / 2
    for i in range(len(xs) + 1):                                       # strips between columns (along Y)
        x = (xs[0] - px / 2) + i * px
        wx = gx / 2 if i in (0, len(xs)) else gx
        cx = x + (wx / 2 if i == 0 else -wx / 2 if i == len(xs) else 0.0)
        deck_c.append(Prim("box", [wx, W - 2 * rim, deck_t], [cx, 0.0, zc]))
    for j in range(len(ys) + 1):                                       # strips between rows (along X)
        y = (ys[0] - py / 2) + j * py
        wy = gy / 2 if j in (0, len(ys)) else gy
        cy = y + (wy / 2 if j == 0 else -wy / 2 if j == len(ys) else 0.0)
        deck_c.append(Prim("box", [L - 2 * rim, wy, deck_t], [0.0, cy, zc]))
    solid = walls + [floor] + pins
    vis = [(deck_v, PP_BLUE)] + [(trimesh.util.concatenate([p.mesh() for p in solid]), PP_BLUE, {"name": "frame"})]
    vol = float(deck_v.volume) + sum(p.mesh().volume for p in solid)
    a.add(Link("body", "瓶架（边框、孔板、底板）", "竖直承放 36 个 O23 mm 顶空瓶；空架可叠放",
               "注塑聚丙烯托盘：3 行 × 12 列孔（O23.1 mm），孔板低于边框 4 mm，四角与长边中点有叠放销",
               visual=vis, collision=walls + [floor] + deck_c + pins, mass_kg=vol * PP_RHO, mass_evidence="assumption",
               mass_note_zh=f"建模 PP 体积 {vol * 1e6:.0f} cm3 × 905 kg/m3（厂家未公布质量）", material="pp", geometry_evidence="documented",
               geometry_note_zh="外形 323×91×30 mm、36 位、孔径 23.1 mm 按 MN 数据；孔距、孔板高度、底板与叠放销为假设/照片估计"))
    # robot slots: rows A and C (fingers close along Y); row B manual only
    robot_rows = ["A", "C"]
    occupied = [(x, ys[rows.index(r)]) for r in robot_rows for x in xs]
    clear = [finger_clearance((x, ys[rows.index(r)]), occupied) for r in robot_rows for x in xs]
    worst = min(clear, key=lambda c: c["min_gap_m"])
    clear_b = finger_clearance((xs[5], ys[0]), [(x, y) for y in ys for x in xs])           # if row B were filled too
    fr = {}
    for r in robot_rows:
        y = ys[rows.index(r)]
        for i, x in enumerate(xs):
            name = f"{r}{i + 1:02d}"
            seat = [x, y, floor_t]
            tcp = [x, y, floor_t + VIAL_GRASP_Z]
            a.ports.append({"name": name, "place_id": "vial_rack_20ml", "part": "body", "kind": "storage", "position_m": [round(v, 5) for v in seat],
                            "insert_axis": [0.0, 0.0, -1.0], "capture_m": 0.01, "radius_m": round(hole_d / 2, 5), "moves_with": "body",
                            "headroom_m": None, "required_free_above_vial_top_m": 0.25, "fits": ["headspace_vial_20ml", "vial"],
                            "access": ["franka_top_down"],
                            "vial_grasp_tcp_m": [round(v, 5) for v in tcp], "vial_grasp_rot_xyzw": [1.0, 0.0, 0.0, 0.0], "finger_axis": [0.0, 1.0, 0.0],
                            "approach_open_width_m": OPEN_W,
                            "note_zh": "顶空瓶瓶底中心（孔底）；俯抓 TCP = 瓶 grasp（瓶底上 44.5 mm），手指沿 Y 合拢、张开 31 mm；架上方须留 ≥0.25 m 空间"})
            fr[f"slot_{name}"] = frame(seat, "base_link", rot=[0.0, 0.0, 0.0, 1.0], note_zh=f"{name} 位：瓶底中心")
    a.labware = {"type_id": a.asset_type_id, "family": "rack", "labware_class": "vial_rack", "fits": ["bench"],
                 "maker": "MACHEREY-NAGEL", "model": "Vial rack, max. diameter 23.1 mm, 36 positions, stackable if empty (blue PP)", "catalogue_no": "702503",
                 "also_sold_as": ["DWK WHEATON 064088", "Restek 22643 (36-position rack for 6/10/20 mL headspace vials)"],
                 "holds": "labware.agilent_hs_vial_20ml", "positions_total": 36, "grid": {"rows": rows, "columns": 12, "pitch_x_m": round(px, 5), "pitch_y_m": round(py, 5),
                                                                                         "hole_diameter_m": hole_d, "pitch_evidence": "assumption (not published)"},
                 "frames": fr, "seat_z_m": floor_t, "deck_top_m": deck_top, "franka_grasp_ok": False,
                 "franka_blocked_reason": "91 mm wide (> 74 mm grip): the rack stays on the bench; the robot moves the vials",
                 "storage": {"place_ids": ["vial_rack_20ml"], "min_pitch_m": math.floor(px * 1e5) / 1e5 - 1e-5, "expected_positions": [p["name"] for p in a.ports],
                             "robot_rows": robot_rows, "manual_only_rows": ["B"], "required_free_above_vial_top_m": 0.25,
                             "note_zh": "A、C 两行 24 位给机器人；B 行留空（手指沿 Y 合拢时会碰到 B 行的瓶）"},
                 "robot_access": {"gripper": "Franka Hand", "approach": "top-down", "finger_axis": [0.0, 1.0, 0.0], "approach_open_width_m": OPEN_W,
                                  "max_grasp_width_m": FRANKA_MAX_GRASP_M, "vial_diameter_m": VIAL_D, "vial_height_with_cap_m": VIAL_H,
                                  "finger_tip_m": FINGER_TIP, "finger_base_m": FINGER_BASE, "tip_below_tcp_m": TIP_BELOW_TCP,
                                  "min_finger_gap_rows_A_C_m": worst["min_gap_m"], "nearest_vial_m": worst["nearest_vial_m"],
                                  "hand_bottom_above_vial_tops_m": worst["hand_bottom_above_vial_tops_m"],
                                  "min_finger_gap_if_row_B_filled_m": clear_b["min_gap_m"],
                                  "fingertip_end_above_deck_m": round(floor_t + VIAL_GRASP_Z - TIP_BELOW_TCP - deck_top, 5),
                                  "free_space_above_vial_tops_m": 0.25,
                                  "note_zh": "手指尺寸取 franka_description 碰撞网格；A、C 行全部装满时，手指与最近的瓶最小间隙见 min_finger_gap_rows_A_C_m；"
                                             "若 B 行也放瓶，手指会压到 B 行（间隙为负）。手掌底面高于瓶盖顶。架上方需 ≥0.25 m 空间（放在台面或开放层板上）。",
                                  "source_ids": ["franka_finger", "robot_access"]}}
    a.prop("body", "size_m", [L, W, H], "m", "外形 L×W×H（MN 702503）", "documented", ["mn_702503"])
    a.prop("body", "positions", 36, "1", "孔位数（3 行 × 12 列，字母数字索引）", "documented", ["mn_702503"])
    a.prop("body", "hole_diameter_m", hole_d, "m", "孔径（最大瓶径 23.1 mm）", "documented", ["mn_702503", "dwk_064088"])
    a.prop("body", "pitch_m", [round(px, 5), round(py, 5)], "m", "孔距 X×Y：厂家未公布，按外形在 3 mm 边框内均分", "assumption", ["rack_assumption"])
    a.prop("body", "deck_top_m", deck_top, "m", "孔板顶面高（照片估计：低于边框 4 mm）", "photo_estimate", ["mn_702503", "rack_assumption"])
    a.prop("body", "material", "polypropylene, blue", "1", "材料与颜色", "documented", ["mn_702503"])
    a.limitations_zh = ["孔距、孔板高度、底板和叠放销尺寸厂家未公布：孔距按外形均分（26.42×28.33 mm），其余按产品照片估计。",
                        "孔径 23.1 mm 与瓶径 22.75 mm 只差 0.35 mm：仿真里放瓶按 touch + intent 吸附到槽位（capture 10 mm），不靠接触求解插入。",
                        "机器人只用 A、C 两行（24 位）；B 行留空，否则手指沿 Y 合拢时会碰到 B 行的瓶。架子本身 91 mm 宽，不由机器人搬运。"]
    return a


BUILDERS = {
    "mn_702503_vial_rack_23mm": build_vial_rack_23mm,
}
