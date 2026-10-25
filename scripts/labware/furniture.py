"""Storage furniture for the default lab: bench step shelf, Secador desiccator cabinet, DURAN glass desiccator, asecos safety
cabinets (flammables Q90, acids/bases SL-CLASSIC, toxics CS-CLASSIC), Liebherr lab refrigerator, DENIOS gas-cylinder stands.

Every piece follows a real product (maker, model, catalogue number, data sheet in sources.py); where the real product does not
give a value it is an assumption with a reason.  Panels are boxes (visual = collision).  Doors are revolute links with:
hinge axis + origin in the asset frame, limits, a `handle` frame (pull point, TCP approach along the door normal), mass.
Storage positions are "ports" of kind `storage` (same format as the expansion geometry manifests) named like the default-lab inventory's places.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh
from trimesh.transformations import quaternion_from_matrix

from labware_kit import FRANKA_MAX_GRASP_M, LabwareAsset, Link, Prim, frame, revolve, ring_boxes
from sources import pick

STEEL = 7850.0
PVC = 1400.0
COPOLYESTER = 1200.0
GLASS33 = 2230.0

RAL7016 = (0.22, 0.24, 0.27, 1.0)      # anthracite grey
RAL7035 = (0.80, 0.81, 0.79, 1.0)      # light grey
RAL1004 = (0.89, 0.66, 0.05, 1.0)      # golden yellow (flammables warning stripe / not used for Q90 body)
LAB_WHITE = (0.94, 0.94, 0.92, 1.0)
PVC_GREY = (0.62, 0.64, 0.66, 1.0)
SECADOR_CLEAR = (0.85, 0.92, 0.95, 0.40)
HANDLE_BLACK = (0.10, 0.10, 0.11, 1.0)
SHELF_GREY = (0.70, 0.71, 0.72, 1.0)
GALV = (0.72, 0.74, 0.76, 1.0)
DENIOS_BLUE = (0.12, 0.30, 0.60, 1.0)


def prims_mesh(prims: list[Prim]) -> trimesh.Trimesh:
    return trimesh.util.concatenate([p.mesh() for p in prims])


def prims_volume(prims: list[Prim]) -> float:
    return float(sum(p.mesh().volume for p in prims))


def box(cx, cy, cz, sx, sy, sz) -> Prim:
    return Prim("box", [sx, sy, sz], [cx, cy, cz])


def quat_from_axes(x_axis, y_axis, z_axis) -> list[float]:
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2] = x_axis, y_axis, z_axis
    w, x, y, z = quaternion_from_matrix(M)
    return [round(float(x), 6), round(float(y), 6), round(float(z), 6), round(float(w), 6)]


# TCP for pulling a vertical bar handle on a front (-Y facing) door: approach +Y (into the door), fingers close along X.
PULL_FRONT = quat_from_axes([0, 0, 1], [1, 0, 0], [0, 1, 0])
# grasping a knob from above (desiccator lid knob)
TOP_DOWN = [1.0, 0.0, 0.0, 0.0]


def add_slots(a: LabwareAsset, place_id: str, prefix: str, xs, ys, z: float, link: str, part: str, headroom: float, fits,
              access, pitch_note: str, start_index: int = 1, radius: float | None = None):
    k = start_index
    for y in ys:
        for x in xs:
            a.ports.append({"name": f"{prefix}-{k:02d}", "place_id": place_id, "part": part, "kind": "storage",
                            "position_m": [round(x, 5), round(y, 5), round(z, 5)], "insert_axis": [0.0, 0.0, -1.0],
                            "capture_m": 0.03, "radius_m": round(radius if radius else 0.04, 4), "moves_with": link,
                            "headroom_m": round(headroom, 4), "fits": list(fits), "access": list(access),
                            "note_zh": pitch_note})
            k += 1


def door_link(a: LabwareAsset, name: str, name_zh: str, hinge_x: float, hinge_y: float, z0: float, width: float, height: float,
              thick: float, hinge_side: str, open_rad: float, mass: float, mass_note: str, rgba, handle_z: float,
              handle_inset: float, limit_evidence: str, limit_source: list[str], parent: str = "carcass",
              handle_len: float = 0.20, handle_offset: float = 0.035, door_sources=()) -> dict:
    """A front door (closed in the plane y = hinge_y .. hinge_y - thick) hinged on a vertical axis at (hinge_x, hinge_y).

    hinge_side "left": the leaf extends to +X from the hinge, opens with +rotation about -Z; "right": extends to -X, about +Z.
    """
    sgn = 1.0 if hinge_side == "left" else -1.0
    axis = [0.0, 0.0, -1.0] if hinge_side == "left" else [0.0, 0.0, 1.0]
    # leaf in the door link frame (origin on the hinge line at the bottom of the leaf, front face at y = -thick)
    leaf = box(sgn * width / 2, -thick / 2, height / 2, width, thick, height)
    hx_local = sgn * (width - handle_inset)
    bar = Prim("cylinder", [0.008, handle_len], [hx_local, -thick - handle_offset, handle_z - z0])
    posts = [box(hx_local, -thick - handle_offset / 2, handle_z - z0 + s * (handle_len / 2 - 0.01), 0.012, handle_offset, 0.012) for s in (-1, 1)]
    vis_leaf = leaf.mesh()
    vis_h = prims_mesh([bar] + posts)
    link = a.add(Link(name, name_zh, "关闭/打开存放空间；外侧竖直拉手", "被动门扇：绕竖直铰链轴转动，资产无执行器",
                      parent=parent, origin_m=[hinge_x, hinge_y, z0],
                      joint={"type": "revolute", "axis": axis, "limits": [0.0, round(open_rad, 6)], "evidence": limit_evidence,
                             "source_ids": list(limit_source),
                             "note_zh": f"正方向 = 门向外（−Y）打开；铰链在{'左' if hinge_side == 'left' else '右'}侧；最大 {math.degrees(open_rad):.1f}°"},
                      visual=[(vis_leaf, rgba), (vis_h, HANDLE_BLACK, {"name": "handle"})], collision=[leaf, bar] + posts,
                      mass_kg=mass, mass_evidence="assumption", mass_note_zh=mass_note, geometry_evidence="documented"))
    handle_asset = [hinge_x + hx_local, hinge_y - thick - handle_offset, handle_z]
    a.labware.setdefault("doors", {})[name] = {
        "link": name, "joint": f"{name}_joint", "hinge_axis": axis, "hinge_origin_m": [round(hinge_x, 5), round(hinge_y, 5), round(z0, 5)],
        "hinge_side": hinge_side, "limits": [0.0, round(open_rad, 6)], "open_direction_zh": "门扇自由边向 −Y（操作者方向）转出",
        "leaf_size_m": [round(width, 5), round(thick, 5), round(height, 5)], "mass_kg": round(mass, 3), "mass_note_zh": mass_note,
        "handle": {"pos_m": [round(v, 5) for v in handle_asset], "rot_xyzw": PULL_FRONT, "approach_axis": [0.0, 1.0, 0.0],
                   "finger_axis": [1.0, 0.0, 0.0], "bar_diameter_m": 0.016, "bar_length_m": handle_len, "franka_ok": True,
                   "note_zh": "拉手中点（关门状态，资产坐标）；TCP 沿门法线 +Y 接近，手指沿 X 合拢夹住竖杆；开门 = 夹住后绕铰链轴沿圆弧向 −Y 拉"},
        "handle_local": {"link": name, "pos_m": [round(hx_local, 5), round(-thick - handle_offset, 5), round(handle_z - z0, 5)]},
        "radius_handle_to_hinge_m": round(abs(hx_local), 5),
    }
    a.labware.setdefault("frames", {})[f"handle_{name}"] = frame(handle_asset, "base_link", rot=PULL_FRONT,
                                                                 approach_axis=[0.0, 1.0, 0.0], note_zh=f"{name_zh}拉手（关门位）")
    return a.labware["doors"][name]


def _storage(a: LabwareAsset, place_ids, min_pitch, headroom, positions, maker, model, catalogue_no, notes):
    a.labware.update({"type_id": a.asset_type_id, "family": "storage_furniture", "maker": maker, "model": model, "catalogue_no": catalogue_no,
                      "storage": {"place_ids": list(place_ids), "min_pitch_m": min_pitch, "headroom_m": headroom,
                                  "expected_positions": positions, "note_zh": notes}})


# ===================================================================================================== bench step shelf
def build_bench_shelf() -> LabwareAsset:
    src = pick("trippnt_step_shelf", "inventory_places", "robot_access")
    a = LabwareAsset("trippnt_step_shelf_2tier", "furniture.trippnt_step_shelf_2tier", "台面阶梯试剂架（两层 × 5 位，TrippNT 24 in PVC 阶梯架 + 前托盘）",
                     "Bench reagent step shelf, 2 tiers x 5 (TrippNT 24 in PVC step shelf + front tray)", "furniture", "furniture.storage.bench_shelf", "passive",
                     description_zh="机器人可达的台面试剂架：后层是 TrippNT 24 in 无底 PVC 阶梯架（宽 610 mm、高 254 mm、深 229 mm、四周 25 mm 挡边），"
                                    "前层是同宽的低托盘；后层在前层之后而不在其上方，顶部敞开，两层各 5 个位置、间距 0.12 m。",
                     sources=src)
    W, D_top, H_top, lip, t = 0.610, 0.229, 0.254, 0.025, 0.006
    D_front = 0.150
    y_front0 = -(D_top + D_front) / 2
    y_step0 = y_front0 + D_front
    y_back = y_step0 + D_top
    prims = []
    # front tray: plate + lips (front, left, right)
    prims.append(box(0, y_front0 + D_front / 2, t / 2, W, D_front, t))
    prims.append(box(0, y_front0 + t / 2, lip / 2, W, t, lip))
    for sx in (-1, 1):
        prims.append(box(sx * (W / 2 - t / 2), y_front0 + D_front / 2, lip / 2, t, D_front, lip))
    # step: top plate at H_top, two side panels (bottomless), front fascia, back panel with a cord cut-out (open lower 60 mm)
    prims.append(box(0, y_step0 + D_top / 2, H_top - t / 2, W, D_top, t))
    for sx in (-1, 1):
        prims.append(box(sx * (W / 2 - t / 2), y_step0 + D_top / 2, (H_top - t) / 2, t, D_top, H_top - t))
    prims.append(box(0, y_step0 + t / 2, (H_top - t) / 2, W - 2 * t, t, H_top - t))
    prims.append(box(0, y_back - t / 2, 0.060 + (H_top - t - 0.060) / 2, W - 2 * t, t, H_top - t - 0.060))
    # 1 in lip around the top
    prims.append(box(0, y_step0 + t / 2, H_top + lip / 2, W, t, lip))
    prims.append(box(0, y_back - t / 2, H_top + lip / 2, W, t, lip))
    for sx in (-1, 1):
        prims.append(box(sx * (W / 2 - t / 2), y_step0 + D_top / 2, H_top + lip / 2, t, D_top, lip))
    mass = prims_volume(prims) * PVC
    a.add(Link("shelf", "阶梯架（前托盘 + 后层台阶）", "存放试剂瓶；两层都从正上方可达", "PVC 板件；前层台面 z=0.006 m，后层台面 z=0.254 m",
               visual=[(prims_mesh(prims), PVC_GREY)], collision=prims, mass_kg=mass, mass_evidence="assumption",
               mass_note_zh=f"PVC 板体积 × {PVC:.0f} kg/m3", material="pvc", geometry_evidence="documented",
               geometry_note_zh="后层宽/高/深与 25 mm 挡边按 TrippNT/Avantor 目录；前托盘（深 150 mm）为本资产补充"))
    xs = [-0.24, -0.12, 0.0, 0.12, 0.24]
    common = dict(link="shelf", fits=("bottle", "jar", "gl45_bottle", "powder_jar", "vial_rack"), access=[],
                  pitch_note="0.12 m 间距；俯视无遮挡（机器人约束：≥11 cm、瓶顶上方 ≥25 cm 空间）")
    add_slots(a, "bench_shelf", "T1", xs, [y_front0 + D_front / 2 + 0.005], t, part="front_tray", headroom=1.0, radius=0.055, **common)
    add_slots(a, "bench_shelf", "T2", xs, [y_step0 + D_top / 2], H_top, part="step_top", headroom=1.0, radius=0.055, **common)
    _storage(a, ["bench_shelf"], 0.11, 0.25 + 0.23, [f"T{t_}-{k:02d}" for t_ in (1, 2) for k in range(1, 6)],
             "TrippNT (distributed by Weber Scientific / Avantor)", "Step shelf, bottomless, PVC, 24 in + matching front tray (project addition)",
             "Weber TNT24W10-8 / Avantor 11376-388 (supplier no. 50123)",
             "两层 × 5 位；T1 前层（台面 z=0.006），T2 后层（z=0.254）；顶部敞开，headroom_m=1.0 表示上方无遮挡（仅受房间限制）")
    a.labware["tiers"] = {"T1": {"surface_z_m": t, "y_m": round(y_front0 + D_front / 2, 4), "depth_m": D_front},
                          "T2": {"surface_z_m": H_top, "y_m": round(y_step0 + D_top / 2, 4), "depth_m": D_top}}
    a.labware["frames"] = {"place_reference": frame([0, 0, 0], "base_link", rot=[0, 0, 0, 1], note_zh="资产原点：占地中心，台面高 z=0")}
    a.prop("shelf", "width_m", W, "m", "宽 24 in", "documented", ["trippnt_step_shelf"])
    a.prop("shelf", "step_height_m", H_top, "m", "后层台面高 10 in", "documented", ["trippnt_step_shelf"])
    a.prop("shelf", "step_depth_m", D_top, "m", "后层深 9 in", "documented", ["trippnt_step_shelf"])
    a.prop("shelf", "lip_height_m", lip, "m", "挡边 1 in", "documented", ["trippnt_step_shelf"])
    a.prop("shelf", "front_tray_depth_m", D_front, "m", "前托盘深度（本资产补充，容纳 1 L GL45 瓶 Ø101 mm）", "assumption", ["robot_access"])
    a.limitations_zh = [
        "后层按 TrippNT 24 in 无底 PVC 阶梯架（宽 610、高 254、深 229 mm，25 mm 挡边）；产品本身是单层台阶，前层托盘是为“两层同属一个资产、"
        "上层在下层之后”而补的同宽 PVC 托盘（深 150 mm，非目录件）。",
        "目录各渠道对深/高的标注不一致（Weber 24×10×8 in，Avantor 宽24/高10/深9 in）；采用 Avantor 的分项标注。",
        "两层各 5 位、间距 0.12 m 按机器人约束（≥0.11 m、俯视无遮挡）排布；挡边 25 mm，放瓶需抬过挡边。",
    ]
    return a


# ===================================================================================================== Secador cabinet
def build_secador() -> LabwareAsset:
    src = {"belart_secador_2": {"kind": "manufacturer",
                                "reference": "SP Bel-Art F42072-1000 Secador 2.0 vertical desiccator cabinet, clear, 2 shelves (https://www.belart.com/bel-art-f42072-1000-secador-clear-2-0-vertical-desiccator-cabinet-2-shelves-13-4-x-16-3-x-12-4.html); product family page https://belart.com/products/dry-storage/standard-desiccators/secador-1-0-2-0-3-0-4-0-vertical-desiccator-cabinets/; NIST listing https://www.nist.gov/node/412726",
                                "locator": "34.1 x 41.4 x 31.4 cm (13.4 x 16.3 x 12.4 in: depth x height x width), internal volume 1.2 ft3 (NIST: 33.1 L), 2 perforated shelves, desiccant space under the bottom shelf, side-swinging door opening to the left, door seal + tab latches, Durastar copolyester",
                                "note": "Benchtop desiccator cabinet (the default lab's `desiccator` place). accessed 2026-10-22.",
                                "licence": "facts"},
           **pick("inventory_places", "robot_access")}
    src["secador_assumption"] = {"kind": "assumption", "reference": "Secador wall / door thickness, shelf heights, door opening angle",
                                 "locator": "engineering value",
                                 "note": "Walls 12 mm and door 10 mm copolyester (gives ~34 L inside, matching the 1.2 ft3 listing); shelves split the inside height evenly above a 60 mm desiccant space; door opens 100 deg (not listed); copolyester density 1200 kg/m3.",
                                 "licence": "n/a"}
    a = LabwareAsset("belart_secador_2_vertical", "furniture.belart_secador_2_vertical", "Bel-Art Secador 2.0 立式干燥柜（透明，2 层隔板，左开门）",
                     "Bel-Art Secador 2.0 vertical desiccator cabinet, clear, 2 shelves (F42072-1000)", "furniture", "furniture.storage.desiccator_cabinet", "passive",
                     description_zh="台面干燥柜 Bel-Art Secador 2.0 立式（F42072-1000）：宽 314、深 341、高 414 mm，两层带孔隔板，底层下方放干燥剂，侧开门向左打开。"
                                    "默认实验室的 desiccator 存放位；放在台面上 Franka 可达，机器人开门技能用它验证。", sources=src)
    W, D, H = 0.314, 0.341, 0.414
    tw, tdoor = 0.012, 0.010
    yf = -D / 2                      # front plane (door outer face)
    yi0 = yf + tdoor                 # inside front (door inner face)
    prims = [box(0, yi0 + (D - tdoor) / 2, tw / 2, W, D - tdoor, tw),                # base
             box(0, yi0 + (D - tdoor) / 2, H - tw / 2, W, D - tdoor, tw),            # top
             box(0, D / 2 - tw / 2, H / 2, W - 2 * tw, tw, H - 2 * tw)]              # back
    for sx in (-1, 1):
        prims.append(box(sx * (W / 2 - tw / 2), yi0 + (D - tdoor) / 2, H / 2, tw, D - tdoor, H - 2 * tw))
    z_desic = tw + 0.060
    inner_h = H - tw - z_desic
    shelf_t = 0.004
    z_s1, z_s2 = z_desic, z_desic + inner_h / 2
    sh_w, sh_d = W - 2 * tw - 0.004, D - tdoor - tw - 0.006
    shelves = [box(0, yi0 + 0.003 + sh_d / 2, z - shelf_t / 2, sh_w, sh_d, shelf_t) for z in (z_s1, z_s2)]
    vol = prims_volume(prims + shelves)
    a.add(Link("carcass", "柜体（含两层隔板）", "密封存放吸湿样品与标准物质", "透明共聚酯箱体；隔板带孔，底层下方为干燥剂空间",
               visual=[(prims_mesh(prims), SECADOR_CLEAR), (prims_mesh(shelves), (0.80, 0.86, 0.90, 0.6), {"name": "shelves"})], collision=prims + shelves,
               mass_kg=vol * COPOLYESTER, mass_evidence="assumption", mass_note_zh="板件体积 × 共聚酯 1200 kg/m3（厂家未公布质量）",
               material="copolyester", geometry_evidence="documented", geometry_note_zh="外形 314×341×414 mm 按目录；壁厚/隔板高度为假设"))
    door_w, door_h = W - 0.004, H - 0.004
    door_mass = door_w * door_h * tdoor * COPOLYESTER
    door_link(a, "door", "侧开门（左铰链）", hinge_x=-W / 2 + 0.002, hinge_y=yf + tdoor, z0=0.002, width=door_w, height=door_h, thick=tdoor,
              hinge_side="left", open_rad=math.radians(100), mass=door_mass, mass_note=f"门板 {door_w*1000:.0f}×{door_h*1000:.0f}×{tdoor*1000:.0f} mm × 1200 kg/m3",
              rgba=SECADOR_CLEAR, handle_z=0.22, handle_inset=0.030, limit_evidence="assumption", limit_source=["secador_assumption"],
              handle_len=0.10, handle_offset=0.025)
    xs = [-0.09, 0.0, 0.09]
    ys = [yi0 + 0.075, yi0 + 0.075 + 0.150]
    for i, z in enumerate((z_s1, z_s2), start=1):
        add_slots(a, "desiccator", f"S{i}", xs, ys, z, link="carcass", part=f"shelf_{i}", headroom=round((inner_h / 2) - shelf_t, 4),
                  fits=("bottle", "vial", "jar", "weighing_boat"), access=["door_joint"], radius=0.04,
                  pitch_note="3×2 位/层（0.09 × 0.15 m）；柜内只能侧向取放（上方有隔板/柜顶）")
    _storage(a, ["desiccator"], 0.085, 0.15, [f"S{s}-{k:02d}" for s in (1, 2) for k in range(1, 7)],
             "SP Bel-Art (SP Industries)", "Secador 2.0 vertical desiccator cabinet, clear, 2 shelves", "F42072-1000",
             "2 层 × 6 位 = 12 位（S1 下层、S2 上层）；适合 100/250 mL 瓶、样品瓶、称量舟；放取须先开门，柜内无俯视空间")
    a.prop("carcass", "outer_size_m", [W, D, H], "m", "外形 宽×深×高（目录 12.4×13.4×16.3 in）", "documented", ["belart_secador_2"])
    a.prop("carcass", "internal_volume_m3", 0.034, "m3", "内容积 1.2 ft3（目录）", "documented", ["belart_secador_2"])
    a.prop("door", "open_limit_rad", round(math.radians(100), 5), "rad", "最大开门角（未公布，假设）", "assumption", ["secador_assumption"])
    a.limitations_zh = ["外形、隔板数、开门方向、内容积按 Bel-Art 目录；壁厚、门厚、隔板高度、最大开门角与质量为假设。",
                        "门的密封、卡扣、湿度计与挂锁环未建模；门是被动铰链（机器人拉手开门）。",
                        "柜内位置只能侧向取放；Franka 俯抓不适用于柜内物品。"]
    return a


# ===================================================================================================== DURAN glass desiccator
def build_duran_desiccator() -> LabwareAsset:
    src = pick("duran_desiccator_250", "iso3585", "inventory_places", "robot_access")
    src["desiccator_assumption"] = {"kind": "assumption", "reference": "DN 250 desiccator bottom / lid profile, plate, knob",
                                    "locator": "engineering value",
                                    "note": "Bowl and dome profiles scaled to the documented flange (O274 / O320 mm), complete height 357 mm and lid height 122 mm; glass 5 mm; porcelain plate O235 x 8 mm at 95 mm; knob O40 x 35 mm; glass density 2230 kg/m3, porcelain 2400 kg/m3.",
                                    "licence": "n/a"}
    a = LabwareAsset("duran_desiccator_dn250", "furniture.duran_desiccator_dn250", "DURAN 玻璃干燥器 DN 250（旋钮盖，瓷板）",
                     "DURAN desiccator DN 250 with knob lid (247816604) and porcelain plate", "furniture", "furniture.storage.desiccator", "passive",
                     description_zh="DURAN 玻璃干燥器 DN 250（247816604 = 底 247706605 + 旋钮盖 244106608）：法兰内径 274、外径 320 mm，总高 357 mm，盖高 122 mm；"
                                    "盖为可整体取下的独立刚体（抓盖钮 + 上提），瓷板上 6 个位置。", sources=src)
    t = 0.005
    R_fl, r_fl_in, H_tot, H_lid = 0.160, 0.137, 0.357, 0.122
    z_fl = H_tot - H_lid                 # flange plane (bottom of the lid)
    # bottom bowl: foot ring, narrow lower bowl (desiccant), wider upper body to the flange
    outer = [(0.0, 0.0), (0.095, 0.0), (0.098, 0.006), (0.090, 0.012), (0.085, 0.085), (0.124, 0.098), (0.128, 0.200), (R_fl, z_fl - 0.010), (R_fl, z_fl)]
    inner = [(r_fl_in, z_fl), (0.123, z_fl - 0.020), (0.123, 0.200), (0.121, 0.110), (0.112, 0.104), (0.080, 0.096), (0.080, 0.015), (0.0, 0.015)]
    bowl = revolve(outer + inner, sections=72)
    plate_r, plate_z = 0.1175, 0.106
    plate = trimesh.creation.cylinder(radius=plate_r, height=0.008, sections=72)
    plate.apply_translation([0, 0, plate_z + 0.004])
    col = [Prim("cylinder", [0.095, 0.015], [0, 0, 0.0075])]
    col += ring_boxes(0.090, 0.012, 0.085, 0.085, t, 20) + ring_boxes(0.085, 0.085, 0.124, 0.098, t, 20) + ring_boxes(0.124, 0.098, 0.128, 0.200, t, 20)
    col += ring_boxes(0.128, 0.200, R_fl, z_fl - 0.010, t + 0.004, 24) + ring_boxes(R_fl, z_fl - 0.010, R_fl, z_fl, R_fl - r_fl_in, 24)
    col.append(Prim("cylinder", [plate_r, 0.008], [0, 0, plate_z + 0.004]))
    m_bowl = bowl.volume * GLASS33 + plate.volume * 2400.0
    a.add(Link("body", "干燥器底（含瓷板）", "下部放干燥剂，瓷板上放样品", "硼硅玻璃 3.3 碗体 + 瓷板 Ø235 mm（瓷板顶面 z=0.114 m）",
               visual=[(bowl, (0.86, 0.93, 0.96, 0.35)), (plate, (0.95, 0.95, 0.93, 1.0), {"name": "porcelain_plate"})], collision=col,
               solid_collision=[Prim("cylinder", [R_fl, z_fl], [0, 0, z_fl / 2])], mass_kg=m_bowl, mass_evidence="assumption",
               mass_note_zh="玻璃体积 × 2230 + 瓷板体积 × 2400 kg/m3", material="borosilicate_3_3", geometry_evidence="documented"))
    lid_profile = [(0.0, H_lid - 0.046), (0.055, H_lid - 0.048), (0.112, H_lid - 0.071), (0.140, 0.028), (r_fl_in, 0.010), (r_fl_in, 0.0),
                   (R_fl, 0.0), (R_fl, 0.010), (0.150, 0.030), (0.120, H_lid - 0.065), (0.060, H_lid - 0.042), (0.020, H_lid - 0.035),
                   (0.020, H_lid), (0.0, H_lid)]
    lid = revolve(lid_profile, sections=72)
    lcol = [Prim("cylinder", [0.020, 0.035], [0, 0, H_lid - 0.0175])]
    lcol += ring_boxes(R_fl, 0.0, R_fl, 0.010, R_fl - r_fl_in, 24) + ring_boxes(R_fl, 0.010, 0.150, 0.030, 0.008, 24)
    lcol += ring_boxes(0.150, 0.030, 0.120, H_lid - 0.065, 0.006, 24) + ring_boxes(0.120, H_lid - 0.065, 0.060, H_lid - 0.042, 0.006, 20)
    lcol.append(Prim("cylinder", [0.060, 0.006], [0, 0, H_lid - 0.045]))
    m_lid = lid.volume * GLASS33
    a.add(Link("lid", "旋钮盖", "密封干燥器；抓盖钮取下/盖回", "硼硅玻璃 3.3 圆顶盖 + 顶部盖钮（法兰磨口）", parent="body", origin_m=[0, 0, z_fl],
               joint={"type": "prismatic", "axis": [0, 0, 1], "limits": [0.0, 0.15], "evidence": "assumption", "source_ids": ["desiccator_assumption"],
                      "note_zh": "正方向 = 竖直上提；实际操作为侧推 + 提起，按 touch + intent：抓住盖钮上提超过 clear_lift 即为独立刚体（parts/lid.urdf）"},
               visual=[(lid, (0.86, 0.93, 0.96, 0.35))], collision=lcol, solid_collision=[Prim("cylinder", [R_fl, H_lid], [0, 0, H_lid / 2])],
               mass_kg=m_lid, mass_evidence="assumption", mass_note_zh="玻璃体积 × 2230 kg/m3", material="borosilicate_3_3",
               geometry_evidence="documented", detachable=True))
    xs_r = 0.072
    for k in range(6):
        ang = math.pi / 2 + 2 * math.pi * k / 6
        a.ports.append({"name": f"P1-{k + 1:02d}", "place_id": "desiccator_glass", "part": "plate", "kind": "storage",
                        "position_m": [round(xs_r * math.cos(ang), 5), round(xs_r * math.sin(ang), 5), round(plate_z + 0.008, 5)],
                        "insert_axis": [0.0, 0.0, -1.0], "capture_m": 0.02, "radius_m": 0.03, "moves_with": "body",
                        "headroom_m": round(z_fl - (plate_z + 0.008) + H_lid - 0.05, 4), "fits": ["vial", "weighing_boat", "crucible", "bottle_small"],
                        "access": ["lid_joint"], "note_zh": "瓷板上 6 位（半径 72 mm 圆周）；取放前先取下盖"})
    _storage(a, ["desiccator_glass"], 0.07, 0.10, [f"P1-{k:02d}" for k in range(1, 7)], "DWK Life Sciences (DURAN)",
             "Desiccator with knobbed lid DN 250 (bottom 247706605 + lid 244106608) + porcelain plate DU297256609", "247816604",
             "瓷板 1 层 × 6 位（与 清单原 desiccator 位置名 P1-01..06 相同）；默认实验室的 desiccator 位改为 Secador 柜，此玻璃干燥器可作 desiccator_glass")
    top = H_tot
    a.labware["closure"] = {"type": "lift_off_lid", "link": "lid", "joint": "lid_joint", "axis": [0, 0, 1], "clear_lift_m": 0.13,
                            "rule_zh": "开盖 = 抓盖钮（lid_grasp）上提 ≥ clear_lift_m → 盖为独立刚体；盖回 = 盖对准 lid_seat 放下",
                            "parts": {"body": "parts/body.urdf", "lid": "parts/lid.urdf"}}
    a.labware["frames"] = {
        "lid_grasp": frame([0, 0, top - 0.0175], "base_link", width_m=0.040, franka_ok=True, yaw_free=True, note_zh="盖钮中心（Ø40 mm）"),
        "lid_pre_grasp": frame([0, 0, top + 0.10], "base_link"),
        "lid_seat": frame([0, 0, z_fl], "body", rot=[0, 0, 0, 1], note_zh="盖 link 原点（法兰面中心）盖好时在底座坐标中的位置"),
    }
    a.labware["mass_note_zh"] = f"盖 {m_lid:.2f} kg（Franka 负载 3 kg 以内）"
    a.prop("body", "flange_outer_diameter_m", 0.320, "m", "法兰外径 320±2 mm", "documented", ["duran_desiccator_250"])
    a.prop("body", "flange_inner_diameter_m", 0.274, "m", "法兰内径", "documented", ["duran_desiccator_250"])
    a.prop("body", "height_complete_m", H_tot, "m", "总高（含盖）", "documented", ["duran_desiccator_250"])
    a.prop("lid", "height_m", H_lid, "m", "盖高", "documented", ["duran_desiccator_250"])
    a.limitations_zh = ["法兰直径、总高、盖高按 DWK 表；碗体与盖的曲面、瓷板直径与高度、盖钮尺寸为按比例的假设。",
                        "磨口密封与真空未建模；盖按 touch + intent 上提取下（实际是侧推）。"]
    return a


# ===================================================================================================== cabinets
def _cabinet(a: LabwareAsset, W, D, H, inner_w, inner_d, inner_h, z_in0, wall_rgba, door_rgba, n_doors, door_thick, door_z0, door_h,
             door_mass, door_mass_note, open_rad, open_ev, open_src, handle_z, partitions_x=(), shelf_levels=(), shelf_t=0.025,
             shelf_spans=None, body_mass=None, body_mass_note="", hinge_inset=0.0, handle_len=0.20):
    yf = -D / 2
    yi0 = yf + door_thick
    yi1 = yi0 + inner_d
    side = (W - inner_w) / 2
    prims = [box(0, (yi0 + D / 2) / 2, z_in0 / 2, W, D / 2 - yi0, z_in0),                                    # plinth / base
             box(0, (yi0 + D / 2) / 2, (z_in0 + inner_h + H) / 2, W, D / 2 - yi0, H - z_in0 - inner_h),     # top
             box(0, (yi1 + D / 2) / 2, z_in0 + inner_h / 2, W, D / 2 - yi1, inner_h)]                       # back
    for sx in (-1, 1):
        prims.append(box(sx * (W / 2 - side / 2), (yi0 + yi1) / 2, z_in0 + inner_h / 2, side, inner_d, inner_h))
    for px, pw in partitions_x:
        prims.append(box(px, (yi0 + yi1) / 2, z_in0 + inner_h / 2, pw, inner_d, inner_h))
    shelves = []
    for z in shelf_levels:
        for (x0, x1) in (shelf_spans or [(-inner_w / 2, inner_w / 2)]):
            shelves.append(box((x0 + x1) / 2, (yi0 + yi1) / 2 + 0.005, z - shelf_t / 2, x1 - x0 - 0.004, inner_d - 0.01, shelf_t))
    a.add(Link("carcass", "柜体（含隔板/托盘）", "存放容器；通风与锁未建模", "钢制柜体与隔板",
               visual=[(prims_mesh(prims), wall_rgba)] + ([(prims_mesh(shelves), SHELF_GREY, {"name": "shelves"})] if shelves else []),
               collision=prims + shelves, mass_kg=body_mass, mass_evidence="assumption" if body_mass_note else "documented",
               mass_note_zh=body_mass_note, material="steel", geometry_evidence="documented"))
    return yf, yi0, yi1


def build_asecos_q90() -> LabwareAsset:
    src = pick("asecos_q90_195_120", "inventory_places")
    src["cabinet_assumption"] = {"kind": "assumption", "reference": "asecos cabinet door leaves, shelf heights and door mass", "locator": "engineering value",
                                 "note": "Door leaf 545 x 1750 x 30 mm per wing (data-sheet drawing: 1007 mm between the open leaves, 551 mm swing depth); handle at 1.0 m on the meeting edges; "
                                         "3 shelves at 0.40 m pitch above the bottom sump (32 mm interior grid allows this); door mass from a steel-skin (2 x 1 mm) + 25 mm fire-board (450 kg/m3) sandwich.",
                                 "licence": "n/a"}
    a = LabwareAsset("asecos_q90_195_120", "furniture.asecos_q90_195_120", "asecos Q-CLASSIC-90 易燃品安全柜 Q90.195.120（EN 14470-1 type 90，两扇门，3 层隔板）",
                     "asecos safety storage cabinet Q-CLASSIC-90, Q90.195.120 (EN 14470-1 type 90)", "furniture", "furniture.storage.flammables_cabinet", "passive",
                     description_zh="易燃液体安全柜 asecos Q90.195.120：外形 1193×615×1953 mm、内部 1050×522×1647 mm、两扇翼门最大 89.5°、3 层可调隔板 + 底部集液槽。"
                                    "清单位置 flammables_cabinet：3 层 × 6 位。", sources=src)
    W, D, H = 1.193, 0.615, 1.953
    iw, idp, ih = 1.050, 0.522, 1.647
    z0 = 0.090 + (H - 0.090 - ih) / 2
    door_t, door_w, door_h = 0.030, 0.545, 1.750
    m_door = door_w * door_h * (2 * 0.001 * STEEL + 0.025 * 450.0)
    levels = [z0 + 0.40, z0 + 0.80, z0 + 1.20]
    yf, yi0, yi1 = _cabinet(a, W, D, H, iw, idp, ih, z0, RAL7016, RAL7035, 2, door_t, 0.15, door_h, m_door, "", math.radians(89.5), "documented",
                            ["asecos_q90_195_120"], 1.0, shelf_levels=levels, body_mass=460.0 - 2 * m_door,
                            body_mass_note=f"数据表整机 460 kg（无内部装备）减两扇门 {2*m_door:.1f} kg")
    a.links[-1].mass_evidence = "documented"
    a.properties["_mass_sources"] = {"carcass": ["asecos_q90_195_120"]}
    note = f"门：钢皮 2×1 mm + 25 mm 防火板（450 kg/m3）夹层 × {door_w*1000:.0f}×{door_h*1000:.0f} mm"
    door_link(a, "door_left", "左门", -door_w - 0.002, yf + door_t, 0.15, door_w, door_h, door_t, "left", math.radians(89.5), m_door, note, RAL7035,
              1.0, 0.04, "documented", ["asecos_q90_195_120"])
    door_link(a, "door_right", "右门", door_w + 0.002, yf + door_t, 0.15, door_w, door_h, door_t, "right", math.radians(89.5), m_door, note, RAL7035,
              1.0, 0.04, "documented", ["asecos_q90_195_120"])
    xs = [-0.4375 + 0.175 * k for k in range(6)]
    for i, z in enumerate(levels, start=1):
        head = (levels[i] - z - 0.025) if i < len(levels) else (z0 + ih - z)
        add_slots(a, "flammables_cabinet", f"S{i}", xs, [(yi0 + yi1) / 2], z, link="carcass", part=f"shelf_{i}", headroom=head,
                  fits=("bottle", "solvent_bottle", "gl45_bottle", "jar"), access=["door_left_joint", "door_right_joint"], radius=0.08,
                  pitch_note="每层一排 6 位（0.175 m 间距，可放 2.5 L 溶剂瓶 Ø133 mm）")
    _storage(a, ["flammables_cabinet"], 0.17, 0.30, [f"S{s}-{k:02d}" for s in (1, 2, 3) for k in range(1, 7)], "asecos GmbH",
             "Q-CLASSIC-90 safety storage cabinet, model Q90.195.120 (3 shelves + bottom sump)", "Q90.195.120 (data sheet ID 34192-041 for .MM)",
             "3 层 × 6 位（S1 最低隔板）；底部集液槽不放容器；Franka 固定基座不可达（房间家具）")
    a.prop("carcass", "outer_size_m", [W, D, H], "m", "外形 宽×深×高", "documented", ["asecos_q90_195_120"])
    a.prop("carcass", "inner_size_m", [iw, idp, ih], "m", "内部 宽×深×高", "documented", ["asecos_q90_195_120"])
    a.prop("carcass", "fire_resistance_min", 90, "min", "EN 14470-1 耐火 90 min", "documented", ["asecos_q90_195_120"])
    a.limitations_zh = ["外形、内部尺寸、整机质量、门数与开门角按 asecos 数据表；门扇尺寸、隔板高度、门质量为按图纸比例的假设。",
                        "门的自闭、锁、通风管与热熔连杆未建模；门为被动铰链。", "本数据表为 .MM（多隔间）版本；3 层隔板配置按经销商对 Q90.195.120 的描述。"]
    return a


def build_asecos_sl() -> LabwareAsset:
    src = pick("asecos_sl_196_120", "inventory_places")
    src["cabinet_assumption"] = {"kind": "assumption", "reference": "SL-CLASSIC door leaves, compartment walls, trays and door mass", "locator": "engineering value",
                                 "note": "Two doors 590 x 1880 x 20 mm, each closing one compartment (485 mm wide inside); 2 tray levels per compartment as used by the default-lab inventory (the cabinet takes up to 4 pull-out trays per side); door mass from 1.2 mm steel + 8 mm insulation board (300 kg/m3); doors open 110 deg.",
                                 "licence": "n/a"}
    a = LabwareAsset("asecos_sl_196_120", "furniture.asecos_sl_196_120", "asecos SL-CLASSIC 酸碱柜 SL.196.120.MV（左酸右碱两隔间）",
                     "asecos cabinet for acids and alkalis SL-CLASSIC, SL.196.120.MV", "furniture", "furniture.storage.corrosives_cabinet", "passive",
                     description_zh="酸碱柜 asecos SL.196.120.MV：外形 1197×603×1965 mm，两个密封隔间（每间内部 485×570×1869 mm），两扇门；左隔间放酸、右隔间放碱。"
                                    "清单位置 acids_cabinet（左）与 bases_cabinet（右）：各 2 层 × 6 位。", sources=src)
    W, D, H = 1.197, 0.603, 1.965
    iw_c, idp, ih = 0.485, 0.570, 1.869
    door_t = 0.020
    z0 = 0.050
    centre_wall = W - 2 * iw_c - 2 * 0.060
    cx = centre_wall / 2 + iw_c / 2
    levels = [z0 + 0.06, z0 + 0.55]
    m_door = 0.590 * 1.880 * (0.0012 * STEEL + 0.008 * 300.0)
    yf, yi0, yi1 = _cabinet(a, W, D, H, 2 * iw_c + centre_wall, idp, ih, z0, LAB_WHITE, LAB_WHITE, 2, door_t, 0.03, 1.88, m_door, "",
                            math.radians(110), "assumption", ["cabinet_assumption"], 1.0, partitions_x=[(0.0, centre_wall)],
                            shelf_levels=levels, shelf_spans=[(-cx - iw_c / 2, -cx + iw_c / 2), (cx - iw_c / 2, cx + iw_c / 2)],
                            body_mass=215.0 - 2 * m_door, body_mass_note=f"数据表 215 kg（无内部装备）减两扇门 {2*m_door:.1f} kg")
    a.links[-1].mass_evidence = "documented"
    a.properties["_mass_sources"] = {"carcass": ["asecos_sl_196_120"]}
    note = f"门：1.2 mm 钢板 + 8 mm 板（300 kg/m3）× 590×1880 mm"
    door_link(a, "door_left", "左门（酸隔间）", -W / 2 + 0.005, yf + door_t, 0.03, 0.590, 1.880, door_t, "left", math.radians(110), m_door, note, LAB_WHITE,
              1.0, 0.04, "assumption", ["cabinet_assumption"])
    door_link(a, "door_right", "右门（碱隔间）", W / 2 - 0.005, yf + door_t, 0.03, 0.590, 1.880, door_t, "right", math.radians(110), m_door, note, LAB_WHITE,
              1.0, 0.04, "assumption", ["cabinet_assumption"])
    ys = [yi0 + 0.15, yi0 + 0.42]
    for side, place, sx, door in (("acid", "acids_cabinet", -1, "door_left_joint"), ("base", "bases_cabinet", 1, "door_right_joint")):
        xs = [sx * cx - 0.15, sx * cx, sx * cx + 0.15]
        for i, z in enumerate(levels, start=1):
            head = (levels[1] - z - 0.025) if i == 1 else (z0 + ih - z)
            add_slots(a, place, f"S{i}", xs, ys, z, link="carcass", part=f"{side}_tray_{i}", headroom=head,
                      fits=("bottle", "gl45_bottle", "solvent_bottle", "jar"), access=[door], radius=0.07,
                      pitch_note=f"{'酸' if side == 'acid' else '碱'}隔间 3×2 位/层（0.15 × 0.27 m）")
    for p in a.ports:
        p["name"] = ("A:" if p["place_id"] == "acids_cabinet" else "B:") + p["name"]
    _storage(a, ["acids_cabinet", "bases_cabinet"], 0.15, 0.30, [f"{c}:S{s}-{k:02d}" for c in ("A", "B") for s in (1, 2) for k in range(1, 7)],
             "asecos GmbH", "Cabinet for acids and alkalis SL-CLASSIC, model SL.196.120.MV (laboratory white)", "SL.196.120.MV (ID 30606-005)",
             "槽位名 A:S1-01..A:S2-06 = acids_cabinet 的 S1-01..S2-06；B:... = bases_cabinet；Franka 固定基座不可达（房间家具）")
    a.prop("carcass", "outer_size_m", [W, D, H], "m", "外形 宽×深×高", "documented", ["asecos_sl_196_120"])
    a.prop("carcass", "compartment_inner_size_m", [iw_c, idp, ih], "m", "每隔间内部 宽×深×高", "documented", ["asecos_sl_196_120"])
    a.limitations_zh = ["外形、隔间内部尺寸、质量、门数按 asecos 数据表；门扇尺寸、托盘高度、门质量与开门角为假设。",
                        "实际为抽拉托盘（每侧最多 4 层）；这里按清单的 2 层建为固定托盘。", "通风、锁未建模。"]
    return a


def build_asecos_cs(place_id: str = "toxics_cabinet", n_shelves: int = 2, nx: int = 3, ny: int = 2, slug_suffix: str = "",
                    title_zh: str = "有毒品，可上锁，通风") -> LabwareAsset:
    src = pick("asecos_cs_195_105", "inventory_places")
    src["cabinet_assumption"] = {"kind": "assumption", "reference": "CS-CLASSIC door leaves, internal depth, shelves and masses", "locator": "engineering value",
                                 "note": "Internal depth 470 mm (520 external minus walls/door, not listed); two wing doors 520 x 1830 x 20 mm; 2 shelves at 0.45 m pitch for the inventory's toxics place; body mass from 1 mm steel sheet volume + 20 % for frame; door mass 1.2 mm steel + 8 mm board.",
                                 "licence": "n/a"}
    slug = "asecos_cs_195_105" + slug_suffix
    a = LabwareAsset(slug, f"furniture.{slug}", f"asecos CS-CLASSIC 化学品柜 CS.195.105（{title_zh}，{n_shelves} 层 × {nx*ny} 位）",
                     f"asecos chemical cabinet CS-CLASSIC, CS.195.105 ({place_id}, {n_shelves} shelves)", "furniture", f"furniture.storage.{place_id}", "passive",
                     description_zh=f"asecos CS-CLASSIC CS.195.105：外形 1055×520×1950 mm，内部宽 996、高 1835 mm，两扇门，可上锁，适合非易燃化学品与有毒物质；"
                                    f"隔板数可选（经销商列出 2/3/4/6 层配置）。清单位置 {place_id}：{n_shelves} 层 × {nx*ny} 位。", sources=src)
    W, D, H = 1.055, 0.520, 1.950
    iw, idp, ih = 0.996, 0.470, 1.835
    door_t, z0 = 0.020, 0.060
    pitch_z = min(0.45, (ih - 0.35) / max(n_shelves, 1))
    levels = [z0 + 0.30 + pitch_z * k for k in range(n_shelves)]
    m_door = 0.520 * 1.830 * (0.0012 * STEEL + 0.008 * 300.0)
    sheet = 75.0 - 2 * m_door                      # data sheet ID 30496-040/-041: 75 kg without interior equipment
    yf, yi0, yi1 = _cabinet(a, W, D, H, iw, idp, ih, z0, LAB_WHITE, LAB_WHITE, 2, door_t, 0.06, 1.83, m_door, "", math.radians(110), "assumption",
                            ["cabinet_assumption"], 1.0, shelf_levels=levels, body_mass=sheet,
                            body_mass_note=f"数据表 ID 30496-040/-041：75 kg（无内部装备）减两扇门 {2*m_door:.1f} kg")
    a.links[-1].mass_evidence = "documented"
    a.sources["asecos_cs_30496040"] = {"kind": "manufacturer", "reference": "asecos product data sheets ID 30496-040 / 30496-041, model CS.195.105.WDFW (https://www.asecos-configurator.com/EN/DE/asecos/download/DAT_EXT_30496040-product-data-sheet_IN.pdf)",
                                       "locator": "p.1: external 1055 x 520 x 1950 mm, internal 996 x 493 x 1835 mm, 75 kg, 2 doors, depth with open doors 1026 mm",
                                       "note": "Mass; the modelled internal depth (470 mm) is 23 mm less than the data sheet's 493 mm (geometry kept unchanged in the 2026-10-23 appearance pass).", "licence": "facts"}
    a.properties["_mass_sources"] = {"carcass": ["asecos_cs_30496040"]}
    note = "门：1.2 mm 钢板 + 8 mm 板 × 520×1830 mm"
    door_link(a, "door_left", "左门", -0.522, yf + door_t, 0.06, 0.520, 1.830, door_t, "left", math.radians(110), m_door, note, LAB_WHITE, 1.0, 0.04,
              "assumption", ["cabinet_assumption"])
    door_link(a, "door_right", "右门", 0.522, yf + door_t, 0.06, 0.520, 1.830, door_t, "right", math.radians(110), m_door, note, LAB_WHITE, 1.0, 0.04,
              "assumption", ["cabinet_assumption"])
    px = 0.90 / nx
    xs = [-0.45 + px / 2 + px * k for k in range(nx)]
    ys = [yi0 + 0.12, yi0 + 0.35][:ny]
    for i, z in enumerate(levels, start=1):
        head = (levels[i] - z - 0.025) if i < len(levels) else (z0 + ih - z)
        add_slots(a, place_id, f"S{i}", xs, ys, z, link="carcass", part=f"shelf_{i}", headroom=head,
                  fits=("bottle", "gl45_bottle", "jar", "sbs", "box"), access=["door_left_joint", "door_right_joint"], radius=0.07,
                  pitch_note=f"{nx}×{ny} 位/层（{px:.3f} × 0.23 m）")
    _storage(a, [place_id], min(px, 0.23) - 0.001, 0.25, [f"S{s}-{k:02d}" for s in range(1, n_shelves + 1) for k in range(1, nx * ny + 1)], "asecos GmbH",
             f"Chemical cabinet CS-CLASSIC, model CS.195.105, {n_shelves} shelves", "CS.195.105",
             f"{n_shelves} 层 × {nx*ny} 位；可上锁、接通风；Franka 固定基座不可达")
    a.prop("carcass", "outer_size_m", [W, D, H], "m", "外形 宽×深×高", "documented", ["asecos_cs_195_105"])
    a.prop("carcass", "inner_width_m", iw, "m", "内部宽", "documented", ["asecos_cs_195_105"])
    a.prop("carcass", "inner_height_m", ih, "m", "内部高", "documented", ["asecos_cs_195_105"])
    a.limitations_zh = ["外形、内部宽与高、质量按 asecos 数据表 ID 30496-040/-041；建模内深 470 mm 比数据表 493 mm 浅 23 mm（外观轮次未改几何）；门扇、隔板高度为假设。",
                        f"锁与通风未建模；隔板数按清单的 {place_id} 位置数（{n_shelves} 层 × {nx*ny}）。"]
    return a


def build_liebherr_lkv3910() -> LabwareAsset:
    src = pick("liebherr_lkv3910", "inventory_places")
    src["fridge_assumption"] = {"kind": "assumption", "reference": "LKv 3910 wall / door split, shelf heights, door mass, hinge side", "locator": "engineering value",
                                "note": "Side walls 78.5 mm, back 80 mm, door 100 mm, base 130 mm, top 75 mm (external minus internal); hinge on the right as delivered (door hinge is reversible); door opens 110 deg; door mass: 2 x 0.6 mm steel skins + 90 mm PU foam (40 kg/m3) + 2 kg liner/handle; 4 of the 5 shelves carry the inventory's 4 x 8 positions at 0.36 m pitch.",
                                "licence": "n/a"}
    a = LabwareAsset("liebherr_lkv3910", "furniture.liebherr_lkv3910", "Liebherr LKv 3910 MediLine 实验室冷藏柜（+3…+16 °C，360 L，防爆内腔）",
                     "Liebherr laboratory refrigerator LKv 3910 MediLine", "furniture", "furniture.storage.lab_refrigerator", "passive",
                     description_zh="实验室冷藏柜 Liebherr LKv 3910：外形 597×615×1840 mm、内部 440×435×1635 mm、5 层可调隔板（每层 45 kg）、内腔无火花；"
                                    "清单位置 fridge_4c：4 层 × 8 位。", sources=src)
    W, D, H = 0.597, 0.615, 1.840
    iw, idp, ih = 0.440, 0.435, 1.635
    door_t, z0 = 0.100, 0.130
    levels = [z0 + 0.06 + 0.36 * k for k in range(4)]
    door_w, door_h = W - 0.004, H - 0.010
    m_door = door_w * door_h * (2 * 0.0006 * STEEL + 0.090 * 40.0) + 2.0
    shell = 2 * (W * H + D * H + W * D) * 0.0006 * STEEL + (W * D * H - iw * idp * ih - door_w * door_h * door_t) * 40.0
    yf, yi0, yi1 = _cabinet(a, W, D, H, iw, idp, ih, z0, LAB_WHITE, LAB_WHITE, 1, door_t, 0.005, door_h, m_door, "", math.radians(110), "assumption",
                            ["fridge_assumption"], 1.1, shelf_levels=levels, shelf_t=0.005, body_mass=shell + 25.0,
                            body_mass_note="钢外壳 0.6 mm + PU 发泡 40 kg/m3 + 压缩机/制冷系统约 25 kg（假设；厂家质量未取得）")
    note = "门：2×0.6 mm 钢皮 + 90 mm PU 发泡（40 kg/m3）+ 内衬/拉手 2 kg"
    door_link(a, "door", "冷藏门（右铰链）", W / 2 - 0.002, yf + door_t, 0.005, door_w, door_h, door_t, "right", math.radians(110), m_door, note, LAB_WHITE,
              1.1, 0.035, "assumption", ["fridge_assumption"], handle_len=0.30)
    xs = [-0.165, -0.055, 0.055, 0.165]
    ys = [yi0 + 0.11, yi0 + 0.32]
    for i, z in enumerate(levels, start=1):
        head = (levels[i] - z - 0.005) if i < len(levels) else (z0 + ih - z)
        add_slots(a, "fridge_4c", f"S{i}", xs, ys, z, link="carcass", part=f"shelf_{i}", headroom=head,
                  fits=("bottle", "gl45_bottle", "vial", "tube_rack", "jar"), access=["door_joint"], radius=0.05,
                  pitch_note="4×2 位/层（0.11 × 0.21 m）")
    _storage(a, ["fridge_4c"], 0.10, 0.30, [f"S{s}-{k:02d}" for s in (1, 2, 3, 4) for k in range(1, 9)], "Liebherr",
             "LKv 3910 MediLine laboratory refrigerator (spark-free interior)", "LKv 3910",
             "4 层 × 8 位（S1 最低）；2–8 °C 存放；Franka 固定基座不可达（房间家具）")
    a.labware["temperature_range_c"] = [3, 16]
    a.prop("carcass", "outer_size_m", [W, D, H], "m", "外形 宽×深×高", "documented", ["liebherr_lkv3910"])
    a.prop("carcass", "inner_size_m", [iw, idp, ih], "m", "内部 宽×深×高", "documented", ["liebherr_lkv3910"])
    a.prop("carcass", "temperature_range_c", [3, 16], "degC", "温度范围", "documented", ["liebherr_lkv3910"])
    a.limitations_zh = ["外形、内部尺寸、温度范围、隔板数按经销商列出的 Liebherr LKv 3910 数据；墙厚分配、门质量、铰链侧（出厂右侧，可换）、隔板高度为假设。",
                        "制冷过程、温度控制与报警未建模（家具资产）；温度作为存放条件记录。"]
    return a


def build_denios_stands() -> LabwareAsset:
    src = {"denios_115873": {"kind": "manufacturer",
                             "reference": "DENIOS steel gas cylinder stand for 2 gas cylinders max. O 230 mm, for wall mounting, item 115873W (https://denios.co.uk/gas-cylinder-stand-galvanized-for-2-x-o-230-mm-gas-cylinders-115873/Gas Cylinder Handling)",
                             "locator": "560 x 410 x 1005 mm (W x D x H), blue powder-coated steel, securing chain, load 150 kg, can be joined to further stands",
                             "note": "Four stands joined side by side give the 8 cylinder positions of the default lab's cylinder_rack. accessed 2026-10-22.",
                             "licence": "facts"},
           **pick("inventory_places")}
    src["stand_assumption"] = {"kind": "assumption", "reference": "stand tube sections and chain height", "locator": "engineering value",
                               "note": "Base plate 4 mm, two 40 x 40 mm rear posts, cradle rail at 0.95 m with two half-ring cradles per stand; mass from steel volume.",
                               "licence": "n/a"}
    a = LabwareAsset("denios_cylinder_stand_x4", "furniture.denios_cylinder_stand_x4", "DENIOS 气瓶架 115873W × 4（8 个 Ø230 mm 气瓶位，链条固定）",
                     "DENIOS gas cylinder stand 115873W x 4 (8 cylinders, O <= 230 mm)", "furniture", "furniture.storage.cylinder_rack", "passive",
                     description_zh="四个 DENIOS 115873W 落地靠墙气瓶架并排（每个 560×410×1005 mm，2 瓶 Ø≤230 mm，链条固定），共 8 位 C1..C8；清单位置 cylinder_rack。",
                     sources=src)
    n, Ws, Ds, Hs = 4, 0.560, 0.410, 1.005
    W = n * Ws
    prims = []
    for i in range(n):
        x0 = -W / 2 + i * Ws + Ws / 2
        prims.append(box(x0, 0, 0.002, Ws - 0.004, Ds, 0.004))
        for sx in (-1, 1):
            prims.append(box(x0 + sx * (Ws / 2 - 0.022), Ds / 2 - 0.022, Hs / 2, 0.040, 0.040, Hs))
        prims.append(box(x0, Ds / 2 - 0.022, 0.95, Ws - 0.004, 0.040, 0.040))
        for sx in (-1, 1):
            prims.append(box(x0 + sx * 0.14, Ds / 2 - 0.06, 0.95, 0.20, 0.008, 0.030))
    mass = prims_volume(prims) * STEEL
    a.add(Link("stands", "气瓶架（4 个并排）", "竖直固定 8 个气瓶", "钢制底板 + 后立柱 + 托架与链条（链条未建模）",
               visual=[(prims_mesh(prims), DENIOS_BLUE)], collision=prims, mass_kg=mass, mass_evidence="assumption",
               mass_note_zh="钢件体积 × 7850 kg/m3", material="steel", geometry_evidence="documented"))
    k = 1
    for i in range(n):
        x0 = -W / 2 + i * Ws + Ws / 2
        for sx in (-1, 1):
            a.ports.append({"name": f"C{k}", "place_id": "cylinder_rack", "part": f"stand_{i + 1}", "kind": "storage",
                            "position_m": [round(x0 + sx * 0.14, 5), round(Ds / 2 - 0.06 - 0.1145 - 0.01, 5), 0.004],
                            "insert_axis": [0.0, 0.0, -1.0], "capture_m": 0.05, "radius_m": 0.115, "moves_with": "stands",
                            "headroom_m": 1.0, "fits": ["gas_cylinder_50l"], "access": [], "note_zh": "气瓶底中心；托架 + 链条在 0.95 m"})
            k += 1
    _storage(a, ["cylinder_rack"], 0.24, None, [f"C{k}" for k in range(1, 9)], "DENIOS", "Steel gas cylinder stand for 2 cylinders O<=230 mm, wall mounting (x4)",
             "115873W", "8 位 C1..C8（每架 2 位，间距 0.28 m）；Franka 不可达（房间设施）")
    a.labware["storage"]["headroom_m"] = None
    a.labware["frames"] = {"place_reference": frame([0, 0, 0], "base_link", rot=[0, 0, 0, 1], note_zh="资产原点：占地中心，地面 z=0；+Y 为墙侧")}
    a.prop("stands", "stand_size_m", [Ws, Ds, Hs], "m", "单架 宽×深×高", "documented", ["denios_115873"])
    a.prop("stands", "max_cylinder_diameter_m", 0.230, "m", "最大气瓶直径", "documented", ["denios_115873"])
    a.limitations_zh = ["单架外形与容量按 DENIOS 目录；管材截面、托架高度、质量为假设；链条未建模（气瓶由场景固定）。"]
    return a


def build_asecos_g90() -> LabwareAsset:
    src = pick("asecos_g90_205_140", "inventory_places", "rama_50l_cylinder")
    src["cabinet_assumption"] = {"kind": "assumption", "reference": "G90 door leaves, cylinder positions and door mass", "locator": "engineering value",
                                 "note": "Two wing doors 620 x 1900 x 60 mm (fire-resistant sandwich: 2 x 1.5 mm steel + 55 mm fire board at 450 kg/m3); 4 cylinder positions on the floor at 0.30 m pitch. (Door angle 180 deg and 690 kg from data sheet ID 30641-001.)",
                                 "licence": "n/a"}
    a = LabwareAsset("asecos_g90_205_140", "furniture.asecos_g90_205_140", "asecos G-ULTIMATE-90 可燃气体钢瓶柜 G90.205.140（4 × 50 L）",
                     "asecos gas cylinder cabinet G-ULTIMATE-90, G90.205.140", "furniture", "furniture.storage.gas_cabinet", "passive",
                     description_zh="可燃气体钢瓶柜 asecos G90.205.140：外形 1398×615×2050 mm、内部 1245×400×1858 mm、可放 4 × 50 L 或 8 × 10 L 钢瓶，约 707.5 kg；清单位置 gas_cabinet_flammable：4 位。",
                     sources=src)
    W, D, H = 1.398, 0.615, 2.050
    iw, idp, ih = 1.245, 0.400, 1.858
    door_t, z0 = 0.060, 0.090
    m_door = 0.620 * 1.900 * (2 * 0.0015 * STEEL + 0.055 * 450.0)
    yf, yi0, yi1 = _cabinet(a, W, D, H, iw, idp, ih, z0, RAL7016, RAL7035, 2, door_t, 0.10, 1.90, m_door, "", math.radians(180), "documented",
                            ["asecos_g90_205_140"], 1.0, body_mass=690.0 - 2 * m_door,
                            body_mass_note=f"数据表 ID 30641-001：690 kg（无内部装备）减两扇门 {2*m_door:.1f} kg")
    a.links[-1].mass_evidence = "documented"
    a.properties["_mass_sources"] = {"carcass": ["asecos_g90_205_140"]}
    note = "门：2×1.5 mm 钢板 + 55 mm 防火板（450 kg/m3）× 620×1900 mm"
    door_link(a, "door_left", "左门", -0.622, yf + door_t, 0.10, 0.620, 1.900, door_t, "left", math.radians(180), m_door, note, RAL7035, 1.0, 0.04,
              "documented", ["asecos_g90_205_140"])
    door_link(a, "door_right", "右门", 0.622, yf + door_t, 0.10, 0.620, 1.900, door_t, "right", math.radians(180), m_door, note, RAL7035, 1.0, 0.04,
              "documented", ["asecos_g90_205_140"])
    for k in range(4):
        a.ports.append({"name": f"G{k + 1}", "place_id": "gas_cabinet_flammable", "part": "floor", "kind": "storage",
                        "position_m": [round(-0.45 + 0.30 * k, 5), round((yi0 + yi1) / 2, 5), z0], "insert_axis": [0.0, 0.0, -1.0],
                        "capture_m": 0.05, "radius_m": 0.115, "moves_with": "carcass", "headroom_m": round(ih, 4), "fits": ["gas_cylinder_50l", "gas_cylinder_10l"],
                        "access": ["door_left_joint", "door_right_joint"], "note_zh": "气瓶底中心（柜内地板）"})
    _storage(a, ["gas_cabinet_flammable"], 0.24, 1.6, [f"G{k}" for k in range(1, 5)], "asecos GmbH",
             "Gas cylinder cabinet G-ULTIMATE-90, model G90.205.140", "G90.205.140 (ID 30641-001-30642)",
             "4 位 G1..G4（50 L 钢瓶；8 × 10 L 时可两两并放）；Franka 不可达（房间设施）")
    a.prop("carcass", "outer_size_m", [W, D, H], "m", "外形 宽×深×高", "documented", ["asecos_g90_205_140"])
    a.prop("carcass", "inner_size_m", [iw, idp, ih], "m", "内部 宽×深×高", "documented", ["asecos_g90_205_140"])
    a.limitations_zh = ["外形、内部尺寸、质量（690 kg）、开门角（180°）按 asecos 数据表 ID 30641-001；门扇尺寸、门质量与瓶位为假设。", "通风、气瓶固定架、管路穿板未建模。"]
    return a


def build_liebherr_lguex1500() -> LabwareAsset:
    src = pick("liebherr_lguex1500", "inventory_places")
    src["freezer_assumption"] = {"kind": "assumption", "reference": "LGUex 1500 door, interior and drawer geometry", "locator": "engineering value",
                                 "note": "Door 70 mm (foam-insulated), interior 440 x 440 x 620 mm (~120 L of the 129 L gross), 3 drawers 420 x 400 x 150 mm pulling out 0.30 m; door hinge right, opens 110 deg; door mass from 2 x 0.6 mm steel + 60 mm PU foam + 1 kg; body mass not found (assumed 40 kg).",
                                 "licence": "n/a"}
    a = LabwareAsset("liebherr_lguex1500", "furniture.liebherr_lguex1500", "Liebherr MediLine LGUex 1500 实验室冷冻柜（−26 °C，3 抽屉，防爆内腔）",
                     "Liebherr MediLine laboratory freezer LGUex 1500", "furniture", "furniture.storage.lab_freezer", "passive",
                     description_zh="台下式实验室冷冻柜 Liebherr LGUex 1500：外形 600×615×820 mm、129 L、3 个抽屉 + 网篮、最低 −26 °C；清单位置 freezer_m20c：3 抽屉 × 8 位。",
                     sources=src)
    W, D, H = 0.600, 0.615, 0.820
    iw, idp, ih = 0.440, 0.440, 0.620
    door_t, z0 = 0.070, 0.120
    m_door = (W - 0.004) * (H - 0.010) * (2 * 0.0006 * STEEL + 0.060 * 40.0) + 1.0
    yf, yi0, yi1 = _cabinet(a, W, D, H, iw, idp, ih, z0, LAB_WHITE, LAB_WHITE, 1, door_t, 0.005, H - 0.010, m_door, "", math.radians(110), "assumption",
                            ["freezer_assumption"], 0.6, body_mass=40.0, body_mass_note="厂家质量未取得：按同尺寸台下冷冻柜假设 40 kg")
    note = "门：2×0.6 mm 钢皮 + 60 mm PU 发泡 + 1 kg"
    door_link(a, "door", "冷冻门（右铰链）", W / 2 - 0.002, yf + door_t, 0.005, W - 0.004, H - 0.010, door_t, "right", math.radians(110), m_door, note,
              LAB_WHITE, 0.62, 0.035, "assumption", ["freezer_assumption"], handle_len=0.20)
    dw, dd, dh, dt = 0.420, 0.400, 0.150, 0.003
    for i in range(3):
        zb = z0 + 0.02 + i * 0.20
        prims = [box(0, -dd / 2, dt / 2, dw, dd, dt), box(0, -dt / 2, dh / 2, dw, dt, dh), box(0, -dd + dt / 2, dh / 2, dw, dt, dh)]
        prims += [box(sx * (dw / 2 - dt / 2), -dd / 2, dh / 2, dt, dd, dh) for sx in (-1, 1)]
        a.add(Link(f"drawer_{i + 1}", f"抽屉 {i + 1}", "放冷冻样品；向前拉出", "透明塑料抽屉（被动滑轨）", parent="carcass", origin_m=[0.0, yi1 - 0.01, zb],
                   joint={"type": "prismatic", "axis": [0, -1, 0], "limits": [0.0, 0.30], "evidence": "assumption", "source_ids": ["freezer_assumption"],
                          "note_zh": "正方向 = 向前（−Y）拉出；须先开门"},
                   visual=[(prims_mesh(prims), (0.85, 0.90, 0.95, 0.6))], collision=prims, mass_kg=prims_volume(prims) * 1050.0, mass_evidence="assumption",
                   mass_note_zh="塑料体积 × 1050 kg/m3", material="ps", geometry_evidence="assumption"))
        xs = [-0.15, -0.05, 0.05, 0.15]
        add_slots(a, "freezer_m20c", f"D{i + 1}", xs, [yi1 - 0.01 - dd + 0.10, yi1 - 0.01 - 0.10], zb + dt, link=f"drawer_{i + 1}", part=f"drawer_{i + 1}",
                  headroom=dh - dt, fits=("vial", "tube", "tube_box", "bottle_small"), access=["door_joint", f"drawer_{i + 1}_joint"], radius=0.045,
                  pitch_note="抽屉内 4×2 位（0.10 × 0.20 m）；位置随抽屉移动")
    _storage(a, ["freezer_m20c"], 0.095, 0.12, [f"D{d}-{k:02d}" for d in (1, 2, 3) for k in range(1, 9)], "Liebherr",
             "MediLine LGUex 1500 laboratory freezer (3 drawers + basket)", "LGUex 1500", "3 抽屉 × 8 位（D1 最低）；−20 °C 存放；Franka 不可达（房间设施）")
    a.labware["temperature_range_c"] = [-26, -9]
    a.prop("carcass", "outer_size_m", [W, D, H], "m", "外形 宽×深×高", "documented", ["liebherr_lguex1500"])
    a.prop("carcass", "gross_volume_m3", 0.129, "m3", "总容积 129 L", "documented", ["liebherr_lguex1500"])
    a.limitations_zh = ["外形、容积、抽屉数与最低温度按经销商列出的 Liebherr 数据；内部尺寸、抽屉尺寸、门/柜质量与铰链侧为假设。", "制冷与温控未建模。"]
    return a


def build_metro_rack() -> LabwareAsset:
    src = pick("metro_super_erecta", "inventory_places")
    src["rack_assumption"] = {"kind": "assumption", "reference": "shelf heights and wire-shelf thickness", "locator": "engineering value",
                              "note": "Shelves at 0.15 / 0.60 / 1.05 / 1.50 m (posts adjustable on 1 in grooves), wire shelf modelled as a 30 mm deep frame plate; chrome steel mass from the listed shelf weight not found -> steel volume.",
                              "licence": "n/a"}
    a = LabwareAsset("metro_super_erecta_1848_4shelf", "furniture.metro_super_erecta_1848_4shelf", "Metro Super Erecta 镀铬线网架（1848NC × 4 层，74P 立柱）",
                     "Metro Super Erecta chrome wire shelving, 1848NC shelves x 4, 74P posts", "furniture", "furniture.storage.open_rack", "passive",
                     description_zh="开放式试剂架 Metro Super Erecta：4 层 1848NC 线网板（1219×457 mm）+ 4 根 74P 立柱（1880 mm）；清单位置 reagent_shelf_a（房间里，Franka 不可达）：4 层 × 8 位。",
                     sources=src)
    W, D, H = 1.219, 0.457, 1.880
    levels = [0.15, 0.60, 1.05, 1.50]
    prims = [Prim("cylinder", [0.0127, H], [sx * (W / 2 - 0.0127), sy * (D / 2 - 0.0127), H / 2]) for sx in (-1, 1) for sy in (-1, 1)]
    for z in levels:
        prims.append(box(0, 0, z - 0.015, W - 0.03, D - 0.03, 0.006))
        prims += [box(0, sy * (D / 2 - 0.02), z - 0.015, W - 0.03, 0.008, 0.03) for sy in (-1, 1)]
    a.add(Link("rack", "线网架", "开放存放试剂瓶", "镀铬钢线网板与立柱", visual=[(prims_mesh(prims), (0.80, 0.81, 0.83, 1.0))], collision=prims,
               mass_kg=prims_volume(prims) * STEEL * 0.35, mass_evidence="assumption", mass_note_zh="网板按 35 % 实心钢体积估算", material="steel",
               geometry_evidence="documented"))
    xs = [-0.5325 + 0.1521 * k for k in range(8)]
    for i, z in enumerate(levels, start=1):
        head = (levels[i] - z - 0.03) if i < len(levels) else 0.35
        add_slots(a, "reagent_shelf_a", f"S{i}", xs, [0.0], z - 0.009, link="rack", part=f"shelf_{i}", headroom=head,
                  fits=("bottle", "gl45_bottle", "jar", "box"), access=[], radius=0.07, pitch_note="每层一排 8 位（0.152 m）")
    _storage(a, ["reagent_shelf_a"], 0.15, 0.30, [f"S{s}-{k:02d}" for s in (1, 2, 3, 4) for k in range(1, 9)], "InterMetro (Metro)",
             "Super Erecta chrome wire shelving, 4 x 1848NC shelves on 74P posts", "1848NC + 74P", "4 层 × 8 位；房间开放架，Franka 不可达")
    a.labware["frames"] = {"place_reference": frame([0, 0, 0], "base_link", rot=[0, 0, 0, 1], note_zh="资产原点：占地中心，地面 z=0")}
    a.prop("rack", "shelf_size_m", [W, D], "m", "网板 48×18 in", "documented", ["metro_super_erecta"])
    a.prop("rack", "post_height_m", H, "m", "立柱 74 in", "documented", ["metro_super_erecta"])
    a.limitations_zh = ["网板与立柱尺寸按 Metro 目录；层高、网板厚度与质量为假设；线网按实心薄板建碰撞。"]
    return a


BUILDERS = {
    "trippnt_step_shelf_2tier": build_bench_shelf,
    "belart_secador_2_vertical": build_secador,
    "duran_desiccator_dn250": build_duran_desiccator,
    "asecos_q90_195_120": build_asecos_q90,
    "asecos_sl_196_120": build_asecos_sl,
    "asecos_cs_195_105": build_asecos_cs,
    "liebherr_lkv3910": build_liebherr_lkv3910,
    "denios_cylinder_stand_x4": build_denios_stands,
    "asecos_cs_195_105_oxidizers": lambda: build_asecos_cs("oxidizers_cabinet", 1, 3, 2, "_oxidizers", "氧化剂柜"),
    "asecos_cs_195_105_standards": lambda: build_asecos_cs("standards_cabinet", 3, 4, 2, "_standards", "标准物质柜"),
    "asecos_cs_195_105_consumables": lambda: build_asecos_cs("consumables_cabinet", 4, 4, 2, "_consumables", "耗材柜"),
    "asecos_g90_205_140": build_asecos_g90,
    "liebherr_lguex1500": build_liebherr_lguex1500,
    "metro_super_erecta_1848_4shelf": build_metro_rack,
}

import appearance as _appearance  # noqa: E402

BUILDERS = {k: (lambda f=f: _appearance.apply(f())) for k, f in BUILDERS.items()}

# inventory place id -> furniture type and position prefix (chemlab/chemlab/inventory.py PLACES, 2026-10-22)
PLACE_MAP = {
    "bench_shelf": {"furniture": "furniture.trippnt_step_shelf_2tier", "positions": "T1-01..T1-05, T2-01..T2-05", "slot_prefix": "", "reach": "franka_bench"},
    "desiccator": {"furniture": "furniture.belart_secador_2_vertical", "positions": "S1-01..S1-06, S2-01..S2-06", "slot_prefix": "", "reach": "franka_bench"},
    "desiccator_glass": {"furniture": "furniture.duran_desiccator_dn250", "positions": "P1-01..P1-06", "slot_prefix": "", "reach": "franka_bench"},
    "flammables_cabinet": {"furniture": "furniture.asecos_q90_195_120", "positions": "S1-01..S3-06", "slot_prefix": "", "reach": "room"},
    "acids_cabinet": {"furniture": "furniture.asecos_sl_196_120", "positions": "S1-01..S2-06", "slot_prefix": "A:", "reach": "room"},
    "bases_cabinet": {"furniture": "furniture.asecos_sl_196_120", "positions": "S1-01..S2-06", "slot_prefix": "B:", "reach": "room"},
    "toxics_cabinet": {"furniture": "furniture.asecos_cs_195_105", "positions": "S1-01..S2-06", "slot_prefix": "", "reach": "room"},
    "fridge_4c": {"furniture": "furniture.liebherr_lkv3910", "positions": "S1-01..S4-08", "slot_prefix": "", "reach": "room"},
    "cylinder_rack": {"furniture": "furniture.denios_cylinder_stand_x4", "positions": "C1..C8", "slot_prefix": "", "reach": "room"},
    "oxidizers_cabinet": {"furniture": "furniture.asecos_cs_195_105_oxidizers", "positions": "S1-01..S1-06", "slot_prefix": "", "reach": "room"},
    "standards_cabinet": {"furniture": "furniture.asecos_cs_195_105_standards", "positions": "S1-01..S3-08", "slot_prefix": "", "reach": "room"},
    "consumables_cabinet": {"furniture": "furniture.asecos_cs_195_105_consumables", "positions": "S1-01..S4-08", "slot_prefix": "", "reach": "room"},
    "gas_cabinet_flammable": {"furniture": "furniture.asecos_g90_205_140", "positions": "G1..G4", "slot_prefix": "", "reach": "room"},
    "freezer_m20c": {"furniture": "furniture.liebherr_lguex1500", "positions": "D1-01..D3-08", "slot_prefix": "", "reach": "room"},
    "reagent_shelf_a": {"furniture": "furniture.metro_super_erecta_1848_4shelf", "positions": "S1-01..S4-08", "slot_prefix": "", "reach": "room"},
}
