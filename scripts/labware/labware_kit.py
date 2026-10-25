"""labware_kit — build, export and check the Chemistry-Sim labware and storage-furniture assets (assets/labware/).

The instrument assets use skills/chemistry-asset-expansion/scripts/geometry_kit.py (boxes / cylinders / spheres only).
Containers need hollow, revolved shells, separate cap links and pouring / liquid data, so this kit writes the same file
set with the same conventions, plus labware data:

    <asset_dir>/meshes/<slug>__<link>.obj + .mtl      visual mesh per link (link frame)
    <asset_dir>/model.glb                             Z-up scene, one node per link (no Y-up conversion, like geometry_kit)
    <asset_dir>/model.urdf                            base_link + links; hollow containers get wall-ring collisions
    <asset_dir>/model_solid.urdf                      same links, one solid collision per link (ledger-only / stored use)
    <asset_dir>/parts/<link>.urdf                     each detachable link (body, cap, lid) as its own rigid object
    <asset_dir>/geometry_manifest.json                geometry_kit keys + "labware" (frames, interior, closure) + "ports"
    <asset_dir>/collision_proxies.json                primitives per link
    <asset_dir>/asset.contract.json                   Chemistry Lab Asset Contract 1.0.0 (asset_kind passive / tool)

Conventions (docs/模型与物理注释说明.md, same as geometry_kit): SI units, right-handed, +Z up, +Y towards the back,
origin at the centre of the footprint, base at z = 0.  Quaternions are (x, y, z, w) as in Isaac Lab 3.
Frames are positions of the robot tool centre point (TCP) in the asset frame (closed state).  Top-down grasps use
rot_xyzw (1, 0, 0, 0): the TCP z axis points down (-Z); the fingers close along the TCP y axis, which is the asset's
-Y axis after that rotation, i.e. the fingers close front-to-back unless yaw_free is true (rotationally symmetric parts).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from trimesh.transformations import euler_matrix, translation_matrix, quaternion_matrix

ROOT = Path(__file__).resolve().parents[2]          # Chemistry-Sim
TEXTURES_DIR = ROOT / "assets" / "labware" / "_textures"


def srgb_to_linear(c: float) -> float:
    """sRGB (0..1, what colour charts / RAL hex give) -> linear (what glTF baseColorFactor and USD diffuse expect)."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(hexstr: str, alpha: float = 1.0) -> tuple:
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0, alpha)


def decal_quad(center, width: float, height: float, facing: str = "-y", offset: float = 0.0006) -> tuple:
    """A textured quad (mesh, uv) in a link frame, facing -Y (front) or +Z (top); the image is upright seen from that side."""
    cx, cy, cz = center
    w, h = width / 2, height / 2
    if facing == "-y":
        y = cy - offset
        v = [(cx - w, y, cz - h), (cx + w, y, cz - h), (cx + w, y, cz + h), (cx - w, y, cz + h)]
    elif facing == "+z":
        z = cz + offset
        v = [(cx - w, cy - h, z), (cx + w, cy - h, z), (cx + w, cy + h, z), (cx - w, cy + h, z)]
    else:
        raise ValueError(facing)
    m = trimesh.Trimesh(vertices=np.array(v), faces=np.array([[0, 1, 2], [0, 2, 3]]), process=False)
    uv = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    return m, uv
LABWARE_DIR = ROOT / "assets" / "labware"
TOP_DOWN = [1.0, 0.0, 0.0, 0.0]                      # TCP z axis -> world -Z (x, y, z, w)
FRANKA_MAX_OPEN_M = 0.080                            # Franka Hand stroke (2 x 40 mm), robot set-up constraint 2026-10-22
FRANKA_MAX_GRASP_M = 0.074                           # usable grasp width (robot set-up: <= about 74 mm across)
HAND_ABOVE_TCP_M = 0.037                             # Franka hand collision starts ~3.7 cm above the TCP (memory: LabUtopia import lessons)
PRE_GRASP_LIFT_M = 0.10


# ---------------------------------------------------------------------------------------------------------- primitives
@dataclass
class Prim:
    shape: str                      # box | cylinder | sphere
    size_m: list                    # box [x, y, z]; cylinder [radius, length]; sphere [radius]
    origin_m: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rpy_rad: list = field(default_factory=lambda: [0.0, 0.0, 0.0])

    def matrix(self) -> np.ndarray:
        return translation_matrix(self.origin_m) @ euler_matrix(*self.rpy_rad, axes="sxyz")

    def mesh(self) -> trimesh.Trimesh:
        if self.shape == "box":
            m = trimesh.creation.box(extents=self.size_m)
        elif self.shape == "cylinder":
            m = trimesh.creation.cylinder(radius=self.size_m[0], height=self.size_m[1], sections=32)
        elif self.shape == "sphere":
            m = trimesh.creation.icosphere(subdivisions=2, radius=self.size_m[0])
        else:
            raise ValueError(self.shape)
        m.apply_transform(self.matrix())
        return m

    def obb(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(centre, axes 3x3 columns, half extents) of the primitive's bounding box (cylinders/spheres conservatively)."""
        T = self.matrix()
        if self.shape == "box":
            half = np.asarray(self.size_m, float) / 2
        elif self.shape == "cylinder":
            half = np.array([self.size_m[0], self.size_m[0], self.size_m[1] / 2])
        else:
            half = np.array([self.size_m[0]] * 3)
        return T[:3, 3].copy(), T[:3, :3].copy(), half

    def to_json(self) -> dict:
        return {"shape": self.shape, "size_m": [round(float(v), 6) for v in self.size_m],
                "origin_m": [round(float(v), 6) for v in self.origin_m], "rpy_rad": [round(float(v), 6) for v in self.rpy_rad]}

    def urdf_geometry(self) -> ET.Element:
        g = ET.Element("geometry")
        if self.shape == "box":
            ET.SubElement(g, "box", size=" ".join(f"{v:.6g}" for v in self.size_m))
        elif self.shape == "cylinder":
            ET.SubElement(g, "cylinder", radius=f"{self.size_m[0]:.6g}", length=f"{self.size_m[1]:.6g}")
        else:
            ET.SubElement(g, "sphere", radius=f"{self.size_m[0]:.6g}")
        return g


def transform_prim(p: Prim, T: np.ndarray) -> Prim:
    """The primitive expressed in another frame (T maps the primitive's frame into it)."""
    M = T @ p.matrix()
    rpy = trimesh.transformations.euler_from_matrix(M, axes="sxyz")
    return Prim(p.shape, list(p.size_m), M[:3, 3].tolist(), list(rpy))


def obb_overlap(a: Prim, b: Prim, tol: float = 1e-5) -> bool:
    """Separating-axis test between the bounding boxes of two primitives (same frame). True = they intersect."""
    ca, Aa, ha = a.obb()
    cb, Ab, hb = b.obb()
    axes = [Aa[:, i] for i in range(3)] + [Ab[:, i] for i in range(3)]
    for i in range(3):
        for j in range(3):
            c = np.cross(Aa[:, i], Ab[:, j])
            if np.linalg.norm(c) > 1e-9:
                axes.append(c / np.linalg.norm(c))
    d = cb - ca
    for ax in axes:
        ra = sum(ha[i] * abs(np.dot(Aa[:, i], ax)) for i in range(3))
        rb = sum(hb[i] * abs(np.dot(Ab[:, i], ax)) for i in range(3))
        if abs(np.dot(d, ax)) > ra + rb - tol:
            return False
    return True


def ring_boxes(r0: float, z0: float, r1: float, z1: float, thickness: float, n: int, overlap: float = 1.08) -> list[Prim]:
    """A ring of n boxes along one segment (r0, z0)-(r1, z1) of a revolved wall, outer face on the profile.

    Box local axes: x radial (thickness), y tangential, z along the segment; rpy = (0, pitch, yaw)."""
    dr, dz = r1 - r0, z1 - z0
    L = math.hypot(dr, dz)
    pitch = math.atan2(dr, dz)
    rm, zm = (r0 + r1) / 2, (z0 + z1) / 2
    # move the box centre inwards by half the thickness along the inward normal of the segment
    nx, nz = -dz / L, dr / L                    # inward normal in the (r, z) half plane (for a segment going upwards)
    rc, zc = rm + nx * thickness / 2, zm + nz * thickness / 2
    width = 2 * math.pi * max(r0, r1) / n * overlap
    out = []
    for k in range(n):
        yaw = 2 * math.pi * k / n
        out.append(Prim("box", [thickness, width, L], [rc * math.cos(yaw), rc * math.sin(yaw), zc], [0.0, pitch, yaw]))
    return out


def revolve(profile_rz, sections: int = 64) -> trimesh.Trimesh:
    """Revolve an (r, z) polyline that starts and ends on the axis into a closed (watertight) solid."""
    m = trimesh.creation.revolve(np.asarray(profile_rz, float), sections=sections)
    m.merge_vertices()
    if m.volume < 0:
        m.invert()
    return m


def profile_volume(profile_rz) -> float:
    """Volume of the solid of revolution bounded by an (r, z) polyline closed to the axis (Pappus, signed area)."""
    p = np.asarray(profile_rz, float)
    v = 0.0
    for (r0, z0), (r1, z1) in zip(p[:-1], p[1:]):
        # frustum slice between consecutive points, integrated with the shoelace form of Pappus' theorem
        v += math.pi * (z1 - z0) * (r0 * r0 + r0 * r1 + r1 * r1) / 3.0
    return abs(v)


def inner_volume_below(inner_rz, h: float) -> float:
    """Liquid volume up to height h inside an inner profile given bottom-up as [(r, z), ...] (r(z) piecewise linear)."""
    pts = sorted(inner_rz, key=lambda p: p[1])
    v = 0.0
    for (r0, z0), (r1, z1) in zip(pts[:-1], pts[1:]):
        if z1 <= z0 or h <= z0:
            continue
        zt = min(h, z1)
        rt = r0 + (r1 - r0) * (zt - z0) / (z1 - z0)
        v += math.pi * (zt - z0) * (r0 * r0 + r0 * rt + rt * rt) / 3.0
    return v


def fill_height(inner_rz, volume_m3: float) -> float:
    lo, hi = min(p[1] for p in inner_rz), max(p[1] for p in inner_rz)
    for _ in range(60):
        mid = (lo + hi) / 2
        if inner_volume_below(inner_rz, mid) < volume_m3:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------------------------------------- links
@dataclass
class Link:
    id: str
    name_zh: str
    function_zh: str
    physical_meaning_zh: str
    parent: str | None = None                 # None: child of base_link
    origin_m: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rpy_rad: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    joint: dict | None = None                 # {"type", "axis", "limits", "evidence", "note_zh"}
    visual: list = field(default_factory=list)        # [(trimesh, rgba)] in link frame
    collision: list = field(default_factory=list)     # [Prim] in link frame (hollow where it matters)
    solid_collision: list = field(default_factory=list)   # [Prim] one solid hull-like primitive set (model_solid.urdf)
    mass_kg: float = 0.01
    mass_evidence: str = "assumption"
    mass_note_zh: str = ""
    material: str = ""
    geometry_evidence: str = "documented"
    geometry_note_zh: str = ""
    detachable: bool = False                  # exported as parts/<id>.urdf too

    def visual_mesh(self) -> trimesh.Trimesh:
        meshes = []
        for el in self.visual:
            m, rgba = el[0], el[1]
            mm = m.copy()
            mm.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                name=f"mat_{self.id}", baseColorFactor=[int(255 * c) for c in rgba], metallicFactor=0.0,
                roughnessFactor=0.35 if rgba[3] < 1 else 0.6, alphaMode="BLEND" if rgba[3] < 1 else "OPAQUE"))
            meshes.append(mm)
        return trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]

    def rgba(self):
        return self.visual[0][1]


def link_local(link: Link) -> np.ndarray:
    return translation_matrix(link.origin_m) @ euler_matrix(*link.rpy_rad, axes="sxyz")


def joint_motion(joint: dict | None, q: float) -> np.ndarray:
    if not joint or joint["type"] == "fixed":
        return np.eye(4)
    axis = np.asarray(joint["axis"], float)
    axis = axis / np.linalg.norm(axis)
    if joint["type"] in ("revolute", "continuous"):
        return trimesh.transformations.rotation_matrix(q, axis)
    return translation_matrix(axis * q)


class LabwareAsset:
    """One asset type: links + labware data -> files + manifest + contract."""

    def __init__(self, slug: str, asset_type_id: str, name_zh: str, name_en: str, group: str, category: str,
                 asset_kind: str = "passive", description_zh: str = "", sources: dict | None = None):
        self.slug, self.asset_type_id, self.name_zh, self.name_en = slug, asset_type_id, name_zh, name_en
        self.group, self.category, self.asset_kind = group, category, asset_kind
        self.description_zh = description_zh
        self.sources: dict = dict(sources or {})
        self.links: list[Link] = []
        self.labware: dict = {}             # frames, interior, closure, capacity ... (manifest "labware" + catalog)
        self.ports: list[dict] = []         # slots in the expansion manifest format (kind storage / surface / hole ...)
        self.properties: dict = {}          # component_id -> {prop: property dict} for the contract
        self.limitations_zh: list[str] = []
        self.mechanisms: dict = {}
        self.state_slots: dict = {}
        self.contract_ports: dict = {}
        self.dims_sources: list[str] = []

    @property
    def asset_dir(self) -> Path:
        return LABWARE_DIR / self.group / self.slug

    @property
    def rel_dir(self) -> str:
        return f"assets/labware/{self.group}/{self.slug}"

    def add(self, link: Link) -> Link:
        if any(l.id == link.id for l in self.links):
            raise ValueError(f"duplicate link {link.id}")
        if link.parent is not None and not any(l.id == link.parent for l in self.links):
            raise ValueError(f"{link.id}: parent {link.parent} must be added first")
        self.links.append(link)
        return link

    def elements(self, l: Link) -> list:
        """(object name, mesh, rgba sRGB, opts) per visual element; element 0 is <slug>__<link>, the others <slug>__<link>__<name>."""
        out, used = [], set()
        for k, el in enumerate(l.visual):
            opts = dict(el[2]) if len(el) > 2 and el[2] else {}
            if k == 0:
                name = f"{self.slug}__{l.id}"
            else:
                base = opts.get("name") or f"part{k}"
                name = f"{self.slug}__{l.id}__{base}"
                j = 2
                while name in used:
                    name = f"{self.slug}__{l.id}__{base}{j}"
                    j += 1
            used.add(name)
            out.append((name, el[0], el[1], opts))
        return out

    def link(self, lid: str) -> Link:
        return next(l for l in self.links if l.id == lid)

    def world(self, lid: str, q: dict | None = None) -> np.ndarray:
        l = self.link(lid)
        T = link_local(l) @ joint_motion(l.joint, (q or {}).get(l.id, 0.0))
        return T if l.parent is None else self.world(l.parent, q) @ T

    def bounds(self, q: dict | None = None) -> np.ndarray:
        pts = []
        for l in self.links:
            m = l.visual_mesh().copy()
            m.apply_transform(self.world(l.id, q))
            pts.append(m.bounds)
        return np.array([np.min([b[0] for b in pts], axis=0), np.max([b[1] for b in pts], axis=0)])

    def prop(self, component: str, key: str, value, unit: str, meaning_zh: str, evidence: str, source_ids: list[str],
             uncertainty: str | None = None):
        self.properties.setdefault(component, {})[key] = {"value": value, "unit": unit, "meaning_zh": meaning_zh, "evidence": evidence,
                                                         "source_ids": list(source_ids), "uncertainty": uncertainty}

    # ------------------------------------------------------------------------------------------------------ export
    def export(self) -> dict:
        d = self.asset_dir
        import shutil
        for sub in ("meshes", "parts"):
            if (d / sub).exists():
                shutil.rmtree(d / sub)                 # drop stale objects of an earlier build (only inside this asset dir)
        (d / "meshes").mkdir(parents=True, exist_ok=True)
        scene = trimesh.Scene()
        root_node = f"{self.slug}__root"
        scene.graph.update(frame_to=root_node, matrix=np.eye(4))
        tex_out = d / "meshes" / "textures"
        for l in self.links:
            parent_node = f"{self.slug}__{l.parent}" if l.parent else root_node
            for k, (name, mesh, rgba, opts) in enumerate(self.elements(l)):
                tex = opts.get("texture")
                r, g, b, a = rgba
                lin = [srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), a]
                if tex:
                    import shutil
                    from PIL import Image
                    tex_out.mkdir(parents=True, exist_ok=True)
                    tname = Path(tex).name
                    shutil.copyfile(tex, tex_out / tname)
                    uv = opts["uv"]
                    lines = [f"mtllib {name}.mtl", f"usemtl {name}"] + [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in mesh.vertices] + \
                            [f"vt {u:.6f} {v:.6f}" for u, v in uv] + [f"f {f0 + 1}/{f0 + 1} {f1 + 1}/{f1 + 1} {f2 + 1}/{f2 + 1}" for f0, f1, f2 in mesh.faces]
                    (d / "meshes" / f"{name}.obj").write_text("\n".join(lines) + "\n", encoding="utf-8")
                    (d / "meshes" / f"{name}.mtl").write_text(f"newmtl {name}\nKd 1 1 1\nKa 0 0 0\nKs 0.05 0.05 0.05\nNs 20\nd 1\nmap_Kd textures/{tname}\n", encoding="utf-8")
                    gm = mesh.copy()
                    gm.visual = trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial(
                        name=f"mat_{name}", baseColorTexture=Image.open(tex).convert("RGB"), baseColorFactor=[1.0, 1.0, 1.0, 1.0],
                        metallicFactor=0.0, roughnessFactor=0.6, doubleSided=False))
                else:
                    gm = mesh.copy()
                    gm.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                        name=f"mat_{name}", baseColorFactor=lin, metallicFactor=float(opts.get("metallic", 0.0)),
                        roughnessFactor=float(opts.get("roughness", 0.35 if a < 1 else 0.6)), alphaMode="BLEND" if a < 1 else "OPAQUE"))
                    text = gm.export(file_type="obj", include_normals=False, include_color=False)
                    text = f"mtllib {name}.mtl\nusemtl {name}\n" + "\n".join(x for x in text.splitlines() if not x.startswith(("mtllib", "usemtl")))
                    (d / "meshes" / f"{name}.obj").write_text(text + "\n", encoding="utf-8")
                    (d / "meshes" / f"{name}.mtl").write_text(f"newmtl {name}\nKd {r:.4f} {g:.4f} {b:.4f}\nKa 0 0 0\nKs 0.1 0.1 0.1\nNs 40\nd {a:.3f}\n", encoding="utf-8")
                if k == 0:
                    scene.add_geometry(gm, node_name=name, geom_name=name, parent_node_name=parent_node, transform=link_local(l))
                else:
                    scene.add_geometry(gm, node_name=name, geom_name=name, parent_node_name=f"{self.slug}__{l.id}", transform=np.eye(4))
        scene.export(d / "model.glb")
        self._write_urdf(d / "model.urdf", self.links, solid=False)
        has_hollow = any(l.solid_collision for l in self.links)
        if has_hollow:
            self._write_urdf(d / "model_solid.urdf", self.links, solid=True)
        detachable = [l for l in self.links if l.detachable]
        if detachable:
            (d / "parts").mkdir(exist_ok=True)
            for l in self.links:
                if l.detachable or l.parent is None:
                    part = Link(**{**l.__dict__, "parent": None, "origin_m": [0.0, 0.0, 0.0], "rpy_rad": [0.0, 0.0, 0.0], "joint": None})
                    self._write_urdf(d / "parts" / f"{l.id}.urdf", [part], solid=False, name=f"{self.slug}__{l.id}", mesh_prefix="../")
        manifest = self._manifest()
        (d / "geometry_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        proxies = {"asset_type_id": self.asset_type_id, "units": "m", "frame": "link_local",
                   "note_zh": "按 link 分解的接触代理：model.urdf 用 collision（空心容器为壁环盒 + 底盘），model_solid.urdf 用 solid_collision（每个 link 一个实心基元，适合只记账、不倒液的存放容器）。",
                   "components": {l.id: {"origin_m": l.origin_m, "parent": l.parent or "base_link", "joint": (l.joint or {}).get("type"),
                                         "primitives": [p.to_json() for p in l.collision],
                                         "solid_primitives": [p.to_json() for p in l.solid_collision]} for l in self.links}}
        (d / "collision_proxies.json").write_text(json.dumps(proxies, ensure_ascii=False, indent=1), encoding="utf-8")
        return manifest

    def _write_urdf(self, path: Path, links: list[Link], solid: bool, name: str | None = None, mesh_prefix: str = ""):
        robot = ET.Element("robot", name=name or self.slug)
        single = len(links) == 1 and links[0].parent is None and name is not None
        if not single:
            ET.SubElement(robot, "link", name="base_link")
        for l in links:
            link = ET.SubElement(robot, "link", name=l.id)
            inertial = ET.SubElement(link, "inertial")
            vm = l.visual_mesh()
            com = vm.center_mass if vm.is_watertight and vm.volume > 1e-12 else vm.bounds.mean(axis=0)
            ET.SubElement(inertial, "origin", xyz=" ".join(f"{v:.6g}" for v in com), rpy="0 0 0")
            ET.SubElement(inertial, "mass", value=f"{l.mass_kg:.6g}")
            ext = vm.bounding_box.extents
            m = l.mass_kg
            ixx = m / 12 * (ext[1] ** 2 + ext[2] ** 2); iyy = m / 12 * (ext[0] ** 2 + ext[2] ** 2); izz = m / 12 * (ext[0] ** 2 + ext[1] ** 2)
            ET.SubElement(inertial, "inertia", ixx=f"{ixx:.6g}", ixy="0", ixz="0", iyy=f"{iyy:.6g}", iyz="0", izz=f"{izz:.6g}")
            for oname, _m, rgba, opts in self.elements(l):
                vis = ET.SubElement(link, "visual", name=oname)
                ET.SubElement(vis, "origin", xyz="0 0 0", rpy="0 0 0")
                g = ET.SubElement(vis, "geometry")
                ET.SubElement(g, "mesh", filename=f"{mesh_prefix}meshes/{oname}.obj")
                mat = ET.SubElement(vis, "material", name=f"mat_{oname}")
                if opts.get("texture"):
                    ET.SubElement(mat, "color", rgba="1 1 1 1")
                    ET.SubElement(mat, "texture", filename=f"{mesh_prefix}meshes/textures/{Path(opts['texture']).name}")
                else:
                    r, gg, b, a = rgba
                    ET.SubElement(mat, "color", rgba=f"{srgb_to_linear(r):.5f} {srgb_to_linear(gg):.5f} {srgb_to_linear(b):.5f} {a:.3f}")
            prims = (l.solid_collision or l.collision) if solid else l.collision
            for p in prims:
                col = ET.SubElement(link, "collision")
                ET.SubElement(col, "origin", xyz=" ".join(f"{v:.6g}" for v in p.origin_m), rpy=" ".join(f"{v:.6g}" for v in p.rpy_rad))
                col.append(p.urdf_geometry())
            if single:
                continue
            j = l.joint or {}
            jt = j.get("type", "fixed")
            joint = ET.SubElement(robot, "joint", name=f"{l.id}_joint", type=jt)
            ET.SubElement(joint, "parent", link=l.parent or "base_link")
            ET.SubElement(joint, "child", link=l.id)
            ET.SubElement(joint, "origin", xyz=" ".join(f"{v:.6g}" for v in l.origin_m), rpy=" ".join(f"{v:.6g}" for v in l.rpy_rad))
            if jt != "fixed":
                ET.SubElement(joint, "axis", xyz=" ".join(f"{v:.6g}" for v in j["axis"]))
                ET.SubElement(joint, "limit", lower=f"{j['limits'][0]:.10g}", upper=f"{j['limits'][1]:.10g}", effort=str(j.get("effort", 10)), velocity="1")
                ET.SubElement(joint, "dynamics", damping=str(j.get("damping", 0.5)), friction="0.0")
        ET.indent(robot)
        path.write_text('<?xml version="1.0"?>\n' + ET.tostring(robot, encoding="unicode") + "\n", encoding="utf-8")

    def _manifest(self) -> dict:
        b = self.bounds()
        size = (b[1] - b[0]).round(5).tolist()
        joint_defs = {l.id: {"type": l.joint["type"], "axis": l.joint["axis"], ("limits_rad" if l.joint["type"] == "revolute" else "limits_m"): l.joint["limits"],
                             "accessor": f"joint[{l.id}_joint]", "state_id": l.joint.get("state_id"), "range_evidence": l.joint.get("evidence", "assumption"),
                             "note_zh": l.joint.get("note_zh", "")}
                      for l in self.links if l.joint and l.joint["type"] != "fixed"}
        arts = {"model_glb": "model.glb", "model_urdf": "model.urdf", "meshes_dir": "meshes", "model_blend": None}
        if (self.asset_dir / "model_solid.urdf").exists():
            arts["model_solid_urdf"] = "model_solid.urdf"
        for l in self.links:
            if (self.asset_dir / "parts" / f"{l.id}.urdf").exists():
                arts[f"part_{l.id}_urdf"] = f"parts/{l.id}.urdf"
        return {
            "asset_type_id": self.asset_type_id, "catalog_id": None, "name_zh": self.name_zh, "name_en": self.name_en,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "builder": "scripts/labware/build_labware.py", "builder_backend": "trimesh revolved shells / primitives (labware_kit.py)",
            "coordinate_system": {"unit": "m", "angle_unit": "rad", "up_axis": "+Z", "back_axis": "+Y", "handedness": "right",
                                  "root_origin_zh": "底面占地中心，z=0 为底面（放置面）；+X 向右，+Y 向后，正面朝 −Y",
                                  "glb_coordinate_conversion_zh": "GLB 按本项目 +Z 向上导出，无 Y-up 转换；节点层级与 URDF link 一一对应。"},
            "overall_dimensions_m": size, "bounds_m": b.round(5).tolist(),
            "total_mass_kg": round(sum(l.mass_kg for l in self.links), 5),
            "mass_evidence": "+".join(sorted({l.mass_evidence for l in self.links})),
            "sources": [{"source_id": k, **v} for k, v in self.sources.items()],
            "components": [{
                "id": l.id, "name_zh": l.name_zh, "function_zh": l.function_zh, "physical_meaning_zh": l.physical_meaning_zh,
                "parent": l.parent or "root", "origin_m": [float(v) for v in l.origin_m], "rpy_rad": [float(v) for v in l.rpy_rad],
                "joint": l.joint, "mass_kg": round(l.mass_kg, 6), "mass_evidence": l.mass_evidence, "mass_note_zh": l.mass_note_zh,
                "material": l.material, "geometry_evidence": l.geometry_evidence, "geometry_note_zh": l.geometry_note_zh,
                "objects": [e[0] for e in self.elements(l)], "detachable": l.detachable,
                "appearance": [{"object": n, "rgba_srgb": [round(float(c), 4) for c in rgba], "hex": "#%02X%02X%02X" % tuple(int(round(255 * c)) for c in rgba[:3]),
                                "rgba_linear": [round(srgb_to_linear(float(c)), 5) for c in rgba[:3]] + [round(float(rgba[3]), 3)],
                                **{kk: vv for kk, vv in o.items() if kk in ("ral", "source_id", "note_zh", "texture_licence")},
                                **({"texture": f"meshes/textures/{Path(o['texture']).name}"} if o.get("texture") else {})}
                               for n, _m, rgba, o in self.elements(l)],
                "collisions": [p.to_json() for p in l.collision]} for l in self.links],
            "root_object": f"{self.slug}__root", "joint_definitions": joint_defs, "artifacts": arts,
            "ports": self.ports, "labware": self.labware,
            "limitations_zh": self.limitations_zh,
        }

    # ------------------------------------------------------------------------------------------------------ contract
    def write_contract(self) -> dict:
        d = self.asset_dir
        rel = self.rel_dir

        def art(path: str, fmt: str):
            f = d / path
            return {"path": f"{rel}/{path}", "format": fmt, "status": "available", "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}

        artifacts = {"model_glb": art("model.glb", "glb"), "model_urdf": art("model.urdf", "urdf"),
                     "geometry_manifest": art("geometry_manifest.json", "json"), "collision_proxies": art("collision_proxies.json", "json")}
        if (d / "model_solid.urdf").exists():
            artifacts["model_solid_urdf"] = art("model_solid.urdf", "urdf")
        for l in self.links:
            if (d / "parts" / f"{l.id}.urdf").exists():
                artifacts[f"part_{l.id}_urdf"] = art(f"parts/{l.id}.urdf", "urdf")
        components = {}
        for l in self.links:
            geo = [{"artifact_id": "model_urdf", "selector": f"link:{l.id}", "role": "both"},
                   {"artifact_id": "model_glb", "selector": f"{self.slug}__{l.id}", "role": "visual"}]
            props = dict(self.properties.get(l.id, {}))
            props.setdefault("mass_kg", {"value": round(l.mass_kg, 6), "unit": "kg", "meaning_zh": "该 link 的仿真质量" + (f"（{l.mass_note_zh}）" if l.mass_note_zh else ""),
                                         "evidence": l.mass_evidence if l.mass_evidence in ("documented", "measured", "photo_estimate", "assumption", "unknown") else "assumption",
                                         "source_ids": [s for s in self.properties.get("_mass_sources", {}).get(l.id, []) if s in self.sources], "uncertainty": None})
            if props["mass_kg"]["evidence"] in ("documented", "measured", "photo_estimate") and not props["mass_kg"]["source_ids"]:
                props["mass_kg"]["evidence"] = "assumption"
            components[l.id] = {"name_zh": l.name_zh, "function_zh": l.function_zh, "physical_meaning_zh": l.physical_meaning_zh,
                                "parent": l.parent, "geometry_status": "bound", "geometry": geo, "capability_ids": [],
                                "passive_reason": self._passive_reason(l), "properties": props}
        model_bindings = {}
        for l in self.links:
            if l.joint and l.joint["type"] != "fixed":
                model_bindings[f"joint_{l.id}"] = {
                    "component_id": l.id, "artifact_id": "model_urdf", "target": f"{l.id}_joint", "channel": "joint", "attribute": "position",
                    "unit": "rad" if l.joint["type"] == "revolute" else "m", "limits": [float(v) for v in l.joint["limits"]], "state_id": None,
                    "mapping_zh": l.joint.get("note_zh") or "被动关节：由场景接触或宿主动作驱动，资产本身没有执行器。",
                    "authority": "none", "direction": "none", "sync_status": "unbound", "source_ids": [s for s in l.joint.get("source_ids", []) if s in self.sources]}
        contract = {
            "schema_version": "1.0.0", "asset_type_id": self.asset_type_id, "asset_version": "1.0.0", "name_zh": self.name_zh,
            "category": self.category, "asset_kind": self.asset_kind, "coverage": "full", "description_zh": self.description_zh,
            "coordinate_system": {"status": "defined", "length_unit": "m", "angle_unit": "rad", "handedness": "right", "up_axis": "+Z",
                                  "frame": "资产根坐标：底面占地中心，z=0 为放置面；+X 向右，+Y 向后，正面朝 −Y；四元数按 (x, y, z, w)"},
            "artifacts": artifacts,
            "sources": {k: {"kind": v["kind"], "reference": v["reference"], "locator": v["locator"], "note": v["note"]} for k, v in self.sources.items()},
            "components": components, "state_slots": self.state_slots, "capabilities": {}, "model_bindings": model_bindings,
            "ports": self.contract_ports, "mechanisms": self.mechanisms,
            "limitations": self.limitations_zh or ["被动资产：没有主动硬件 API；抓取、开盖、倾倒和搬运属于机器人技能与宿主记账。"],
            "extensions": {"chemistry_lab.labware": {k: v for k, v in self.labware.items()}},
        }
        (d / "asset.contract.json").write_text(json.dumps(contract, ensure_ascii=False, indent=1), encoding="utf-8")
        return contract

    def _passive_reason(self, l: Link) -> str:
        if self.category.startswith("furniture"):
            return "家具构件没有执行器或传感器；门、抽屉、盖由机器人或人推动（被动关节），存放位只记录位置与容纳关系。"
        if l.joint and l.joint["type"] != "fixed":
            return "被动部件：开合/旋下由机器人抓握+意图触发（touch + intent），资产本身没有执行器、传感器或自驱动 API。"
        return "被动容器/器具：只提供几何、容积、质量与抓取/放置坐标；盛装物由宿主的物质账本记录，倾倒和搬运属于机器人技能。"


# ---------------------------------------------------------------------------------------------------------- helpers
def frame(pos, link: str = "base_link", rot=None, **extra) -> dict:
    out = {"link": link, "pos_m": [round(float(v), 5) for v in pos], "rot_xyzw": list(rot or TOP_DOWN)}
    out.update(extra)
    return out


def top_down_frames(grasp_z: float, top_z: float, width_m: float, yaw_free: bool = True, link: str = "base_link",
                    xy=(0.0, 0.0), contact_z=None, note_zh: str = "") -> dict:
    """grasp / pre_grasp / post_grasp / place / pre_place for a top-down grasp at height grasp_z (object frame)."""
    clear = max(grasp_z + PRE_GRASP_LIFT_M, top_z + HAND_ABOVE_TCP_M + 0.05)
    g = frame([xy[0], xy[1], grasp_z], link, width_m=round(width_m, 5), franka_ok=bool(width_m <= FRANKA_MAX_GRASP_M),
              yaw_free=yaw_free, hand_bottom_z_m=round(grasp_z + HAND_ABOVE_TCP_M, 5), note_zh=note_zh)
    if contact_z is not None:
        g["finger_contact_z_m"] = [round(contact_z[0], 5), round(contact_z[1], 5)]
    return {"grasp": g,
            "pre_grasp": frame([xy[0], xy[1], clear], link, note_zh="抓取前悬停点：竖直下降到 grasp"),
            "post_grasp": frame([xy[0], xy[1], clear], link, note_zh="抓取后竖直抬起到此"),
            "place": frame([xy[0], xy[1], grasp_z], link, note_zh="放置时 TCP 在物体坐标中的位置（= grasp）；目标位姿 = 存放位/仪器位 ⊗ place"),
            "pre_place": frame([xy[0], xy[1], clear], link, note_zh="放置前悬停点")}


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
