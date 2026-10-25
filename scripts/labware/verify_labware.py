"""Offline checks for every asset under assets/labware/ (no Isaac, no GPU).

For each asset directory (one with geometry_manifest.json) the checks read the written files, not the builder's objects:
  files / urdf      every artifact and every mesh the URDFs reference exists; URDF links == manifest components
  watertight        each link's visual OBJ is watertight, or the link has collision primitives
  collision         every link has at least one collision primitive in model.urdf
  glb               model.glb loads with one geometry per link
  envelope          URDF forward kinematics at joint limits: nothing below z = 0; closed size == manifest size
  frames            labware frames lie inside the closed bounding box (+5 mm), pre_* frames above the top;
                    franka_ok <=> width <= 74 mm; the hand (37 mm above the TCP) clears the object's top for top-down grasps
  interior          brim volume >= nominal capacity; interior radius < outer radius; fill height below the lip
  cap_detach        closed cap and body collisions do not overlap; the cap lifted by clear_lift is free of the body and above
                    the lip; the closure rule (closure.py) gives loosened -> detached -> (seat, turn back) -> closed, and a
                    touch without a grasp changes nothing
  doors             every door / lid joint has a hinge axis + origin, limits, a handle frame and a door mass
  slots             storage slots lie inside the furniture, on a shelf surface, with the declared pitch and headroom
  contract          skills/chemistry-lab-assets/scripts/asset_tool.py validate --check-files
Usage:  python scripts/labware/verify_labware.py [--only slug ...] [--out assets/labware/verification_report.json]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import trimesh
from trimesh.transformations import euler_matrix, translation_matrix

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from closure import Closure, HingedClosure  # noqa: E402
from labware_kit import FRANKA_MAX_GRASP_M, HAND_ABOVE_TCP_M, Prim, obb_overlap, transform_prim  # noqa: E402

ROOT = HERE.parents[1]
LABWARE = ROOT / "assets" / "labware"
ASSET_TOOL = ROOT / "skills" / "chemistry-lab-assets" / "scripts" / "asset_tool.py"


def _vec(el, key, default="0 0 0"):
    return np.array([float(v) for v in ((el.get(key) if el is not None and el.get(key) else default).split())])


def _joint_motion(jtype, axis, q):
    axis = axis / (np.linalg.norm(axis) or 1.0)
    if jtype in ("revolute", "continuous"):
        return trimesh.transformations.rotation_matrix(q, axis)
    if jtype == "prismatic":
        return translation_matrix(axis * q)
    return np.eye(4)


class Urdf:
    def __init__(self, path: Path):
        self.path = path
        self.root = ET.parse(path).getroot()
        self.links = {l.get("name"): l for l in self.root.findall("link")}
        self.joints = {j.find("child").get("link"): j for j in self.root.findall("joint")}

    def world(self, link: str, q: dict) -> np.ndarray:
        if link not in self.joints:
            return np.eye(4)
        j = self.joints[link]
        o = j.find("origin")
        T = translation_matrix(_vec(o, "xyz")) @ euler_matrix(*_vec(o, "rpy"), axes="sxyz")
        if j.get("type") != "fixed":
            T = T @ _joint_motion(j.get("type"), _vec(j.find("axis"), "xyz", "1 0 0"), q.get(j.get("name"), 0.0))
        return self.world(j.find("parent").get("link"), q) @ T

    def collisions(self, link: str) -> list[Prim]:
        out = []
        for c in self.links[link].findall("collision"):
            o = c.find("origin")
            g = c.find("geometry")[0]
            if g.tag == "box":
                size = [float(v) for v in g.get("size").split()]
            elif g.tag == "cylinder":
                size = [float(g.get("radius")), float(g.get("length"))]
            else:
                size = [float(g.get("radius"))]
            out.append(Prim(g.tag, size, list(_vec(o, "xyz")), list(_vec(o, "rpy"))))
        return out

    def movable(self):
        return {j.get("name"): (float(j.find("limit").get("lower")), float(j.find("limit").get("upper")))
                for j in self.joints.values() if j.get("type") in ("revolute", "prismatic")}


def _check(results, cid, ok, data=None):
    results.append({"id": cid, "status": "passed" if ok else "failed", "data": data})
    return ok


def verify_asset(d: Path, run_contract: bool = True) -> dict:
    res: list = []
    man = json.loads((d / "geometry_manifest.json").read_text(encoding="utf-8"))
    lw = man.get("labware") or {}
    slug = d.name
    u = Urdf(d / "model.urdf")
    comp_ids = [c["id"] for c in man["components"]]
    # ---------------------------------------------------------------- files / urdf
    missing = [p for k, p in man["artifacts"].items() if p and k != "meshes_dir" and not (d / p).exists()]
    _check(res, "files", not missing and (d / "asset.contract.json").exists() and (d / "collision_proxies.json").exists(), missing)
    _check(res, "urdf_links_match_manifest", set(u.links) - {"base_link"} == set(comp_ids), sorted(u.links))
    bad_mesh = []
    for urdf_path in [d / "model.urdf", d / "model_solid.urdf", *sorted((d / "parts").glob("*.urdf"))]:
        if not urdf_path.exists():
            continue
        for m in ET.parse(urdf_path).getroot().iter("mesh"):
            if not (urdf_path.parent / m.get("filename")).exists():
                bad_mesh.append(f"{urdf_path.name}:{m.get('filename')}")
    _check(res, "urdf_meshes_exist", not bad_mesh, bad_mesh)
    # ---------------------------------------------------------------- watertight / collision
    wt = {}
    for cid in comp_ids:
        mesh = trimesh.load(d / "meshes" / f"{slug}__{cid}.obj", force="mesh")
        n_col = len(u.collisions(cid))
        wt[cid] = {"watertight": bool(mesh.is_watertight), "collisions": n_col}
    _check(res, "watertight_or_collision", all(v["watertight"] or v["collisions"] > 0 for v in wt.values()), wt)
    _check(res, "collision_present", all(v["collisions"] > 0 for v in wt.values()), {k: v["collisions"] for k, v in wt.items()})
    objects = {c["id"]: c.get("objects") or [f"{slug}__{c['id']}"] for c in man["components"]}
    n_obj = sum(len(v) for v in objects.values())
    scene = trimesh.load(d / "model.glb", force="scene")
    _check(res, "glb_loads", len(scene.geometry) == n_obj, {"geometries": len(scene.geometry), "objects": n_obj})
    missing_obj = [o for v in objects.values() for o in v if not (d / "meshes" / f"{o}.obj").exists()]
    _check(res, "object_meshes_exist", not missing_obj, missing_obj)

    # ---------------------------------------------------------------- envelope (FK)
    def bounds(q):
        pts = []
        for cid in comp_ids:
            for o in objects[cid]:
                m = trimesh.load(d / "meshes" / f"{o}.obj", force="mesh")
                m.apply_transform(u.world(cid, q))
                pts.append(m.bounds)
        return np.array([np.min([b[0] for b in pts], axis=0), np.max([b[1] for b in pts], axis=0)])

    b0 = bounds({})
    size_ok = np.allclose(b0[1] - b0[0], man["overall_dimensions_m"], atol=2e-4)
    _check(res, "envelope_closed", size_ok and b0[0][2] >= -1e-6, {"bounds": b0.round(5).tolist()})
    lows = {}
    for jn, (lo, hi) in u.movable().items():
        for qv in (lo, hi):
            bb = bounds({jn: qv})
            lows[f"{jn}={qv:.3g}"] = round(float(bb[0][2]), 5)
    _check(res, "envelope_joint_limits_above_floor", all(v >= -1e-6 for v in lows.values()), lows)
    top = float(b0[1][2])
    # ---------------------------------------------------------------- frames
    frames = lw.get("frames") or {}
    bad = []
    for name, f in frames.items():
        if f.get("link") not in ("base_link", None):
            continue
        p = np.array(f["pos_m"])
        if name.startswith(("pre_", "post_")) or name.endswith(("_pre_grasp", "_pre_place")):
            if not (p[2] >= top - 1e-6 and np.all(p[:2] >= b0[0][:2] - 0.005) and np.all(p[:2] <= b0[1][:2] + 0.005)):
                bad.append(name)
        elif name.startswith("handle"):
            if not (np.all(p >= b0[0] - 0.08) and np.all(p <= b0[1] + 0.08)):
                bad.append(name)
        elif not (np.all(p >= b0[0] - 0.005) and np.all(p <= b0[1] + 0.005)):
            bad.append(name)
    _check(res, "frames_inside_bounds", not bad, bad)
    width_bad, clear_bad = [], []
    for name, f in frames.items():
        if "width_m" in f and "franka_ok" in f:
            if bool(f["franka_ok"]) != bool(f["width_m"] <= FRANKA_MAX_GRASP_M + 1e-9) and not (not f["franka_ok"] and f.get("franka_blocked_reason")):
                width_bad.append(name)
            if f["franka_ok"] and f.get("rot_xyzw", [0, 0, 0, 1])[:2] != [0.0, 0.0] and f.get("link") == "base_link":
                # top-down grasp: the hand (37 mm above the TCP) must clear the top of everything within the finger footprint
                if f["pos_m"][2] + HAND_ABOVE_TCP_M < local_top(d, man, u, f) - 1e-4:
                    clear_bad.append(name)
    _check(res, "grasp_width_flag_consistent", not width_bad, width_bad)
    _check(res, "hand_clears_top", not clear_bad, clear_bad)
    if lw.get("family") in ("reagent_bottle", "solvent_bottle", "powder_jar", "vial", "tube"):
        g = frames.get("grasp", {})
        _check(res, "franka_grasp_available", bool(g.get("franka_ok")), {"width_m": g.get("width_m")})
    # ---------------------------------------------------------------- interior
    it = lw.get("interior")
    if it and lw.get("nominal_capacity_m3"):
        outer_r = (lw.get("outer") or {}).get("diameter_m", 1.0) / 2
        fh = it.get("nominal_fill_height_m")
        ok = (lw["brim_capacity_m3"] >= lw["nominal_capacity_m3"]) and (it.get("radius_m", 0) < outer_r or it.get("shape") != "revolved") \
            and it["floor_m"] < it["height_m"] and (fh is None or fh < it["height_m"])
        _check(res, "interior_consistent", ok, {"brim_mL": round(lw["brim_capacity_m3"] * 1e6, 3), "nominal_mL": lw["nominal_capacity_m3"] * 1e6,
                                                "fill_h": fh, "lip": it["height_m"]})
    # ---------------------------------------------------------------- cap detach
    cl = lw.get("closure") or {}
    if cl.get("type") == "screw_cap":
        body_c, cap_c = u.collisions("body"), u.collisions(cl["link"])
        Tcap = u.world(cl["link"], {})
        cap_w = [transform_prim(p, Tcap) for p in cap_c]
        overl = [(i, j) for i, a in enumerate(cap_w) for j, b in enumerate(body_c) if obb_overlap(a, b)]
        lift = cl["clear_lift_m"]
        Tl = translation_matrix([0, 0, lift]) @ Tcap
        cap_l = [transform_prim(p, Tl) for p in cap_c]
        overl_l = [(i, j) for i, a in enumerate(cap_l) for j, b in enumerate(body_c) if obb_overlap(a, b)]
        cap_mesh = trimesh.load(d / "meshes" / f"{slug}__{cl['link']}.obj", force="mesh")
        cap_mesh.apply_transform(Tl)
        lip = (lw.get("interior") or {}).get("height_m", 0)
        c = Closure.from_manifest(cl)
        seq = [c.twist(0.5 * c.detach_after_rad, cap_grasped=False), c.twist(0.5 * c.detach_after_rad, cap_grasped=True),
               c.twist(0.6 * c.detach_after_rad, cap_grasped=True), c.twist(-1.0, cap_grasped=True)]
        c.seat(True)
        seq += [c.twist(-0.5 * c.detach_after_rad, cap_grasped=True), c.twist(-0.6 * c.detach_after_rad, cap_grasped=True)]
        rule_ok = seq == ["closed", "loosened", "detached", "detached", "loosened", "closed"]
        parts_ok = all((d / p).exists() for p in (cl.get("parts") or {}).values())
        _check(res, "cap_detaches", not overl and not overl_l and cap_mesh.bounds[0][2] >= lip - 1e-6 and rule_ok and parts_ok,
               {"closed_overlaps": len(overl), "lifted_overlaps": len(overl_l), "lift_m": lift,
                "cap_bottom_after_lift": round(float(cap_mesh.bounds[0][2]), 5), "lip": lip, "rule_sequence": seq, "parts": parts_ok})
    elif cl.get("type") == "hinged_lid":
        h = HingedClosure(open_rad=cl["open_rad"])
        seq = [h.state, h.set_angle(cl["open_rad"]), h.set_angle(0.0)]
        lowz = lows.get(f"{cl['joint']}={cl['open_rad']:.3g}", 0)
        _check(res, "lid_opens", seq == ["closed", "open", "closed"] and lowz >= -1e-6, {"sequence": seq})
    elif cl.get("type") == "lift_off_lid":
        body_c, lid_c = u.collisions("body"), u.collisions(cl["link"])
        Tl = u.world(cl["link"], {})
        lid_w = [transform_prim(p, Tl) for p in lid_c]
        ov = [(i, j) for i, a in enumerate(lid_w) for j, b in enumerate(body_c) if obb_overlap(a, b)]
        Tl2 = translation_matrix([0, 0, cl["clear_lift_m"]]) @ Tl
        ov2 = [(i, j) for i, a in enumerate([transform_prim(p, Tl2) for p in lid_c]) for j, b in enumerate(body_c) if obb_overlap(a, b)]
        _check(res, "lid_lifts_off", not ov and not ov2 and (d / cl["parts"]["lid"]).exists(), {"closed_overlaps": len(ov), "lifted_overlaps": len(ov2)})
    # ---------------------------------------------------------------- doors
    doors = lw.get("doors") or {}
    if doors:
        bad = []
        for name, dd in doors.items():
            j = u.joints.get(dd["link"])
            okd = j is not None and j.get("name") == dd["joint"] and dd.get("hinge_axis") and dd.get("hinge_origin_m") is not None \
                and dd.get("limits") and dd.get("mass_kg", 0) > 0 and dd.get("handle", {}).get("pos_m") is not None
            if okd:
                lo, hi = dd["limits"]
                okd = abs(float(j.find("limit").get("lower")) - lo) < 1e-6 and abs(float(j.find("limit").get("upper")) - hi) < 1e-6
            if not okd:
                bad.append(name)
        _check(res, "doors_hinge_handle_mass", not bad, {"doors": sorted(doors), "bad": bad})
    # ---------------------------------------------------------------- slots
    ports = [p for p in man.get("ports") or [] if p.get("kind") == "storage"]
    if ports:
        out = [p["name"] for p in ports if not (np.all(np.array(p["position_m"]) >= b0[0] - 1e-6) and np.all(np.array(p["position_m"]) <= b0[1] + 1e-6))]
        _check(res, "slots_inside", not out, {"n": len(ports), "outside": out})
        sl = lw.get("storage") or {}
        if sl.get("min_pitch_m"):
            P = np.array([p["position_m"] for p in ports])
            dmin = min(float(np.linalg.norm(P[i, :2] - P[j, :2])) for i in range(len(P)) for j in range(i + 1, len(P))
                       if abs(P[i, 2] - P[j, 2]) < 1e-3) if len(P) > 1 else 1.0
            _check(res, "slot_pitch", dmin >= sl["min_pitch_m"] - 1e-6, {"min_pitch_m": round(dmin, 4), "required": sl["min_pitch_m"]})
        if sl.get("headroom_m") is not None:
            short = [p["name"] for p in ports if p.get("headroom_m", 0) < sl["headroom_m"] - 1e-6]
            _check(res, "slot_headroom", not short, {"required_m": sl["headroom_m"], "short": short})
        if sl.get("expected_positions"):
            names = sorted(p["name"] for p in ports)
            _check(res, "slot_names_match_place", names == sorted(sl["expected_positions"]), {"n": len(names)})
    # ---------------------------------------------------------------- appearance (colours, decal textures + licences)
    app = [a_ for c in man["components"] for a_ in c.get("appearance") or []]
    if app:
        bad_tex = [a_["object"] for a_ in app if a_.get("texture") and not ((d / a_["texture"]).exists() and a_.get("texture_licence"))]
        _check(res, "appearance_textures_licensed", not bad_tex, {"objects": len(app), "textured": sum(1 for a_ in app if a_.get("texture")), "bad": bad_tex})
    # ---------------------------------------------------------------- contract
    if run_contract:
        r = subprocess.run([sys.executable, str(ASSET_TOOL), "validate", str(d / "asset.contract.json"), "--check-files", "--asset-root", str(ROOT)],
                           capture_output=True, text=True)
        try:
            out = json.loads(r.stdout)
        except Exception:
            out = {"valid": False, "errors": [r.stdout[-400:], r.stderr[-400:]]}
        _check(res, "contract_valid_check_files", bool(out.get("valid")), out.get("errors"))
    status = "passed" if all(x["status"] == "passed" for x in res) else "failed"
    return {"asset": man["asset_type_id"], "dir": str(Path(d).resolve().relative_to(ROOT)), "status": status, "checks": res}


def local_top(d: Path, man: dict, u: Urdf, f: dict) -> float:
    """Highest point of the closed asset inside the hand's footprint around the grasp (a 0.10 x 0.03 m box)."""
    x, y = f["pos_m"][0], f["pos_m"][1]
    zmax = 0.0
    for c in man["components"]:
        for o in c.get("objects") or [f"{d.name}__{c['id']}"]:
            m = trimesh.load(d / "meshes" / f"{o}.obj", force="mesh")
            m.apply_transform(u.world(c["id"], {}))
            v = m.vertices
            sel = (np.abs(v[:, 0] - x) <= 0.05) & (np.abs(v[:, 1] - y) <= 0.05)
            if sel.any():
                zmax = max(zmax, float(v[sel, 2].max()))
    return zmax


def asset_dirs(only=None):
    for m in sorted(LABWARE.glob("*/*/geometry_manifest.json")):
        if only and m.parent.name not in only:
            continue
        yield m.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", default=str(LABWARE / "verification_report.json"))
    ap.add_argument("--no-contract", action="store_true")
    a = ap.parse_args()
    reports = [verify_asset(d, run_contract=not a.no_contract) for d in asset_dirs(a.only)]
    for r in reports:
        failed = [c["id"] for c in r["checks"] if c["status"] != "passed"]
        print(f"{r['status']:6s} {r['asset']:52s} {len(r['checks']):2d} checks" + (f"  FAILED: {failed}" if failed else ""))
    if not a.only:
        Path(a.out).write_text(json.dumps({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "method": "scripts/labware/verify_labware.py (offline: URDF FK, trimesh, OBB SAT, closure rule, asset_tool)",
                                           "environment": {"trimesh": trimesh.__version__, "isaac": "not used"}, "assets": reports}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if all(r["status"] == "passed" for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
