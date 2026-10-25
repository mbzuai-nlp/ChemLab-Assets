"""Revolved containers with a separate closure link: GL 45 reagent bottles, the 2.5 L solvent bottle, wide-mouth powder jars,
screw vials, conical screw-cap tubes (one builder) and the Safe-Lock snap-cap tube (hinged lid).

Each builder returns a LabwareAsset ready to export.  Documented values (catalogue OD / height / capacity / thread / cap size)
are used exactly; neck / shoulder proportions are photo estimates; wall thicknesses are assumptions (sources.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from labware_kit import (FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, LabwareAsset, Link, Prim, TOP_DOWN, fill_height, frame,
                         inner_volume_below, profile_volume, revolve, ring_boxes, top_down_frames)
from sources import pick

DENSITY = {"borosilicate_3_3": 2230.0, "soda_lime_glass": 2500.0, "pp": 900.0, "hdpe": 950.0, "ps": 1050.0, "pe": 950.0,
           "stainless_18_10": 7900.0, "steel": 7850.0, "brass": 8500.0, "tin": 7290.0, "aluminium": 2700.0, "fused_silica": 2200.0, "pmp": 830.0}
GLASS_CLEAR = (0.86, 0.93, 0.96, 0.35)
GLASS_AMBER = (0.55, 0.30, 0.08, 0.72)
PP_BLUE = (0.10, 0.32, 0.78, 1.0)
PP_BLACK = (0.08, 0.08, 0.09, 1.0)
HDPE_WHITE = (0.94, 0.94, 0.92, 1.0)
PP_CLEAR = (0.92, 0.93, 0.92, 0.55)
CAP_WHITE = (0.96, 0.96, 0.96, 1.0)
CAP_ORANGE = (0.95, 0.45, 0.10, 1.0)
CAP_SILVER = (0.70, 0.71, 0.74, 1.0)
FINGER_PAD_HALF_M = 0.009            # Franka finger pad ~18 mm tall along the approach axis (engineering estimate)


@dataclass
class ScrewCapSpec:
    slug: str
    type_id: str
    name_zh: str
    name_en: str
    family: str                      # reagent_bottle | solvent_bottle | powder_jar | vial | tube
    nominal_ml: float
    od_m: float                      # body outside diameter (documented)
    h_body_m: float                  # body height to the lip (documented or derived from the documented capped height)
    h_total_m: float                 # height with the cap on (documented or derived)
    neck_r_m: float                  # thread major radius
    neck_in_r_m: float               # neck inner radius (pour opening)
    thread_zone_m: float             # height of the threaded neck below the lip
    collar_r_m: float | None         # neck collar below the thread (None = none)
    collar_h_m: float
    shoulder_dzdr: float             # shoulder steepness (dz per dr), photo estimate
    wall_m: float
    bottom_m: float
    body_material: str
    body_rgba: tuple
    cap_od_m: float
    cap_h_m: float
    cap_top_m: float
    cap_material: str
    cap_rgba: tuple
    turns: float
    pitch_m: float | None
    thread_name: str
    bottom_shape: str = "flat"       # flat | conical | round
    cone_h_m: float = 0.0            # conical / round bottom height
    tip_r_m: float = 0.0
    sources: tuple = ()
    dims_source: str = ""            # source id for OD / height
    cap_source: str = ""
    nominal_source: str = ""
    labware_class: str = "bottle"
    storage_note_zh: str = ""
    extra_limitations: tuple = ()
    n_ring: int = 16
    stands_upright: bool = True
    notes_zh: str = ""
    fits: tuple = ()
    maker: str = ""
    model: str = ""
    catalogue_no: str = ""


def _profiles(s: ScrewCapSpec):
    """(outer, inner) (r, z) polylines; outer bottom-axis -> lip, inner lip -> bottom-axis."""
    R, H = s.od_m / 2, s.h_body_m
    t, tb = s.wall_m, s.bottom_m
    cap_z0 = s.h_total_m - s.cap_h_m
    z_thread0 = H - s.thread_zone_m                                  # threaded neck below the lip
    cr = s.collar_r_m if s.collar_r_m else s.neck_r_m
    z_c1 = min(z_thread0, cap_z0 - 0.0005)                          # collar / shoulder (wider than the thread) stay below the cap skirt
    z_c0 = z_c1 - (s.collar_h_m if s.collar_r_m else 0.0)
    z_s = z_c0 - s.shoulder_dzdr * max(R - cr, 0.0)                  # shoulder start (body top)
    f = min(0.004, R * 0.15)                                         # base fillet
    outer, inner = [], []
    if s.bottom_shape == "flat":
        outer += [(0.0, 0.0), (R - f, 0.0), (R, f)]
        inner_bottom = [(R - t, tb + f * 0.7), (R - t - f * 0.7, tb), (0.0, tb)]
    else:                                                            # conical / round: tip at z = 0
        ch = s.cone_h_m
        outer += [(0.0, 0.0), (s.tip_r_m, 0.0005)]
        if s.bottom_shape == "round":
            for k in range(1, 6):
                a = k / 6 * math.pi / 2
                outer.append((s.tip_r_m + (R - s.tip_r_m) * math.sin(a), ch * (1 - math.cos(a))))
        outer.append((R, ch))
        tip_in = max(s.tip_r_m - t, 0.0005)
        inner_bottom = [(R - t, ch + t * 0.5), (tip_in, tb), (0.0, tb)]
    if z_s > outer[-1][1] + 1e-4:
        outer.append((R, z_s))
    if s.collar_r_m:
        outer += [(cr, z_c0), (cr, z_c1)]
    outer += [(s.neck_r_m, z_c1), (s.neck_r_m, H)]
    # inner: lip -> down
    rin_body = R - t
    inner += [(s.neck_in_r_m, H), (s.neck_in_r_m, z_c0 if s.collar_r_m else z_c1)]
    z_s_in = z_s - t * 0.5
    if rin_body > s.neck_in_r_m + 1e-4:
        inner.append((rin_body, max(z_s_in, inner_bottom[0][1] + 1e-3)))
    inner += inner_bottom
    return outer, inner, dict(R=R, H=H, cap_z0=cap_z0, z_c0=z_c0, z_c1=z_c1, z_s=z_s, cr=cr, f=f)


def _ring_collision(outer, inner, s: ScrewCapSpec, geo: dict) -> list[Prim]:
    """Wall-ring boxes along the outer profile with the thickness reaching the inner profile, plus the bottom."""
    prims: list[Prim] = []
    R, n = geo["R"], s.n_ring
    t_body = max(s.wall_m, 0.0015)
    if s.bottom_shape == "flat":
        prims.append(Prim("cylinder", [R - geo["f"] * 0.3, s.bottom_m], [0.0, 0.0, s.bottom_m / 2]))
        z_body0 = geo["f"] * 0.5
    else:
        # cone: rings from the tip to the cone top
        prims.append(Prim("cylinder", [max(s.tip_r_m, 0.002), max(s.bottom_m, 0.0015)], [0.0, 0.0, max(s.bottom_m, 0.0015) / 2]))
        prims += ring_boxes(max(s.tip_r_m, 0.002), 0.0008, R, s.cone_h_m, t_body, n)
        z_body0 = s.cone_h_m
    z_s = geo["z_s"]
    if z_s > z_body0 + 1e-3:
        prims += ring_boxes(R, z_body0, R, z_s, t_body, n)
    cr = geo["cr"]
    z_neck0 = geo["z_c0"] if s.collar_r_m else geo["z_c1"]
    if R - cr > 1e-4:                                                 # shoulder
        prims += ring_boxes(R, z_s, cr, z_neck0, max(t_body, 0.002), n)
    if s.collar_r_m:
        prims += ring_boxes(cr, geo["z_c0"], cr, geo["z_c1"], cr - s.neck_in_r_m, n)
    prims += ring_boxes(s.neck_r_m, geo["z_c1"], s.neck_r_m, geo["H"], s.neck_r_m - s.neck_in_r_m, n)
    return prims


def _cap_meshes(s: ScrewCapSpec):
    ro, h, top = s.cap_od_m / 2, s.cap_h_m, s.cap_top_m
    w = 2 * math.pi * s.neck_r_m / s.n_ring * 1.08                    # neck ring-box width; its corners reach beyond neck_r
    ri = math.hypot(s.neck_r_m, w / 2) + 0.0003
    prof = [(0.0, h - top), (ri, h - top), (ri, 0.0), (ro, 0.0), (ro, h), (0.0, h)]
    vis = revolve(prof, sections=48)
    col = [Prim("cylinder", [ro, top], [0.0, 0.0, h - top / 2])]
    col += ring_boxes(ro, 0.0, ro, h - top, ro - ri, max(12, s.n_ring))
    return vis, col, ri


def _outer_radius_at(outer, z):
    pts = list(outer)
    best = 0.0
    for (r0, z0), (r1, z1) in zip(pts[:-1], pts[1:]):
        lo, hi = min(z0, z1), max(z0, z1)
        if lo - 1e-9 <= z <= hi + 1e-9:
            r = r0 if abs(z1 - z0) < 1e-12 else r0 + (r1 - r0) * (z - z0) / (z1 - z0)
            best = max(best, r, max(r0, r1) if abs(z1 - z0) < 1e-12 else 0.0)
    return best


def build_screwcap(s: ScrewCapSpec) -> LabwareAsset:
    outer, inner, geo = _profiles(s)
    body_mesh = revolve(outer + inner, sections=64)
    cap_vis, cap_col, cap_ri = _cap_meshes(s)
    cap_z0 = geo["cap_z0"]
    assert cap_ri - s.neck_r_m > 0, "cap inner radius must clear the thread"
    assert cap_z0 + s.cap_h_m - s.cap_top_m >= s.h_body_m - 1e-6, f"{s.slug}: cap top must clear the lip"
    body_vol = body_mesh.volume
    body_mass = body_vol * DENSITY[s.body_material]
    cap_mass = cap_vis.volume * DENSITY[s.cap_material]
    src = pick(*s.sources)
    a = LabwareAsset(s.slug, s.type_id, s.name_zh, s.name_en, "containers", f"labware.container.{s.family}", "passive",
                     description_zh=s.notes_zh, sources=src)
    body_col = _ring_collision(outer, inner, s, geo)
    body = a.add(Link("body", "瓶身" if s.family != "tube" else "管身", "盛放试剂/样品；提供抓取部位、倾倒口与螺纹颈",
                      f"{s.body_material} 壳体；外径 {s.od_m*1000:.1f} mm、本体高 {s.h_body_m*1000:.1f} mm；内腔为 LiquidContents 的计数区",
                      visual=[(body_mesh, s.body_rgba)], collision=body_col,
                      solid_collision=[Prim("cylinder", [geo["R"], s.h_body_m], [0.0, 0.0, s.h_body_m / 2])],
                      mass_kg=body_mass, mass_evidence="assumption",
                      mass_note_zh=f"由建模壁厚体积 {body_vol*1e6:.1f} cm3 × {s.body_material} 密度 {DENSITY[s.body_material]:.0f} kg/m3 计算",
                      material=s.body_material, geometry_evidence="documented",
                      geometry_note_zh="外径与高度按目录值；肩部/颈圈比例按照片估计；壁厚为假设"))
    turns_rad = 2 * math.pi * s.turns
    cap = a.add(Link("cap", "螺旋盖", "密封瓶口；拧松后整体取下，可放回再拧紧",
                     f"{s.cap_material} 螺旋盖 Ø{s.cap_od_m*1000:.1f}×{s.cap_h_m*1000:.1f} mm；{s.thread_name} 螺纹",
                     parent="body", origin_m=[0.0, 0.0, cap_z0],
                     joint={"type": "revolute", "axis": [0, 0, 1], "limits": [0.0, round(turns_rad, 6)], "evidence": "assumption",
                            "source_ids": ["closure_turns"],
                            "note_zh": f"正方向 = 俯视逆时针 = 拧松；累计 {s.turns} 圈（{turns_rad:.3f} rad）后盖脱离螺纹（touch + intent：夹住盖 + 腕部转动即记账，不靠接触物理拧出）"},
                     visual=[(cap_vis, s.cap_rgba)], collision=cap_col,
                     solid_collision=[Prim("cylinder", [s.cap_od_m / 2, s.cap_h_m], [0.0, 0.0, s.cap_h_m / 2])],
                     mass_kg=cap_mass, mass_evidence="assumption",
                     mass_note_zh=f"由建模体积 {cap_vis.volume*1e6:.2f} cm3 × {s.cap_material} 密度计算",
                     material=s.cap_material, geometry_evidence="documented", detachable=True,
                     geometry_note_zh="外径与高度按目录值；顶厚/裙厚为假设"))
    # ---------------------------------------------------------------- labware data
    interior = [(r, z) for r, z in reversed(inner)]                  # bottom -> lip
    brim = profile_volume(interior + [(0.0, s.h_body_m)])
    nominal = s.nominal_ml * 1e-6
    vol_table = [[round(h, 5), round(inner_volume_below(interior, h) * 1e6, 3)] for h in np.linspace(s.bottom_m, s.h_body_m, 21)]
    top = s.h_total_m
    # top-down grasp: palm (37 mm above TCP) must clear the cap top; fingers on the neck / shoulder / body
    z_g = top - HAND_ABOVE_TCP_M + 0.004
    z_g = min(z_g, top - 0.006)
    span = (z_g - FINGER_PAD_HALF_M, z_g + FINGER_PAD_HALF_M)
    widths = []
    for z in np.linspace(max(span[0], 0.0), span[1], 19):
        r = _outer_radius_at(outer, z)
        if cap_z0 <= z <= top:
            r = max(r, s.cap_od_m / 2)
        widths.append(2 * r)
    width = max(widths)
    grasp_note = "俯抓瓶颈/肩部（手掌碰撞面在 TCP 上方 37 mm，须高于盖顶）；带盖与开盖后都可用"
    body_grasp_ok = width <= FRANKA_MAX_GRASP_M
    if not body_grasp_ok and s.cap_od_m <= FRANKA_MAX_GRASP_M:
        z_g, width = cap_z0 + s.cap_h_m / 2, s.cap_od_m        # carry by the (screwed-on) cap: the body is wider than the hand opens
        span = (z_g - FINGER_PAD_HALF_M, z_g + FINGER_PAD_HALF_M)
        grasp_note = f"瓶身外径 {s.od_m*1000:.0f} mm 超出 Franka 可抓宽度（≤74 mm）：只能在盖拧紧时抓盖搬运；开盖后瓶身不可由 Franka 抓取"
    frames = top_down_frames(z_g, top, width, yaw_free=True, contact_z=span, note_zh=grasp_note)
    frames["grasp"]["holds"] = "body" if body_grasp_ok else "cap_closed_only"
    cap_mid = cap_z0 + s.cap_h_m / 2
    frames["cap_grasp"] = frame([0, 0, cap_mid], "base_link", width_m=round(s.cap_od_m, 5),
                                franka_ok=bool(s.cap_od_m <= FRANKA_MAX_GRASP_M), yaw_free=True,
                                note_zh="盖中心高度；夹住后绕 +Z 正向转动 = 拧松")
    frames["cap_grasp_local"] = frame([0, 0, s.cap_h_m / 2], "cap", note_zh="同一抓取点，用盖自身坐标表示（盖已取下时）")
    frames["cap_pre_grasp"] = frame([0, 0, top + 0.08], "base_link")
    frames["cap_seat"] = frame([0, 0, cap_z0], "body", rot=[0.0, 0.0, 0.0, 1.0],
                               note_zh="盖 link 原点（盖底中心）拧紧时在瓶身坐标中的位姿；重新盖上 = 盖原点对准此位姿后反向转动")
    frames["pour_lip"] = frame([s.neck_r_m, 0.0, s.h_body_m], "body", rot=[0.0, 0.0, 0.0, 1.0],
                               note_zh="+X 侧瓶口外缘；倾倒时最低点（Pour 原语按此计算越过目标杯沿的高度）")
    a.labware = {
        "type_id": s.type_id, "family": s.family, "labware_class": s.labware_class, "fits": list(s.fits or (s.labware_class,)),
        "maker": s.maker, "model": s.model, "catalogue_no": s.catalogue_no,
        "nominal_capacity_m3": nominal, "brim_capacity_m3": round(brim, 9),
        "nominal_capacity_evidence": "documented", "nominal_source_id": s.nominal_source or s.dims_source,
        "outer": {"diameter_m": s.od_m, "height_body_m": s.h_body_m, "height_with_cap_m": s.h_total_m, "source_id": s.dims_source},
        "interior": {"shape": "revolved", "radius_m": round(geo["R"] - s.wall_m, 5), "floor_m": s.bottom_m, "height_m": s.h_body_m,
                     "body_top_m": round(geo["z_s"], 5), "neck_radius_m": s.neck_in_r_m,
                     "nominal_fill_height_m": round(fill_height(interior, nominal), 5) if nominal < brim else None,
                     "volume_table_m_ml": vol_table, "profile_rz_m": [[round(r, 5), round(z, 5)] for r, z in interior],
                     "note_zh": "LiquidContents 计数圆柱可取 radius_m / floor_m / height_m；精确液位用 volume_table（高度 m → 体积 mL）"},
        "closure": {"type": "screw_cap", "thread": s.thread_name, "link": "cap", "joint": "cap_joint", "axis": [0, 0, 1],
                    "unscrew_sign": 1, "turns": s.turns, "pitch_m": s.pitch_m, "detach_after_rad": round(turns_rad, 6),
                    "lift_at_detach_m": round(s.turns * (s.pitch_m or 0.0), 5), "cap_seat_z_m": round(cap_z0, 5),
                    "clear_lift_m": round(s.h_body_m - cap_z0 + 0.002, 5),
                    "rule_zh": "开盖 = 夹住盖（cap_grasp）+ 腕部绕盖轴正向累计转动 ≥ detach_after_rad → 盖与瓶身分离为独立刚体（parts/cap.urdf），"
                               "盖可放到台面；盖回 = 盖对准 cap_seat + 反向转动同样角度 → 重新固连。容器需由存放位/夹具固定（单臂）。",
                    "parts": {"body": "parts/body.urdf", "cap": "parts/cap.urdf"}},
        "frames": frames,
        "materials": {"body": s.body_material, "cap": s.cap_material},
        "empty_mass_kg": round(body_mass + cap_mass, 5), "mass_body_kg": round(body_mass, 5), "mass_cap_kg": round(cap_mass, 5),
        "mass_evidence": "computed from modelled wall volume x material density (wall thickness assumed)",
        "stands_upright": s.stands_upright, "franka_grasp_ok": bool(width <= FRANKA_MAX_GRASP_M),
        "storage_note_zh": s.storage_note_zh,
    }
    # ---------------------------------------------------------------- contract data
    sid = s.dims_source
    a.prop("body", "outer_diameter_m", s.od_m, "m", "瓶身外径（目录）", "documented", [sid])
    a.prop("body", "height_with_cap_m", s.h_total_m, "m", "带盖总高（目录或由目录推得）", "documented", [sid])
    a.prop("body", "nominal_capacity_m3", nominal, "m3", "标称容量（目录）", "documented", [s.nominal_source or sid])
    a.prop("body", "brim_capacity_m3", round(brim, 9), "m3", "建模内腔到瓶口的满口容量（由壁厚假设计算）", "assumption", ["wall_assumption"])
    a.prop("body", "wall_thickness_m", s.wall_m, "m", "壁厚（假设，厂家不公布）", "assumption", ["wall_assumption"])
    a.prop("cap", "outer_diameter_m", s.cap_od_m, "m", "盖外径", "documented" if s.cap_source else "assumption",
           [s.cap_source] if s.cap_source else ["wall_assumption"])
    a.prop("cap", "release_turns", s.turns, "1", "拧下所需圈数", "assumption", ["closure_turns"])
    a.state_slots = {"closure.state": {
        "component_ids": ["cap"], "value_schema": {"type": "string", "enum": ["closed", "loosened", "detached"]}, "unit": "1",
        "reference_conditions": None, "meaning_zh": "盖的场景状态：拧紧 / 已拧松未脱离 / 已取下（由场景或宿主按 closure.rule 记录）",
        "origin": "scene", "access": "read_only", "visibility": "agent", "read_binding": None, "write_via": None,
        "sampling": {"mode": "instant", "time_domain": "simulation", "window_description": None}, "source_ids": ["closure_turns"]}}
    a.mechanisms = {"closure.thread": {
        "component_ids": ["body", "cap"], "state_ids": ["closure.state"],
        "meaning_zh": "螺旋盖的开合规则（touch + intent）：夹住盖并转动的累计角度达到阈值即脱离；反向同角度即拧紧。不模拟螺纹接触力。",
        "implementation": "qualitative",
        "equations": ["state = detached if cumulative_unscrew_rad >= detach_after_rad (cap grasped)",
                      "state = closed if cap at cap_seat and cumulative_screw_rad >= detach_after_rad"],
        "parameters": {"detach_after_rad": {"value": round(turns_rad, 6), "unit": "rad", "meaning_zh": "拧下所需累计转角", "evidence": "assumption",
                                            "source_ids": ["closure_turns"], "uncertainty": "±0.5 圈"},
                       "thread_pitch_m": {"value": s.pitch_m, "unit": "m", "meaning_zh": "螺距", "evidence": "documented" if s.thread_name == "GL45" else "assumption",
                                          "source_ids": ["gl45_thread"] if s.thread_name == "GL45" else ["closure_turns"], "uncertainty": None}},
        "source_ids": ["closure_turns"], "limitations": ["不计螺纹摩擦、密封圈与扭矩；单臂开盖要求容器被存放位或夹具固定。"]}}
    a.contract_ports = {"mouth": {"component_id": "body", "medium": "liquid", "direction": "bidirectional", "frame": "frame:pour_lip",
                                  "meaning_zh": "瓶口：倒出/加入液体或粉末（开盖后）",
                                  "connection_constraints": ["盖必须为 detached 状态才能倒出或加入", "倾倒属于机器人技能，转移量由宿主账本记录"]}}
    a.limitations_zh = [
        "被动容器：没有主动硬件 API；开盖、倾倒、搬运属于机器人技能，内容物由宿主物质账本记录。",
        "外径/高度/容量/螺纹/盖尺寸为目录值；肩部与颈圈比例为照片估计；壁厚与底厚为假设，空瓶质量由此计算。",
        "开盖用 touch + intent 规则（closure.thread），不模拟螺纹接触；拧下后盖作为 parts/cap.urdf 独立刚体。",
        "model.urdf 的瓶身碰撞为壁环盒 + 底盘（可装粒子液体）；只记账的存放容器可用 model_solid.urdf。",
    ] + list(s.extra_limitations)
    return a


# ======================================================================================================== specs
def gl45_spec(ml: int, amber: bool) -> ScrewCapSpec:
    table = {100: (0.056, 0.105), 250: (0.070, 0.143), 500: (0.086, 0.181), 1000: (0.101, 0.230)}   # OD, height with cap
    od, hc = table[ml]
    colour = "amber" if amber else "clear"
    return ScrewCapSpec(
        slug=f"duran_gl45_{ml}ml_{colour}", type_id=f"labware.duran_gl45_{ml}ml_{colour}",
        name_zh=f"DURAN GL45 试剂瓶 {ml} mL（{'棕色' if amber else '透明'}，蓝色 PP 螺旋盖）",
        name_en=f"DURAN GL 45 laboratory bottle {ml} mL, {colour}, PP screw cap",
        family="reagent_bottle", nominal_ml=ml, od_m=od, h_body_m=round(hc - 0.005, 4), h_total_m=hc,
        neck_r_m=0.0225, neck_in_r_m=0.0170, thread_zone_m=0.020, collar_r_m=0.0250, collar_h_m=0.006, shoulder_dzdr=1.3,
        wall_m=0.0025 if ml >= 250 else 0.0022, bottom_m=0.004, body_material="borosilicate_3_3",
        body_rgba=GLASS_AMBER if amber else GLASS_CLEAR, cap_od_m=0.058, cap_h_m=0.027, cap_top_m=0.003, cap_material="pp",
        cap_rgba=PP_BLUE, turns=1.25, pitch_m=0.004, thread_name="GL45",
        sources=("duran_gl45_amber", "duran_gl45_clear", "gl45_thread", "duran_gl45_cap", "iso3585", "pp_hdpe_density",
                 "wall_assumption", "neck_profile_estimate", "closure_turns", "robot_access") if amber else
                ("duran_gl45_clear", "gl45_thread", "duran_gl45_cap", "iso3585", "pp_hdpe_density", "wall_assumption",
                 "neck_profile_estimate", "closure_turns", "robot_access"),
        dims_source="duran_gl45_amber" if amber else "duran_gl45_clear", cap_source="duran_gl45_cap",
        labware_class="bottle", fits=("bottle", "gl45_bottle"),
        storage_note_zh="试剂/缓冲液/标准溶液；棕色瓶用于见光分解物质" if amber else "试剂/缓冲液/标准溶液/溶剂",
        maker="DWK Life Sciences (DURAN)", model=f"DURAN {'GL 45 laboratory bottle, amber' if amber else 'Original GL 45 laboratory bottle, clear'}, {ml} mL, PP screw cap + pouring ring",
        catalogue_no={100: "21801 24 5 / 21806 24 5", 250: "21801 36 5 / 21806 36 5", 500: "21801 44 5 / 21806 44 5", 1000: "21801 54 5 / 21806 54 5"}[ml].split(" / ")[1 if amber else 0],
        notes_zh=f"DURAN Original GL 45 试剂瓶 {ml} mL（{colour}），带蓝色 PP 螺旋盖；外径 {od*1000:.0f} mm、带盖高 {hc*1000:.0f} mm（目录）。"
                 "盖为独立 link（俯视逆时针 1.25 圈拧下），抓取坐标在瓶颈/肩部（Franka ≤ 74 mm）。",
    )


def winchester_25l_spec() -> ScrewCapSpec:
    return ScrewCapSpec(
        slug="winchester_2500ml_amber", type_id="labware.winchester_2500ml_amber",
        name_zh="2.5 L 棕色玻璃溶剂瓶（Winchester，黑色内衬螺旋盖）", name_en="Winchester solvent bottle 2.5 L, amber glass",
        family="solvent_bottle", nominal_ml=2500, od_m=0.1329, h_body_m=0.292, h_total_m=0.297,
        neck_r_m=0.0190, neck_in_r_m=0.0140, thread_zone_m=0.016, collar_r_m=0.0215, collar_h_m=0.006, shoulder_dzdr=0.9,
        wall_m=0.0035, bottom_m=0.006, body_material="soda_lime_glass", body_rgba=GLASS_AMBER,
        cap_od_m=0.042, cap_h_m=0.022, cap_top_m=0.003, cap_material="pp", cap_rgba=PP_BLACK, turns=1.0, pitch_m=0.0042,
        thread_name="38 mm (SPI 400-type, assumed)",
        sources=("winchester_25l", "pp_hdpe_density", "wall_assumption", "neck_profile_estimate", "closure_turns", "robot_access"),
        dims_source="winchester_25l", cap_source="", labware_class="bottle", fits=("bottle", "solvent_bottle"),
        storage_note_zh="2.5 L 溶剂（乙醇、丙酮、甲醇等易燃液体放易燃品柜）",
        maker="Philip Harris (Findel Education)", model="Winchester bottle, amber glass, 2.5 L", catalogue_no="FE00001092",
        extra_limitations=("瓶口螺纹与盖尺寸（38 mm、Ø42×22 mm）为假设：清单只给出高 297 mm、宽 132.9 mm；玻璃按钠钙玻璃 2500 kg/m3。",
                           "满瓶约 2.5 kg 液体 + 瓶重，超出单手倾倒的舒适范围；Franka 负载 3 kg 以内可搬运但倾倒需慢速。"),
        notes_zh="2.5 L 棕色 Winchester 溶剂瓶：高 297 mm、宽 132.9 mm（Philip Harris FE00001092）；抓取点在瓶颈。",
    )


def nalgene_jar_spec(ml: int) -> ScrewCapSpec:
    if ml == 250:
        return ScrewCapSpec(
            slug="nalgene_wm_hdpe_250ml", type_id="labware.nalgene_wm_hdpe_250ml",
            name_zh="Nalgene 广口 HDPE 粉末瓶 250 mL（43 mm 螺旋盖）", name_en="Nalgene wide-mouth HDPE bottle 250 mL (powder jar)",
            family="powder_jar", nominal_ml=250, od_m=0.061, h_body_m=0.118, h_total_m=0.131,
            neck_r_m=0.0205, neck_in_r_m=0.0165, thread_zone_m=0.012, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.8,
            wall_m=0.0012, bottom_m=0.0015, body_material="hdpe", body_rgba=HDPE_WHITE,
            cap_od_m=0.0465, cap_h_m=0.017, cap_top_m=0.0025, cap_material="pp", cap_rgba=CAP_WHITE, turns=2.0, pitch_m=0.0042,
            thread_name="43-415", sources=("nalgene_wm_250", "pp_hdpe_density", "wall_assumption", "neck_profile_estimate", "closure_turns", "robot_access"),
            dims_source="nalgene_wm_250", cap_source="", labware_class="jar", fits=("jar", "powder_jar"),
            storage_note_zh="固体试剂（盐、标准物质粉末）", n_ring=16,
            maker="Thermo Scientific (Nalgene)", model="Wide-mouth HDPE packaging bottle 250 mL, 43-415 closure", catalogue_no="312189-0008",
            extra_limitations=("盖外径 46.5 mm、高 17 mm 为假设（43-415 盖的典型尺寸）；瓶身高 118 mm 由总高 131 mm 减盖露出部分推得。",),
            notes_zh="Nalgene 312189-0008 广口 HDPE 瓶：外径 61 mm、高 131 mm、瓶口内径 33 mm、43-415 盖；用作粉末瓶。")
    return ScrewCapSpec(
        slug="nalgene_wm_hdpe_1000ml", type_id="labware.nalgene_wm_hdpe_1000ml",
        name_zh="Nalgene 广口 HDPE 粉末瓶 1 L（63 mm 螺旋盖）", name_en="Nalgene wide-mouth HDPE bottle 1 L (powder jar)",
        family="powder_jar", nominal_ml=1000, od_m=0.091, h_body_m=0.188, h_total_m=0.199,
        neck_r_m=0.0315, neck_in_r_m=0.0275, thread_zone_m=0.012, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.6,
        wall_m=0.0016, bottom_m=0.002, body_material="hdpe", body_rgba=HDPE_WHITE,
        cap_od_m=0.068, cap_h_m=0.020, cap_top_m=0.003, cap_material="pp", cap_rgba=CAP_WHITE, turns=1.0, pitch_m=0.0042,
        thread_name="63 mm (SPI 400-type, assumed)",
        sources=("nalgene_wm_1000", "pp_hdpe_density", "wall_assumption", "neck_profile_estimate", "closure_turns", "robot_access"),
        dims_source="nalgene_wm_1000", cap_source="", labware_class="jar", fits=("jar", "powder_jar"),
        storage_note_zh="大包装固体试剂", n_ring=16,
        maker="Thermo Scientific (Nalgene)", model="Wide-mouth HDPE packaging bottle 1000 mL, 63 mm closure", catalogue_no="312189-0032",
        extra_limitations=("盖外径 68 mm、高 20 mm 与螺纹类型为假设；瓶身高 188 mm 由总高 199 mm 推得（盖露出 11 mm）。",),
        notes_zh="Nalgene 312189-0032 广口 HDPE 1 L 瓶：外径 91 mm、高 199 mm、63 mm 盖；瓶身 91 mm 超出 Franka 张开宽度，只能在盖拧紧时抓盖搬运。")


def vial_spec(kind: str) -> ScrewCapSpec:
    if kind == "2ml":
        return ScrewCapSpec(
            slug="agilent_vial_2ml_screw", type_id="labware.agilent_vial_2ml_screw",
            name_zh="Agilent 2 mL 螺口样品瓶（12×32 mm，9 mm 螺纹盖）", name_en="Agilent 5182-0715 2 mL screw-top vial 12 x 32 mm",
            family="vial", nominal_ml=2.0, od_m=0.0116, h_body_m=0.0305, h_total_m=0.032,
            neck_r_m=0.0047, neck_in_r_m=0.0033, thread_zone_m=0.0045, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.5,
            wall_m=0.0007, bottom_m=0.0010, body_material="borosilicate_3_3", body_rgba=GLASS_CLEAR,
            cap_od_m=0.0118, cap_h_m=0.0065, cap_top_m=0.0012, cap_material="pp", cap_rgba=PP_BLUE, turns=1.0, pitch_m=0.0015,
            thread_name="9 mm short thread", sources=("agilent_5182_0715", "iso3585", "pp_hdpe_density", "wall_assumption", "closure_turns", "robot_access"),
            dims_source="agilent_5182_0715", cap_source="agilent_5182_0715", labware_class="vial", fits=("vial", "vial_12x32"),
            storage_note_zh="自动进样瓶；装样后放样品架或仪器托盘", n_ring=12,
            maker="Agilent", model="Screw top vial 2 mL, clear, 12 x 32 mm", catalogue_no="5182-0715",
            extra_limitations=("12×32 mm 为带盖外形；瓶身外径 11.6 mm、盖高 6.5 mm、颈部尺寸为按标准 9 mm 短螺纹瓶的假设。",),
            notes_zh="Agilent 5182-0715 2 mL 螺口样品瓶，12×32 mm、12 mm 盖（9 mm 螺纹）。与 Matterix 现有 GC 样品瓶（chemsim/sample_vial）不同：这是螺口版本。")
    return ScrewCapSpec(
        slug="agilent_hs_vial_20ml", type_id="labware.agilent_hs_vial_20ml",
        name_zh="Agilent 20 mL 螺口顶空瓶（22.75×75 mm，18 mm 磁性螺旋盖）", name_en="Agilent 5188-2753 20 mL screw-top headspace vial",
        family="vial", nominal_ml=20.0, od_m=0.02275, h_body_m=0.075, h_total_m=0.0775,
        neck_r_m=0.0090, neck_in_r_m=0.0063, thread_zone_m=0.008, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.7,
        wall_m=0.0012, bottom_m=0.002, body_material="borosilicate_3_3", body_rgba=GLASS_CLEAR,
        cap_od_m=0.0205, cap_h_m=0.0105, cap_top_m=0.0015, cap_material="steel", cap_rgba=CAP_SILVER, turns=1.0, pitch_m=0.0021,
        thread_name="18 mm screw", bottom_shape="round", cone_h_m=0.004, tip_r_m=0.008,
        sources=("agilent_5188_2753", "iso3585", "wall_assumption", "closure_turns", "robot_access"),
        dims_source="agilent_5188_2753", cap_source="", labware_class="vial", fits=("vial", "headspace_vial_20ml"),
        storage_note_zh="顶空进样瓶（HS 8697）", n_ring=12,
        maker="Agilent", model="Headspace vial, screw top, 20 mL, 22.75 x 75 mm", catalogue_no="5188-2753",
        extra_limitations=("盖外径 20.5 mm、高 10.5 mm（磁性钢盖 + 隔垫）与颈部尺寸为假设；22.75×75 mm 为瓶身（目录）。",),
        notes_zh="Agilent 5188-2753 20 mL 螺口顶空瓶：22.75×75 mm、18 mm 螺旋盖、圆底。")


def falcon_spec(ml: int) -> ScrewCapSpec:
    if ml == 15:
        return ScrewCapSpec(
            slug="falcon_tube_15ml", type_id="labware.falcon_tube_15ml",
            name_zh="Falcon 15 mL 锥底离心管（352096，PE 螺旋盖）", name_en="Corning Falcon 352096 15 mL conical tube",
            family="tube", nominal_ml=15.0, od_m=0.017, h_body_m=0.1105, h_total_m=0.120,
            neck_r_m=0.0083, neck_in_r_m=0.0072, thread_zone_m=0.009, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.5,
            wall_m=0.0009, bottom_m=0.0012, body_material="pp", body_rgba=PP_CLEAR,
            cap_od_m=0.0205, cap_h_m=0.0125, cap_top_m=0.0015, cap_material="pe", cap_rgba=CAP_WHITE, turns=1.5, pitch_m=0.003,
            thread_name="Falcon 15 mL", bottom_shape="conical", cone_h_m=0.022, tip_r_m=0.0015,
            sources=("falcon_352096", "pp_hdpe_density", "wall_assumption", "closure_turns", "robot_access"),
            dims_source="falcon_352096", cap_source="", labware_class="tube", fits=("tube", "conical_15ml"),
            storage_note_zh="锥底管不能自立：放在管架中", n_ring=12, stands_upright=False,
            maker="Corning (Falcon)", model="15 mL PP conical centrifuge tube", catalogue_no="352096",
            extra_limitations=("17×120 mm 按带盖外形；锥底高 22 mm、盖 Ø20.5×12.5 mm 为照片估计。", "锥底管不能自立，需要管架（未建模；可用 LabUtopia test_tube_rack 或专用架）。"),
            notes_zh="Corning Falcon 352096 15 mL PP 锥底离心管，约 17 mm 外径 × 120 mm 长。")
    return ScrewCapSpec(
        slug="falcon_tube_50ml", type_id="labware.falcon_tube_50ml",
        name_zh="Falcon 50 mL 锥底离心管（352070，螺旋盖）", name_en="Corning Falcon 352070 50 mL conical tube",
        family="tube", nominal_ml=50.0, od_m=0.0295, h_body_m=0.1035, h_total_m=0.115,
        neck_r_m=0.0148, neck_in_r_m=0.0136, thread_zone_m=0.010, collar_r_m=None, collar_h_m=0.0, shoulder_dzdr=0.5,
        wall_m=0.0011, bottom_m=0.0014, body_material="pp", body_rgba=PP_CLEAR,
        cap_od_m=0.0345, cap_h_m=0.0135, cap_top_m=0.0016, cap_material="pe", cap_rgba=CAP_WHITE, turns=1.5, pitch_m=0.0035,
        thread_name="Falcon 50 mL", bottom_shape="conical", cone_h_m=0.020, tip_r_m=0.002,
        sources=("falcon_352070", "pp_hdpe_density", "wall_assumption", "closure_turns", "robot_access"),
        dims_source="falcon_352070", cap_source="", labware_class="tube", fits=("tube", "conical_50ml"),
        storage_note_zh="锥底管不能自立：放在管架中", n_ring=14, stands_upright=False,
        maker="Corning (Falcon)", model="50 mL PP conical centrifuge tube", catalogue_no="352070",
        extra_limitations=("30×115 mm 按带盖外形（管身外径取 29.5 mm）；锥底高 20 mm、盖 Ø34.5×13.5 mm 为照片估计。", "锥底管不能自立，需要管架。"),
        notes_zh="Corning Falcon 352070 50 mL PP 锥底离心管，约 30 mm 外径 × 115 mm 长。")


def nalgene_hdpe_spec(kind: str) -> ScrewCapSpec:
    """Narrow-mouth HDPE reagent bottles 500 mL / 1 L and the 500 mL wide-mouth jar (inventory types bottle_hdpe_500ml/_1000ml, jar_hdpe_500g)."""
    common = dict(family="reagent_bottle", wall_m=0.0014, bottom_m=0.0018, body_material="hdpe", body_rgba=HDPE_WHITE, cap_material="pp",
                  cap_rgba=CAP_WHITE, collar_r_m=None, collar_h_m=0.0, labware_class="bottle", n_ring=16, maker="Thermo Scientific (Nalgene)")
    if kind == "nm500":
        return ScrewCapSpec(slug="nalgene_nm_hdpe_500ml", type_id="labware.nalgene_nm_hdpe_500ml", name_zh="Nalgene 窄口 HDPE 试剂瓶 500 mL（28 mm 盖）",
                            name_en="Nalgene narrow-mouth HDPE bottle 500 mL", nominal_ml=500, od_m=0.073, h_body_m=0.160, h_total_m=0.170,
                            neck_r_m=0.0140, neck_in_r_m=0.0105, thread_zone_m=0.011, shoulder_dzdr=0.9, cap_od_m=0.0315, cap_h_m=0.016, cap_top_m=0.0025,
                            turns=1.0, pitch_m=0.0042, thread_name="28 mm (SPI 400-type, assumed)",
                            sources=("nalgene_nm_500", "pp_hdpe_density", "wall_assumption", "neck_profile_estimate", "closure_turns", "robot_access"),
                            dims_source="nalgene_nm_500", fits=("bottle", "hdpe_bottle"), storage_note_zh="水溶液、碱液（HDPE 耐碱）", model="Narrow-mouth HDPE lab quality bottle 500 mL, 28 mm PP closure",
                            catalogue_no="2002-0016", extra_limitations=("盖外径 31.5 mm、高 16 mm 为 28 mm 盖的典型尺寸（假设）；瓶身高 160 mm 由总高 170 mm 推得。",),
                            notes_zh="Nalgene 2002-0016 窄口 HDPE 瓶 500 mL：外径 73 mm、高 170 mm、瓶口内径 21 mm、28 mm 盖。", **common)
    if kind == "nm1000":
        return ScrewCapSpec(slug="nalgene_nm_hdpe_1000ml", type_id="labware.nalgene_nm_hdpe_1000ml", name_zh="Nalgene 窄口 HDPE 试剂瓶 1 L（38-430 盖）",
                            name_en="Nalgene narrow-mouth HDPE bottle 1000 mL", nominal_ml=1000, od_m=0.092, h_body_m=0.205, h_total_m=0.217,
                            neck_r_m=0.0185, neck_in_r_m=0.0135, thread_zone_m=0.012, shoulder_dzdr=0.9, cap_od_m=0.042, cap_h_m=0.019, cap_top_m=0.003,
                            turns=1.0, pitch_m=0.0042, thread_name="38-430",
                            sources=("nalgene_nm_1000", "pp_hdpe_density", "wall_assumption", "neck_profile_estimate", "closure_turns", "robot_access"),
                            dims_source="nalgene_nm_1000", fits=("bottle", "hdpe_bottle"), storage_note_zh="水溶液、碱液", model="Narrow-mouth natural HDPE packaging bottle 1000 mL, 38-430 closure",
                            catalogue_no="312089-0032", extra_limitations=("盖外径 42 mm、高 19 mm 为 38-430 盖的典型尺寸（假设）；瓶身高 205 mm 由总高 217 mm 推得。",),
                            notes_zh="Nalgene 312089-0032 窄口 HDPE 1 L 瓶：外径 92 mm、高 217 mm、瓶口内径 27 mm、38-430 盖；抓取点在瓶颈。", **common)
    common["family"], common["labware_class"] = "powder_jar", "jar"
    return ScrewCapSpec(slug="nalgene_wm_hdpe_500ml", type_id="labware.nalgene_wm_hdpe_500ml", name_zh="Nalgene 广口 HDPE 粉末瓶 500 mL（53 mm 盖）",
                        name_en="Nalgene wide-mouth HDPE bottle 500 mL (powder jar)", nominal_ml=500, od_m=0.073, h_body_m=0.155, h_total_m=0.168,
                        neck_r_m=0.0255, neck_in_r_m=0.0220, thread_zone_m=0.012, shoulder_dzdr=0.8, cap_od_m=0.057, cap_h_m=0.018, cap_top_m=0.0028,
                        turns=2.0, pitch_m=0.0042, thread_name="53-415",
                        sources=("nalgene_wm_500", "pp_hdpe_density", "wall_assumption", "neck_profile_estimate", "closure_turns", "robot_access"),
                        dims_source="nalgene_wm_500", fits=("jar", "powder_jar"), storage_note_zh="约 500 g 固体试剂", model="Wide-mouth HDPE packaging bottle 500 mL, 53-415 closure",
                        catalogue_no="312189-0016", extra_limitations=("盖外径 57 mm、高 18 mm 为 53-415 盖的典型尺寸（假设）；瓶身高 155 mm 由总高 168 mm 推得。",),
                        notes_zh="Nalgene 312189-0016 广口 HDPE 瓶 500 mL：外径 73 mm、高 168 mm、瓶口内径 44 mm、53-415 盖；用作 500 g 粉末瓶。", **common)


# ------------------------------------------------------------------------------------------------ Safe-Lock tube
def build_safelock_15() -> LabwareAsset:
    """Eppendorf Safe-Lock 1.5 mL (drawing p.6): hinged lid on a revolute joint (opens 90 deg per the drawing)."""
    src = pick("eppendorf_safelock_15", "pp_hdpe_density", "wall_assumption", "robot_access")
    a = LabwareAsset("eppendorf_safelock_1p5ml", "labware.eppendorf_safelock_1p5ml", "Eppendorf Safe-Lock 1.5 mL 微量离心管（翻盖）",
                     "Eppendorf Safe-Lock Tube 1.5 mL", "containers", "labware.container.tube", "passive",
                     description_zh="Eppendorf Safe-Lock 1.5 mL PP 管（0030120086）：长 38.9 mm、上段外径 10.8 mm、管口 2 mm 边沿、铰链翻盖；尺寸按 Eppendorf 技术数据表第 6 页图。",
                     sources=src)
    L, rim_h = 0.0389, 0.002
    R_top, r_cone_top, tip_r = 0.0054, 0.00535, 0.0012
    z_cone = L - rim_h - 0.020            # straight 20 mm section below the 2 mm rim
    t = 0.0008
    outer = [(0.0, 0.0), (tip_r, 0.0004), (0.0028, 0.0025), (r_cone_top, z_cone), (R_top, L - rim_h), (0.0062, L - rim_h), (0.0062, L)]
    inner = [(0.0052, L), (R_top - t, L - rim_h - 0.001), (r_cone_top - t, z_cone), (0.002, 0.0028), (0.0, 0.0012)]
    body_mesh = revolve(outer + inner, sections=48)
    col = [Prim("cylinder", [0.0018, 0.0012], [0, 0, 0.0006])]
    col += ring_boxes(0.0018, 0.0008, r_cone_top, z_cone, 0.0009, 12)
    col += ring_boxes(r_cone_top, z_cone, R_top, L - rim_h, 0.0009, 12)
    col += ring_boxes(0.0062, L - rim_h, 0.0062, L, 0.0062 - 0.0052, 12)
    mass_body = body_mesh.volume * DENSITY["pp"]
    a.add(Link("body", "管身", "盛放样品（≤1.5 mL）", "PP 锥底管；上段外径 10.8 mm", visual=[(body_mesh, PP_CLEAR)], collision=col,
               solid_collision=[Prim("cylinder", [R_top, L], [0, 0, L / 2])], mass_kg=mass_body, mass_evidence="assumption",
               mass_note_zh="建模体积 × PP 密度", material="pp", geometry_evidence="documented"))
    # lid: a disc with a plug, hinged at +Y side of the rim; joint axis along X, opening rotates the lid up and back (+Y)
    import trimesh
    lid_r, lid_t = 0.0062, 0.0012
    plug = trimesh.creation.cylinder(radius=0.0050, height=0.0035, sections=32)
    plug.apply_translation([0.0, -0.0063, -0.00175])
    disc = trimesh.creation.cylinder(radius=lid_r, height=lid_t, sections=32)
    disc.apply_translation([0.0, -0.0063, lid_t / 2])
    tab = trimesh.creation.box(extents=[0.008, 0.007, lid_t])
    tab.apply_translation([0.0, -0.0063 - lid_r - 0.0025, lid_t / 2])
    lid_mesh = trimesh.util.concatenate([disc, plug, tab])
    mass_lid = (disc.volume + plug.volume + tab.volume) * DENSITY["pp"]
    a.add(Link("lid", "翻盖（Safe-Lock）", "密封管口；向上翻开 90°", "PP 翻盖，铰链在 +Y 侧管沿；带推开用的舌片（−Y 侧）",
               parent="body", origin_m=[0.0, 0.0063, L],
               joint={"type": "revolute", "axis": [-1, 0, 0], "limits": [0.0, round(math.pi / 2, 6)], "evidence": "documented",
                      "source_ids": ["eppendorf_safelock_15"],
                      "note_zh": "正方向 = 盖绕铰链向上翻开（数据表图示开盖 90°）；开盖 = 推舌片向上（touch + intent）"},
               visual=[(lid_mesh, PP_CLEAR)], collision=[Prim("cylinder", [lid_r, lid_t], [0.0, -0.0063, lid_t / 2]),
                                                         Prim("box", [0.008, 0.007, lid_t], [0.0, -0.0063 - lid_r - 0.0025, lid_t / 2])],
               solid_collision=[Prim("cylinder", [lid_r, lid_t], [0.0, -0.0063, lid_t / 2])],
               mass_kg=mass_lid, mass_evidence="assumption", mass_note_zh="建模体积 × PP 密度", material="pp", geometry_evidence="documented"))
    interior = [(r, z) for r, z in reversed(inner)]
    brim = profile_volume(interior + [(0.0, L)])
    z_g = L - 0.012
    frames = top_down_frames(z_g, L + 0.0015, 2 * R_top, yaw_free=False, contact_z=(z_g - 0.009, z_g + 0.009),
                             note_zh="俯抓管上段（外径 10.8 mm）；手指沿 Y 合拢会碰铰链，宜沿 X 合拢（yaw 90°）")
    frames["grasp"]["rot_xyzw"] = [0.70710678, 0.70710678, 0.0, 0.0]
    frames["lid_tab"] = frame([0.0, -0.0063 - lid_r - 0.0045 + 0.0063, L + 0.0006], "base_link", rot=[0.0, 0.0, 0.0, 1.0],
                              note_zh="翻盖舌片：向上推（+Z）即开盖")
    frames["cap_seat"] = frame([0.0, 0.0063, L], "body", rot=[0.0, 0.0, 0.0, 1.0], note_zh="盖铰链原点（关闭位）")
    a.labware = {
        "type_id": a.asset_type_id, "family": "tube", "labware_class": "tube", "fits": ["tube", "microtube_1p5ml"],
        "maker": "Eppendorf", "model": "Safe-Lock Tubes 1.5 mL, PP, colourless", "catalogue_no": "0030120086",
        "nominal_capacity_m3": 1.5e-6, "brim_capacity_m3": round(brim, 10), "nominal_capacity_evidence": "documented",
        "nominal_source_id": "eppendorf_safelock_15",
        "outer": {"diameter_m": 0.0108, "height_body_m": L, "height_closed_m": 0.041, "source_id": "eppendorf_safelock_15"},
        "interior": {"shape": "revolved", "radius_m": R_top - t, "floor_m": 0.0012, "height_m": L, "body_top_m": L - rim_h,
                     "nominal_fill_height_m": round(fill_height(interior, 1.5e-6), 5),
                     "volume_table_m_ml": [[round(h, 5), round(inner_volume_below(interior, h) * 1e6, 4)] for h in np.linspace(0.0012, L, 11)],
                     "profile_rz_m": [[round(r, 5), round(z, 5)] for r, z in interior]},
        "closure": {"type": "hinged_lid", "link": "lid", "joint": "lid_joint", "axis": [-1, 0, 0], "open_rad": round(math.pi / 2, 6),
                    "rule_zh": "开盖 = 推舌片（lid_tab）向上，盖关节转到 open_rad；关盖 = 压回 0。翻盖不脱离管身。"},
        "frames": frames, "materials": {"body": "pp", "lid": "pp"},
        "empty_mass_kg": round(mass_body + mass_lid, 6), "mass_evidence": "computed from modelled volume x PP density",
        "stands_upright": False, "franka_grasp_ok": True, "storage_note_zh": "放在管架或冰箱的管盒中（锥底不能自立）",
    }
    a.prop("body", "outer_diameter_m", 0.0108, "m", "上段外径（数据表图）", "documented", ["eppendorf_safelock_15"])
    a.prop("body", "length_m", L, "m", "管长（数据表图）", "documented", ["eppendorf_safelock_15"])
    a.prop("body", "nominal_capacity_m3", 1.5e-6, "m3", "标称容量", "documented", ["eppendorf_safelock_15"])
    a.state_slots = {"closure.state": {
        "component_ids": ["lid"], "value_schema": {"type": "string", "enum": ["closed", "open"]}, "unit": "1", "reference_conditions": None,
        "meaning_zh": "翻盖状态（由场景按盖关节角记录）", "origin": "scene", "access": "read_only", "visibility": "agent",
        "read_binding": None, "write_via": None, "sampling": {"mode": "instant", "time_domain": "simulation", "window_description": None},
        "source_ids": ["eppendorf_safelock_15"]}}
    a.contract_ports = {"mouth": {"component_id": "body", "medium": "liquid", "direction": "bidirectional", "frame": "frame:grasp",
                                  "meaning_zh": "管口：移液进出", "connection_constraints": ["翻盖须为 open"]}}
    a.limitations_zh = ["被动容器；翻盖开合由机器人推舌片（touch + intent）。", "锥底管不能自立，需要管架（未建模）。",
                        "外形按 Eppendorf 数据表图；壁厚、盖厚为假设。"]
    return a


ALL_CONTAINER_BUILDERS = {}
for _ml in (100, 250, 500, 1000):
    for _amber in (False, True):
        _s = gl45_spec(_ml, _amber)
        ALL_CONTAINER_BUILDERS[_s.slug] = (lambda s=_s: build_screwcap(s))
for _fn in (winchester_25l_spec, lambda: nalgene_jar_spec(250), lambda: nalgene_jar_spec(1000), lambda: vial_spec("2ml"),
            lambda: vial_spec("20ml"), lambda: falcon_spec(15), lambda: falcon_spec(50)):
    _s = _fn()
    ALL_CONTAINER_BUILDERS[_s.slug] = (lambda s=_s: build_screwcap(s))
ALL_CONTAINER_BUILDERS["eppendorf_safelock_1p5ml"] = build_safelock_15
for _k in ("nm500", "nm1000", "wm500"):
    _s = nalgene_hdpe_spec(_k)
    ALL_CONTAINER_BUILDERS[_s.slug] = (lambda s=_s: build_screwcap(s))
