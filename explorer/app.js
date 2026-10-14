// ChemLab-Assets Explorer: a static three.js viewer for the ChemLab-Assets dataset (Hugging Face Space, sdk: static).
// Adapted from the in-house asset inspector. The per-instrument bundles in data/ are pre-built; 3D models, preview
// renders and downloadable files are read straight from the dataset repository (DATA_BASE).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

/* ======================================================================================= configuration */
const DATASET_ID = 'MBZUAI/ChemLab-Assets';
const DATASET_URL = `https://huggingface.co/datasets/${DATASET_ID}`;
const DEFAULT_DATA_BASE = 'https://huggingface.co/datasets/MBZUAI/ChemLab-Assets/resolve/main/';
const SPACE_ID = 'MBZUAI/ChemLab-Assets-Explorer';
const WEBSITE_URL = 'https://mbzuai-nlp.github.io/ChemLab-Assets/';                          // project website; the header link stays hidden while this is empty
const DEFAULT_TID = 'instrument.esco_efa5uduvw';
const HUB_ORIGIN = 'https://huggingface.co';     // the Space page that embeds this app in an iframe

const QUERY = new URLSearchParams(location.search);
// ?base=<url> reads models, previews and files from another copy of the dataset (used for local testing).
const DATA_BASE = (() => {
  const raw = QUERY.get('base');
  if (raw) {
    try {
      const u = new URL(raw, location.href);
      if (u.protocol === 'https:' || u.protocol === 'http:') return u.href.endsWith('/') ? u.href : `${u.href}/`;
    } catch { /* not a URL */ }
    console.warn('Ignoring ?base=: only http(s) URLs are accepted.');
  }
  return DEFAULT_DATA_BASE;
})();
const ON_HUB = DATA_BASE === DEFAULT_DATA_BASE;
const encPath = (p) => String(p).split('/').map(encodeURIComponent).join('/');
const dataUrl = (p) => DATA_BASE + encPath(p);
const downloadUrl = (p) => dataUrl(p) + (ON_HUB ? '?download=true' : '');
const hubTree = (p) => `${DATASET_URL}/tree/main/${encPath(p)}`;
const hubBlob = (p) => `${DATASET_URL}/blob/main/${encPath(p)}`;
const thumbUrl = (slug) => `media/thumb/${encodeURIComponent(slug)}.webp`;

/* ======================================================================================= small helpers */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const num = (v, d = 3) => (!isNum(v) ? '—' : Number.isInteger(v) ? String(v) : v.toFixed(d).replace(/\.?0+$/, '').replace(/^-0$/, '0'));
const NA = '<span class="faint">—</span>';
const fmt = (v, d = 3) => (v == null || v === '' ? NA : Array.isArray(v) ? (v.length ? v.map((x) => (isNum(x) ? num(x, d) : esc(x))).join(', ') : NA)
  : isNum(v) ? num(v, d) : typeof v === 'boolean' ? (v ? 'yes' : 'no') : typeof v === 'object' ? esc(JSON.stringify(v)) : esc(v));
const vec = (v, d = 3) => (Array.isArray(v) && v.length ? `(${v.map((x) => num(x, d)).join(', ')})` : '—');
const mmv = (v) => (isNum(v) ? String(Math.round(v * 1000)) : '—');
const sizeMM = (s) => (Array.isArray(s) && s.length === 3 && s.every(isNum) ? `${s.map(mmv).join(' × ')} mm` : '—');
const deg = (r) => (isNum(r) ? `${(r * 180 / Math.PI).toFixed(1)}°` : '—');
const bytes = (n) => (!isNum(n) ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB` : `${(n / 1048576).toFixed(n < 104857600 ? 1 : 0)} MB`);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const ACRONYM = { rtx: 'RTX', gc: 'GC', hplc: 'HPLC', usd: 'USD', ir: 'IR', uv: 'UV', ms: 'MS', nmr: 'NMR', fid: 'FID', lcd: 'LCD', led: 'LED', xyz: 'XYZ', icp: 'ICP', tem: 'TEM', sem: 'SEM', xrd: 'XRD', ftir: 'FTIR', dsc: 'DSC', tga: 'TGA', pcr: 'PCR', csh: 'CSH', glb: 'GLB', urdf: 'URDF', vfd: 'VFD', ph: 'pH' };
const humanize = (s) => {
  const t = String(s ?? '').replace(/_+/g, ' ').replace(/\s+/g, ' ').trim().split(' ').map((w) => ACRONYM[w.toLowerCase()] || w).join(' ');
  return t ? t[0].toUpperCase() + t.slice(1) : '';
};
const zh = (s, tag = 'span', cls = '') => (s ? `<${tag} lang="zh"${cls ? ` class="${cls}"` : ''}>${esc(s)}</${tag}>` : '');
const countBy = (arr, k) => arr.reduce((m, x) => { const v = x?.[k]; if (v != null) m[v] = (m[v] || 0) + 1; return m; }, {});
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const narrowMQ = matchMedia('(max-width: 899.98px)');
const darkMQ = matchMedia('(prefers-color-scheme: dark)');
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};
const ICON = {
  download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19.5h14" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  sun: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 2.5v2.3M12 19.2v2.3M2.5 12h2.3M19.2 12h2.3M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  moon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.5 14.6A8 8 0 0 1 9.4 4.5a8 8 0 1 0 10.1 10.1Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  auto: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor"/></svg>',
};

/* ---------------------------------------------------------------------------- vocabulary (English labels) */
const LABEL = {
  documented: 'Documented', manufacturer_datasheet: 'Manufacturer datasheet', manufacturer_verified: 'Manufacturer verified',
  manufacturer_specified: 'Manufacturer specified', manufacturer_drawing: 'Manufacturer drawing', manufacturer_dimensions: 'Manufacturer dimensions',
  photo_estimate: 'Photo estimate', photo_inference: 'Photo inference', engineering_assumption: 'Engineering assumption',
  simulation_assumption: 'Simulation assumption', assumption: 'Assumption', assumed: 'Assumed', estimated: 'Estimated',
  task_envelope: 'Task envelope', derived: 'Derived', derived_documented: 'Derived from documents', unknown: 'Unknown',
  not_calibrated: 'Not calibrated', schematic: 'Schematic', internal_schematic: 'Internal schematic', drawing_estimate: 'Drawing estimate',
  implemented: 'Implemented', verified: 'Verified', declared: 'Declared', unsupported: 'Unsupported', absent: 'Absent',
  not_applicable: 'Not applicable', passed: 'Passed', failed: 'Failed', not_run: 'Not run',
  full: 'Full port', partial: 'Partial port', bridged: 'Bridged', agent: 'Agent', evaluator: 'Evaluator',
  read_only: 'Read-only', read_write: 'Read / write', setpoint: 'Setpoint', process_truth: 'Process truth', sensor: 'Sensor', scene: 'Scene',
  lumped: 'Lumped model', numerical: 'Numerical model', qualitative: 'Qualitative',
};
const TONE = {
  implemented: 'good', verified: 'good', passed: 'good', full: 'good', documented: 'good', declared: 'warn', partial: 'warn',
  unsupported: 'bad', failed: 'bad', absent: 'neutral', not_applicable: 'neutral', not_run: 'neutral', unknown: 'neutral',
  not_calibrated: 'neutral', bridged: 'info', agent: 'info', evaluator: 'neutral', lumped: 'neutral', numerical: 'neutral', qualitative: 'neutral',
};
function toneOf(v) {
  const s = String(v ?? '').toLowerCase();
  if (TONE[s]) return TONE[s];
  if (!s || /unknown|not_/.test(s)) return 'neutral';
  if (/assum|estimat|approx|photo|schematic|proxy|envelope|drawing|inferen/.test(s)) return 'warn';
  if (/manufacturer|documented|verified|derived|measured|specified/.test(s)) return 'good';
  return 'neutral';
}
const label = (v) => LABEL[v] || humanize(v);
const pill = (v, text) => (v == null || v === '' ? '' : `<span class="pill t-${toneOf(v)}" title="${esc(v)}">${esc(text || label(v))}</span>`);
const ISAAC = {
  full: 'Behaviour re-implemented natively in Isaac Lab (through Matterix) and checked step by step against the Python runtime.',
  partial: 'Core behaviour re-implemented natively in Isaac Lab (through Matterix); some capabilities are not ported.',
  bridged: 'The Python runtime drives the Isaac Lab scene through the Matterix bridge.',
};
const isaacPill = (st, chip = false) => (st ? `<span class="${chip ? 'chip' : 'pill'} ${chip ? '' : `t-${toneOf(st)}`}" title="${esc(ISAAC[st] || st)}">Isaac Lab · ${esc(label(st))}</span>` : '');
const KIND = { door: 'Door / lid', knob: 'Knob', button: 'Button', rotor: 'Rotor', other: 'Other' };
const KIND_GROUP = { door: 'Doors & lids', knob: 'Knobs', rotor: 'Rotors', other: 'Other joints', button: 'Buttons' };
const KIND_ORDER = ['door', 'knob', 'rotor', 'other', 'button'];
const KIND_PL = { door: ['door/lid', 'doors/lids'], knob: ['knob', 'knobs'], button: ['button', 'buttons'], rotor: ['rotor', 'rotors'], other: ['other joint', 'other joints'] };
const kindOf = (j) => (KIND[j.kind] ? j.kind : 'other');
const kindText = (n, k) => { const p = KIND_PL[k] || [k, k]; return `${n} ${n === 1 ? p[0] : p[1]}`; };
const rangeText = (j) => (j.type === 'prismatic' ? `${num((j.lower ?? 0) * 1000, 1)} – ${num((j.upper ?? 0) * 1000, 1)} mm`
  : j.type === 'continuous' ? 'continuous' : `${deg(j.lower)} – ${deg(j.upper)}`);
const LEVELS = ['process', 'host', 'scene', 'robot', 'hardware'];
const LAYERS = [
  { key: 'mesh', label: 'Mesh', on: true, group: 'Model' },
  { key: 'wire', label: 'Wireframe', on: false, group: 'Model' },
  { key: 'ghost', label: 'X-ray', on: false, group: 'Model' },
  { key: 'collision', label: 'Collision proxies', on: false, group: 'Annotations', sw: ['collision'] },
  { key: 'joints', label: 'Joint axes', on: true, group: 'Annotations', sw: ['door', 'rotor', 'other'] },
  { key: 'buttons', label: 'Buttons & knobs', on: false, group: 'Annotations', sw: ['button', 'knob'] },
  { key: 'slots', label: 'Interface points', on: true, group: 'Annotations', sw: ['slot'] },
  { key: 'readouts', label: 'Panel readouts', on: false, group: 'Annotations', sw: ['readout'] },
  { key: 'frames', label: 'Placement frames', on: false, group: 'Annotations' },
  { key: 'layout', label: 'Footprint & clearance', on: false, group: 'Annotations', sw: ['frame', 'layout'] },
  { key: 'bbox', label: 'Bounding box', on: false, group: 'Annotations', sw: ['frame', 'measured'] },
  { key: 'axes', label: 'Axes', on: true, group: 'Annotations' },
  { key: 'labels', label: 'Labels', on: true, group: 'Annotations' },
];

/* ======================================================================================= state */
const S = {
  index: [], domains: [], byTid: new Map(), domainMap: new Map(),
  search: '', domain: '', view: 'viewer', tab: 'overview',
  tid: null, bundle: null, bundleError: null, seq: 0, selected: null, modelReady: false, bounds: null, orientation: null,
  layers: Object.fromEntries(LAYERS.map((l) => [l.key, l.on])),
  jointValues: {}, jointsOpen: false, compFilter: '', capFilter: '', sort: { key: 'chem_id', dir: 1 },
};
const domainName = (k) => S.domainMap.get(k)?.name || humanize(k || 'other');

/* ======================================================================================= theme */
const THEME_KEY = 'chemlab-explorer-theme';
const themeMode = () => { const t = document.documentElement.dataset.theme; return t === 'light' || t === 'dark' ? t : 'system'; };
const effectiveTheme = () => (themeMode() === 'system' ? (darkMQ.matches ? 'dark' : 'light') : themeMode());
function updateThemeButton() {
  const m = themeMode(), next = { system: 'light', light: 'dark', dark: 'system' }[m];
  const btn = $('#theme-btn');
  btn.innerHTML = m === 'light' ? ICON.sun : m === 'dark' ? ICON.moon : ICON.auto;
  const now = m === 'system' ? `follows your system (${effectiveTheme()})` : m;
  btn.setAttribute('aria-label', `Colour theme: ${now}. Switch to ${next === 'system' ? 'system setting' : next}.`);
  btn.title = `Theme: ${now}`;
}
function setThemeMode(m) {
  if (m === 'system') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = m;
  store.set(THEME_KEY, m === 'system' ? null : m);
  themeChanged();
}
function themeChanged() {
  updateThemeButton();
  if (GL) { readColors(); repaint(); }
}

/* ======================================================================================= three.js setup */
const canvas = $('#gl');
let GL = null;
try {
  GL = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
} catch (e) {
  console.warn('WebGL is not available:', e?.message || e);
}
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 0.005, 200);
camera.up.set(0, 0, 1);
camera.position.set(1.2, -1.2, 0.9);
let controls = null;
const assetRoot = new THREE.Group();   // the asset frame: Z up, metres (URDF / contract frame)
const modelWrap = new THREE.Group();   // holds the glTF scene, rotated +90° about X when the GLB is glTF Y-up
assetRoot.add(modelWrap);
scene.add(assetRoot);
const overlay = Object.fromEntries(['collision', 'joints', 'buttons', 'slots', 'readouts', 'frames', 'layout', 'bbox', 'axes'].map((k) => [k, new THREE.Group()]));
for (const g of Object.values(overlay)) assetRoot.add(g);
let model = null, meshes = [], nodeToComp = new Map(), compMeshes = new Map(), pivots = new Map();
let grid = null, gridDim = 1, DASH = 0.02;
const COL = {};
const OV_ROLES = ['collision', 'collision-edge', 'door', 'knob', 'button', 'rotor', 'other', 'slot', 'readout', 'readout-edge', 'frame', 'measured', 'layout', 'select'];
const loader = new GLTFLoader();
let dirty = true;
const invalidate = () => { dirty = true; };

function readColors() {
  const cs = getComputedStyle(document.documentElement);
  const get = (name, fallback) => { const v = cs.getPropertyValue(name).trim(); try { return new THREE.Color(v || fallback); } catch { return new THREE.Color(fallback); } };
  for (const r of OV_ROLES) COL[r] = get(`--ov-${r}`, '#888888');
  COL.gridMajor = get('--grid-major', '#aab7c0');
  COL.gridMinor = get('--grid-minor', '#ccd5db');
}
// Studio environment map (built Z-up like the rest of the view): without one, PBR metals reflect nothing and render
// almost black. Metallic materials use it fully, non-metals only lightly (see setupModel).
function studioEnvironment() {
  const env = new THREE.Scene();
  const dome = new THREE.SphereGeometry(40, 48, 24);
  const col = [];
  const p = dome.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const t = THREE.MathUtils.clamp((p.getZ(i) / 40 + 1) / 2, 0, 1);         // 0 = floor, 1 = ceiling (world +Z)
    const v = t < 0.5 ? 0.16 + 0.5 * t : 0.41 + 1.0 * (t - 0.5);
    col.push(v, v * 1.02, v * 1.06);
  }
  dome.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  env.add(new THREE.Mesh(dome, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide })));
  for (const [pos, w, h] of [[[-12, -18, 22], 16, 10], [[18, -10, 16], 10, 8], [[0, 20, 25], 20, 6]]) {   // soft boxes
    const panel = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }));
    panel.position.set(...pos);
    panel.lookAt(0, 0, 0);
    env.add(panel);
  }
  const pmrem = new THREE.PMREMGenerator(GL);
  const tex = pmrem.fromScene(env, 0.03).texture;
  pmrem.dispose();
  env.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
  return tex;
}
function buildGrid(maxDim) {
  gridDim = maxDim;
  if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose(); }
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(maxDim, 0.02))));
  const cell = p / 5, div = 60;                  // 20 cm cells for a 1–10 m instrument, 2 cm for 0.1–1 m
  grid = new THREE.GridHelper(cell * div, div, COL.gridMajor, COL.gridMinor);
  grid.rotation.x = Math.PI / 2;
  grid.material.transparent = true;
  grid.material.opacity = 0.8;
  grid.material.depthWrite = false;
  grid.renderOrder = -1;
  scene.add(grid);
  invalidate();
}
if (GL) {
  GL.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  GL.outputColorSpace = THREE.SRGBColorSpace;
  GL.setClearColor(0x000000, 0);
  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = !reducedMotion.matches;
  controls.dampingFactor = 0.12;
  controls.addEventListener('change', invalidate);
  reducedMotion.addEventListener?.('change', () => { controls.enableDamping = !reducedMotion.matches; });
  scene.add(new THREE.HemisphereLight(0xffffff, 0x9aa3b0, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(2, -3, 4);
  scene.add(sun);
  const fill = new THREE.DirectionalLight(0xffffff, 0.5);
  fill.position.set(-3, 2, 1);
  scene.add(fill);
  scene.environment = studioEnvironment();
  readColors();
  buildGrid(1);
  new ResizeObserver(invalidate).observe($('#stage'));
  requestAnimationFrame(frame);
}

function frame() {
  requestAnimationFrame(frame);
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;                          // table view or collapsed layout
  const pr = Math.min(window.devicePixelRatio || 1, 2);
  if (GL.getPixelRatio() !== pr) { GL.setPixelRatio(pr); dirty = true; }
  if (canvas.width !== Math.floor(w * pr) || canvas.height !== Math.floor(h * pr)) {
    GL.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    dirty = true;
  }
  if (controls.update()) dirty = true;            // true while damping settles
  if (!dirty) return;                            // render on demand: an idle page costs nothing
  dirty = false;
  GL.render(scene, camera);
  updateLabels(w, h);
}

/* ---------------------------------------------------------------------------- HTML labels pinned to 3D points */
const labels = [];
const labelLayer = $('#labels');
const _v = new THREE.Vector3();
function addLabel(l) { labels.push(l); }
function clearLabels() { for (const l of labels) l.el?.remove(); labels.length = 0; }
function updateLabels(w, h) {
  const show = S.layers.labels && S.modelReady;
  const k = (h / 2) / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  for (const l of labels) {
    let on = show && (!l.layer || S.layers[l.layer]);
    if (on) {
      if (l.object) l.object.getWorldPosition(_v); else _v.copy(l.pos);
      if (l.sizeWorld) {                           // hide labels of things that are tiny on screen (keypads, vial racks)
        const px = l.sizeWorld * k / Math.max(_v.distanceTo(camera.position), 1e-4);
        if (px < (l.minPx || 40)) on = false;
      }
    }
    if (on) {
      _v.project(camera);
      if (_v.z > 1 || Math.abs(_v.x) > 1.02 || Math.abs(_v.y) > 1.02) on = false;
    }
    if (!on) { if (l.el && l.el.style.display !== 'none') l.el.style.display = 'none'; continue; }
    if (!l.el) {
      l.el = document.createElement('div');
      l.el.className = `lbl ${l.cls || ''}`;
      l.el.textContent = l.text;
      l.el.title = l.title || l.text;
      if (l.onclick) l.el.addEventListener('click', l.onclick);
      labelLayer.appendChild(l.el);
    }
    l.el.style.display = '';
    l.el.style.transform = `translate(${((_v.x + 1) / 2 * w).toFixed(1)}px, ${((1 - _v.y) / 2 * h).toFixed(1)}px) translate(-50%, -135%)`;
    l.el.classList.toggle('sel', !!l.compId && l.compId === S.selected);
  }
}

/* ---------------------------------------------------------------------------- geometry helpers */
const matsOf = (m) => (Array.isArray(m.material) ? m.material : [m.material]);
function modelBounds() {
  const b = new THREE.Box3();
  for (const m of meshes) b.expandByObject(m, true);
  return b;
}
// The dataset frame puts every instrument's front towards −Y (Z up). Camera views and axis names use that convention;
// layout.front is only drawn by the layout overlay, because one asset (sft_sft110) declares +X there while its own
// coordinate system and readouts face −Y.
const frontVec = () => new THREE.Vector3(0, -1, 0);
function layoutFront() {
  const fr = S.bundle?.layout?.front;
  const v = Array.isArray(fr) ? new THREE.Vector3(fr[0] || 0, fr[1] || 0, 0) : new THREE.Vector3(0, -1, 0);
  return v.lengthSq() > 1e-8 ? v.normalize() : new THREE.Vector3(0, -1, 0);
}
const UP = new THREE.Vector3(0, 0, 1);
const rightVec = (f = frontVec()) => new THREE.Vector3().crossVectors(UP, f).normalize();   // viewer's right when facing the front
// camera directions in (right, front, up) components
const CAM = { iso: [1, 1, 0.8], front: [0, 1, 0.3], back: [0, -1, 0.3], left: [-1, 0, 0.3], right: [1, 0, 0.3], top: [0, 0.0001, 1] };
function viewDir(name) {
  const f = frontVec(), r = rightVec(f), [a, b, c] = CAM[name] || CAM.iso;
  return r.multiplyScalar(a).addScaledVector(f, b).addScaledVector(UP, c).normalize();
}
function fitDistance(radius, margin) {
  const v = THREE.MathUtils.degToRad(camera.fov / 2);
  const hz = Math.atan(Math.tan(v) * camera.aspect);
  return radius / Math.sin(Math.min(v, hz)) * margin;
}
function fitView(name = 'iso') {
  if (!GL) return;
  const b = S.bounds && !S.bounds.isEmpty() ? S.bounds : new THREE.Box3(new THREE.Vector3(-0.3, -0.3, 0), new THREE.Vector3(0.3, 0.3, 0.5));
  const c = b.getCenter(new THREE.Vector3()), r = Math.max(b.getSize(new THREE.Vector3()).length() / 2, 0.01);
  const d = name === 'fit' ? camera.position.clone().sub(controls.target).normalize() : viewDir(name);
  const dist = fitDistance(r, 1.04);
  camera.position.copy(c).addScaledVector(d, dist);
  controls.target.copy(c);
  camera.near = Math.max(dist / 500, 0.0005);
  camera.far = dist * 40;
  camera.updateProjectionMatrix();
  controls.update();
  invalidate();
}
function focusOn(box) {
  if (!GL || !box || box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3()), r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.01);
  const d = camera.position.clone().sub(controls.target).normalize();
  const dist = fitDistance(r, 1.35);
  camera.position.copy(c).addScaledVector(d, dist);
  controls.target.copy(c);
  camera.near = Math.min(camera.near, Math.max(dist / 500, 0.0005));
  camera.updateProjectionMatrix();
  controls.update();
  invalidate();
}
const lineMat = (role, dashed = false) => (dashed
  ? new THREE.LineDashedMaterial({ color: COL[role], dashSize: DASH, gapSize: DASH * 0.6 })
  : new THREE.LineBasicMaterial({ color: COL[role] }));
const tagged = (o, role) => { o.userData.ov = role; return o; };
function mkEdges(geo, role) { return tagged(new THREE.LineSegments(new THREE.EdgesGeometry(geo), lineMat(role)), role); }
function mkArrow(origin, dir, len, role, head = len * 0.25) {
  const d = new THREE.Vector3(...(dir || [0, 0, 1]));
  if (d.lengthSq() < 1e-12) d.set(0, 0, 1);
  return tagged(new THREE.ArrowHelper(d.normalize(), new THREE.Vector3(...origin), len, COL[role], head, head * 0.6), role);
}
function mkRect(x0, y0, x1, y1, z, role, dashed = false) {
  const pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]].map(([x, y]) => new THREE.Vector3(x, y, z));
  const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(role, dashed));
  if (dashed) l.computeLineDistances();
  return tagged(l, role);
}
function mkBox(min, max, role, dashed = false) {
  const b = new THREE.Box3(new THREE.Vector3(...min), new THREE.Vector3(...max));
  const s = b.getSize(new THREE.Vector3()), c = b.getCenter(new THREE.Vector3());
  const g = new THREE.BoxGeometry(Math.max(s.x, 1e-4), Math.max(s.y, 1e-4), Math.max(s.z, 1e-4));
  const l = new THREE.LineSegments(new THREE.EdgesGeometry(g), lineMat(role, dashed));
  g.dispose();
  if (dashed) l.computeLineDistances();
  l.position.copy(c);
  return tagged(l, role);
}
const rpyEuler = (rpy) => { const [r, p, y] = rpy || [0, 0, 0]; return new THREE.Euler(r, p, y, 'ZYX'); };
function disposeTree(root) {
  root.traverse((o) => {
    o.geometry?.dispose();
    if (o.material) for (const m of (Array.isArray(o.material) ? o.material : [o.material])) {
      for (const v of Object.values(m)) if (v && v.isTexture) v.dispose();
      m.dispose();
    }
  });
}
// recolour annotations after a theme change (materials keep their role in userData.ov)
function repaint() {
  assetRoot.traverse((o) => {
    const c = COL[o.userData.ov];
    if (!c) return;
    if (o.type === 'ArrowHelper') o.setColor(c);
    else if (o.material?.color) o.material.color.copy(c);
  });
  buildGrid(gridDim);
  applyMaterials();
}

/* ======================================================================================= loading an instrument */
const bundleCache = new Map();
async function loadBundle(tid) {
  if (bundleCache.has(tid)) { const b = bundleCache.get(tid); bundleCache.delete(tid); bundleCache.set(tid, b); return b; }
  const r = await fetch(`data/assets/${encodeURIComponent(tid)}.json`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const b = normalizeBundle(await r.json());
  bundleCache.set(tid, b);
  while (bundleCache.size > 10) bundleCache.delete(bundleCache.keys().next().value);
  return b;
}
function normalizeBundle(b) {
  for (const k of ['joints', 'slots', 'readouts', 'components', 'files', 'previews', 'limitations', 'sources', 'hidden_parts']) if (!Array.isArray(b[k])) b[k] = [];
  for (const k of ['capabilities', 'state_slots', 'mechanisms', 'frames', 'links', 'controls', 'checks']) if (!b[k] || typeof b[k] !== 'object' || Array.isArray(b[k])) b[k] = {};
  for (const c of b.components) { if (!Array.isArray(c.collisions)) c.collisions = []; if (!Array.isArray(c.objects)) c.objects = []; }
  for (const j of b.joints) { if (!Array.isArray(j.axis)) j.axis = [0, 0, 1]; if (!Array.isArray(j.origin_m)) j.origin_m = [0, 0, 0]; }
  return b;
}

const glbCache = new Map();                     // url -> ArrayBuffer, least recently used first
let glbCacheBytes = 0, glbAbort = null;
const GLB_CACHE_LIMIT = 160 * 1048576;
function cacheGlb(url, buf) {
  if (buf.byteLength > GLB_CACHE_LIMIT / 3) return;
  glbCache.set(url, buf);
  glbCacheBytes += buf.byteLength;
  for (const [k, v] of glbCache) {
    if (glbCacheBytes <= GLB_CACHE_LIMIT) break;
    if (k === url) continue;
    glbCache.delete(k);
    glbCacheBytes -= v.byteLength;
  }
}
async function fetchBuffer(url, expected, onProgress, signal) {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ''}`);
  const total = expected || Number(res.headers.get('content-length')) || 0;
  if (!res.body?.getReader) { const b = await res.arrayBuffer(); onProgress(b.byteLength, total); return b; }
  const reader = res.body.getReader();
  const parts = [];
  let got = 0, last = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    got += value.byteLength;
    const now = performance.now();
    if (now - last > 50) { last = now; onProgress(got, total); }
  }
  onProgress(got, total);
  const out = new Uint8Array(got);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.byteLength; }
  return out.buffer;
}
function describeError(e) {
  const m = String(e?.message || e || 'Unknown error');
  if (/^HTTP 404/.test(m)) return 'The file was not found on the server (HTTP 404).';
  if (/^HTTP 40[13]/.test(m)) return `The server refused the download (${m}); the dataset may not be public yet.`;
  if (/^HTTP 429/.test(m)) return 'Too many downloads in a short time (HTTP 429). Wait a minute and try again.';
  if (/^HTTP/.test(m)) return `The server answered ${m}.`;
  if (/failed to fetch|networkerror|load failed/i.test(m)) return 'The network request failed: you may be offline, or a firewall may block the download.';
  return `${m.replace(/\.$/, '')}.`;
}

function setLoader(loaded, total, text) {
  if (text) $('#loader-text').textContent = text;
  const bar = $('#loader-bar'), track = bar.parentElement;
  if (total > 0) {
    track.classList.remove('indet');
    bar.style.width = `${clamp(loaded / total * 100, 0, 100).toFixed(1)}%`;
    $('#loader-bytes').textContent = loaded >= total && total > 1 ? bytes(total) : `${bytes(loaded) || '0 B'} of ${bytes(total)}`;
  } else {
    track.classList.add('indet');
    bar.style.width = '';
    $('#loader-bytes').textContent = loaded ? bytes(loaded) : '';
  }
}
function showLoader(a, text) {
  $('#viewer-error').hidden = true;
  const img = $('#loader-thumb');
  img.hidden = !a;
  if (a) img.src = thumbUrl(a.slug);
  $('#loader').hidden = false;
  setLoader(0, 0, text);
}
const hideLoader = () => { $('#loader').hidden = true; };
function showViewerError(title, msg, { retry = false, download = null } = {}) {
  hideLoader();
  const el = $('#viewer-error');
  el.innerHTML = `<h2>${esc(title)}</h2><p>${esc(msg)}</p>${retry || download ? `<div class="row">${retry ? '<button type="button" class="btn primary" data-act="retry">Try again</button>' : ''}${download ? `<a class="btn" href="${esc(download)}" target="_blank" rel="noopener">${ICON.download}Download the GLB</a>` : ''}</div>` : ''}`;
  el.hidden = false;
}

function clearAsset() {
  clearLabels();
  for (const g of Object.values(overlay)) { disposeTree(g); g.clear(); }
  const keep = new Set([modelWrap, ...Object.values(overlay)]);
  for (const o of [...assetRoot.children]) if (!keep.has(o)) { disposeTree(o); assetRoot.remove(o); }
  disposeTree(modelWrap);
  modelWrap.clear();
  modelWrap.rotation.set(0, 0, 0);
  model = null; meshes = []; nodeToComp = new Map(); compMeshes = new Map(); pivots = new Map();
  S.selected = null; S.jointValues = {}; S.modelReady = false; S.bounds = null; S.orientation = null;
  $('#pick-card').hidden = true;
  $('#jp-body').innerHTML = '';
  $('#joint-panel').hidden = true;
  invalidate();
}

async function selectInstrument(tid, { user = false } = {}) {
  const a = S.byTid.get(tid);
  if (!a) return;
  if (user) afterUserSelect();
  if (tid === S.tid && !S.bundleError) return;
  const seq = ++S.seq;
  glbAbort?.abort();
  S.tid = tid; S.bundle = null; S.bundleError = null;
  writeLocation(tid, user);
  markActive();
  renderTitle(a);
  clearAsset();
  renderTab();
  showLoader(a, 'Loading instrument data…');
  let b;
  try {
    b = await loadBundle(tid);
  } catch (e) {
    if (seq !== S.seq) return;
    S.bundleError = e;
    renderTab();
    showViewerError('This instrument could not be loaded', `Its data file could not be read. ${describeError(e)}`, { retry: true });
    return;
  }
  if (seq !== S.seq) return;
  S.bundle = b;
  renderTab();
  if (!GL) {
    showViewerError('3D view unavailable', 'WebGL is not available in this browser, so the model cannot be drawn. The details and downloads in the panel still work.', { download: b.glb ? downloadUrl(b.glb) : null });
    return;
  }
  await loadModel(b, seq);
}
async function loadModel(b, seq) {
  if (!b.glb) { showViewerError('No 3D model', 'This instrument has no GLB file in the release.'); return; }
  const url = dataUrl(b.glb), name = b.glb.split('/').pop();
  const expected = b.files.find((f) => f.path === b.glb)?.bytes || 0;
  showLoader(S.byTid.get(b.tid), `Downloading ${name}…`);
  setLoader(0, expected);
  try {
    let buf = glbCache.get(url);
    if (buf) { glbCache.delete(url); glbCache.set(url, buf); }
    else {
      glbAbort?.abort();
      const ctl = new AbortController();
      glbAbort = ctl;
      buf = await fetchBuffer(url, expected, (l, t) => { if (seq === S.seq) setLoader(l, t); }, ctl.signal);
      if (glbAbort === ctl) glbAbort = null;
      cacheGlb(url, buf);
    }
    if (seq !== S.seq) return;
    setLoader(1, 1, 'Building the scene…');
    await nextFrame();
    const gltf = await loader.parseAsync(buf, '');
    if (seq !== S.seq) { disposeTree(gltf.scene); return; }
    setupModel(b, gltf.scene);
    hideLoader();
    if (S.tab === 'joints' || S.tab === 'components') rerenderTab();
  } catch (e) {
    if (e?.name === 'AbortError' || seq !== S.seq) return;
    console.warn('GLB load failed:', e);
    let host = '';
    try { host = new URL(url).host; } catch { /* keep empty */ }
    showViewerError('The 3D model could not be loaded', `${describeError(e)}${host ? ` The model is read from ${host}.` : ''}`, { retry: true, download: downloadUrl(b.glb) });
  }
}

function setupModel(b, root) {
  model = root;
  modelWrap.add(model);
  model.traverse((o) => {
    if (!o.isMesh) return;
    o.material = Array.isArray(o.material) ? o.material.map((m) => m.clone()) : o.material.clone();
    for (const mm of matsOf(o)) {
      mm.envMapIntensity = (mm.metalness ?? 0) >= 0.5 ? 1.0 : 0.25;
      mm.userData.orig = { transparent: mm.transparent, opacity: mm.opacity, depthWrite: mm.depthWrite, emissive: mm.emissive ? mm.emissive.clone() : null, emissiveIntensity: mm.emissiveIntensity ?? 1 };
    }
    meshes.push(o);
  });
  // Orientation: glb_up says whether the GLB is glTF +Y-up (needs +90° about X) or already Z-up. A few flags in the
  // source data were wrong, so both placements are measured against the declared size and the better fit wins.
  const flagRot = b.glb_up === 'y' || (b.glb_up == null && b.glb_orientation === 'gltf_y_up_rotated');
  let useRot = flagRot, dRot = null, dFlat = null;
  if (Array.isArray(b.size_m) && b.size_m.length === 3 && b.size_m.every(isNum)) {
    const measure = (rx) => { modelWrap.rotation.set(rx, 0, 0); modelWrap.updateMatrixWorld(true); return modelBounds().getSize(new THREE.Vector3()).toArray(); };
    const dev = (sz) => Math.max(...sz.map((v, i) => Math.abs(v - b.size_m[i])));
    dRot = dev(measure(Math.PI / 2));
    dFlat = dev(measure(0));
    if (Math.abs(dRot - dFlat) > 0.002) useRot = dRot < dFlat;
  }
  modelWrap.rotation.set(useRot ? Math.PI / 2 : 0, 0, 0);
  modelWrap.updateMatrixWorld(true);
  S.orientation = { flag: flagRot ? 'y-up' : 'z-up', used: useRot ? 'y-up' : 'z-up', dRot, dFlat };
  mapComponents(b);
  buildPivots(b);
  S.bounds = modelBounds();
  const size = S.bounds.isEmpty() ? new THREE.Vector3(0.5, 0.5, 0.5) : S.bounds.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 0.02);
  DASH = clamp(maxDim * 0.012, 0.002, 0.03);
  buildGrid(maxDim);
  buildOverlays(b, maxDim);
  buildJointPanel(b);
  S.modelReady = true;
  applyLayers();
  fitView('iso');
}
const baseName = (n) => n.replace(/\.\d{3}$/, '');
function isAncestor(a, o) { let p = o.parent; while (p) { if (p === a) return true; p = p.parent; } return false; }
function mapComponents(b) {
  const byName = new Map();
  model.traverse((o) => { if (o.name) { byName.set(o.name, o); if (!byName.has(baseName(o.name))) byName.set(baseName(o.name), o); } });
  for (const c of b.components) {
    const objs = [];
    for (const n of c.objects) { const o = byName.get(n) || byName.get(baseName(n)); if (o) objs.push(o); }
    const tops = objs.filter((o) => !objs.some((p) => p !== o && isAncestor(p, o)));   // keep only top-most nodes
    const ms = [];
    for (const t of tops) t.traverse((o) => { if (o.isMesh) ms.push(o); });
    compMeshes.set(c.id, { tops, meshes: ms });
    for (const m of ms) if (!nodeToComp.has(m)) nodeToComp.set(m, c.id);
  }
}
function linkComponentId(link) {
  const ids = new Set(S.bundle.components.map((c) => c.id));
  for (const cand of [link, link.replace(/_link$/, ''), link.replace(/_joint$/, '')]) if (ids.has(cand)) return cand;
  return null;
}
// Every joint becomes a pivot at its origin; the child link's meshes are re-parented under it, so a slider moves them.
// Joint origins are given in the asset frame (also for joints nested under a moving link), so a nested pivot is
// placed by converting that point into its parent pivot's frame.
function buildPivots(b) {
  const byChild = new Map(b.joints.map((j) => [j.child, j]));
  const made = new Set();
  const make = (j) => {
    if (made.has(j.name)) return pivots.get(j.name)?.pivot || assetRoot;   // (a cycle in the data falls back to the root)
    made.add(j.name);
    const parentJoint = byChild.get(j.parent);
    const parent = parentJoint && parentJoint !== j ? make(parentJoint) : assetRoot;
    const pv = new THREE.Object3D();
    pv.name = `pivot:${j.name}`;
    parent.updateMatrixWorld(true);
    pv.position.copy(parent.worldToLocal(assetRoot.localToWorld(new THREE.Vector3(...j.origin_m))));
    parent.add(pv);
    pv.updateMatrixWorld(true);
    const rest = pv.position.clone();
    const cid = linkComponentId(j.child);
    const cm = cid ? compMeshes.get(cid) : null;
    let tops = cm ? cm.tops : [];
    if (!tops.length) {                          // fall back to a node-name match on the link name
      const hits = [];
      model.traverse((o) => { const segs = o.name.split('__'); if (segs[1] === j.child || o.name === j.child) hits.push(o); });
      tops = hits.filter((o) => !hits.some((p) => p !== o && isAncestor(p, o)));
    }
    for (const t of tops) pv.attach(t);
    pivots.set(j.name, { pivot: pv, joint: j, rest, hasMesh: tops.length > 0, compId: cid });
    return pv;
  };
  for (const j of b.joints) make(j);
}
function setJoint(name, q) {
  const p = pivots.get(name);
  if (!p) return;
  S.jointValues[name] = q;
  const j = p.joint, axis = new THREE.Vector3(...j.axis).normalize();
  if (j.type === 'prismatic') p.pivot.position.copy(p.rest).addScaledVector(axis, q);
  else p.pivot.quaternion.setFromAxisAngle(axis, q);
  invalidate();
}
function compOrigin(c, byId, depth = 0) {         // component frame origins chain up the parent tree (rotations are zero in the files)
  const o = new THREE.Vector3(...(c.origin_m || [0, 0, 0]));
  const p = c.parent && byId.get(c.parent);
  if (p && depth < 8) o.add(compOrigin(p, byId, depth + 1));
  return o;
}
function hostForComponent(cid, byId) {           // nearest moving ancestor, so annotations ride along with doors and lids
  for (let id = cid, n = 0; id && n < 12; n++) {
    for (const [, p] of pivots) if (p.compId === id) return p.pivot;
    id = byId.get(id)?.parent;
  }
  return assetRoot;
}
function hostForLink(link) {
  if (!link) return assetRoot;
  for (const [, p] of pivots) if (p.joint.child === link) return p.pivot;
  return assetRoot;
}
function addOv(layer, obj, host = assetRoot) {
  obj.userData.layer = layer;
  overlay[layer].add(obj);
  if (host && host !== assetRoot) host.attach(obj);
  return obj;
}
const AXIS_NAMES = [[[1, 0, 0], '+X'], [[-1, 0, 0], '−X'], [[0, 1, 0], '+Y'], [[0, -1, 0], '−Y'], [[0, 0, 1], '+Z'], [[0, 0, -1], '−Z']];
function axisName(v) {
  if (!Array.isArray(v)) return '—';
  const u = new THREE.Vector3(...v);
  if (u.lengthSq() < 1e-9) return '—';
  u.normalize();
  for (const [a, n] of AXIS_NAMES) if (u.distanceTo(new THREE.Vector3(...a)) < 1e-3) return n;
  return vec(v, 2);
}
function sideName(axis) {                        // how a world axis relates to the instrument's front
  const f = frontVec(), r = rightVec(f), a = new THREE.Vector3(...axis);
  if (a.distanceTo(UP) < 1e-3) return 'up';
  for (const [v, n] of [[f, 'front'], [f.clone().negate(), 'back'], [r, 'right'], [r.clone().negate(), 'left']]) if (a.distanceTo(v) < 1e-3) return n;
  return '';
}

function buildOverlays(b, maxDim) {
  const byId = new Map(b.components.map((c) => [c.id, c]));
  const hyp = Math.hypot(...(b.size_m || [maxDim, maxDim, maxDim]));
  // collision proxies (primitives from the URDF / collision_proxies.json)
  const colMat = new THREE.MeshBasicMaterial({ color: COL.collision, transparent: true, opacity: 0.22, depthWrite: false });
  for (const c of b.components) {
    if (!c.collisions.length) continue;
    const base = compOrigin(c, byId), host = hostForComponent(c.id, byId);
    for (const k of c.collisions) {
      let geo;
      if (k.shape === 'box') geo = new THREE.BoxGeometry(...(k.size_m || [0.01, 0.01, 0.01]).map((v) => Math.max(v, 1e-4)));
      else if (k.shape === 'cylinder' || k.shape === 'capsule') { geo = new THREE.CylinderGeometry(k.radius_m, k.radius_m, Math.max(k.length_m || 0, 1e-4), 24); geo.rotateX(Math.PI / 2); }
      else if (k.shape === 'sphere') geo = new THREE.SphereGeometry(k.radius_m || 0.005, 16, 12);
      else geo = new THREE.BoxGeometry(...(k.size_m || [0.01, 0.01, 0.01]).map((v) => Math.max(v, 1e-4)));
      const m = tagged(new THREE.Mesh(geo, colMat), 'collision');
      m.position.copy(base).add(new THREE.Vector3(...(k.origin_m || [0, 0, 0])));
      m.rotation.copy(rpyEuler(k.rpy_rad));
      const e = mkEdges(geo, 'collision-edge');
      e.position.copy(m.position);
      e.rotation.copy(m.rotation);
      addOv('collision', m, host);
      addOv('collision', e, host);
    }
  }
  // joints: axis arrows for doors, rotors and other joints; outline boxes for buttons and knobs
  const axisLen = Math.max(Math.min(0.2, hyp * 0.15), Math.min(0.06, hyp * 0.3));
  for (const j of b.joints) {
    const kind = kindOf(j);
    const p = pivots.get(j.name);
    const host = p && p.pivot.parent !== assetRoot ? p.pivot.parent : assetRoot;
    const onclick = () => selectJoint(j.name);
    if (kind === 'button' || kind === 'knob') {
      if (!Array.isArray(j.child_bbox_min) || !Array.isArray(j.child_bbox_max)) continue;
      const box = addOv('buttons', mkBox(j.child_bbox_min, j.child_bbox_max, kind), host);
      if (kind === 'knob') addOv('buttons', mkArrow(j.origin_m, j.axis, Math.max(axisLen * 0.4, 0.02), 'knob', Math.max(axisLen * 0.1, 0.006)), host);
      const diag = new THREE.Vector3(...j.child_bbox_max).sub(new THREE.Vector3(...j.child_bbox_min)).length();
      addLabel({ object: box, text: j.name.replace(/^btn_|^knob_|^key_/, '').replace(/_(link|joint)$/, ''), cls: `k-${kind}`, layer: 'buttons', title: `${j.name}\n${KIND[kind]} · ${j.type} · ${rangeText(j)}`, onclick, sizeWorld: diag, minPx: 30, compId: p?.compId });
      continue;
    }
    addOv('joints', mkArrow(j.origin_m, j.axis, axisLen, kind), host);
    const s = tagged(new THREE.Mesh(new THREE.SphereGeometry(axisLen * 0.06, 12, 8), new THREE.MeshBasicMaterial({ color: COL[kind] })), kind);
    s.position.set(...j.origin_m);
    addOv('joints', s, host);
    const diag = Array.isArray(j.child_bbox_min) && Array.isArray(j.child_bbox_max) ? new THREE.Vector3(...j.child_bbox_max).sub(new THREE.Vector3(...j.child_bbox_min)).length() : 0;
    addLabel({ object: s, text: `${j.name} · ${rangeText(j)}`, cls: `k-${kind}`, layer: 'joints', title: `${j.name}: ${KIND[kind]}, ${j.type}, axis ${vec(j.axis, 3)}`, onclick, sizeWorld: diag || undefined, minPx: 22, compId: p?.compId });
  }
  // interface points ("slots"): where things are placed, inserted or connected
  for (const s of b.slots) {
    if (!Array.isArray(s.pos)) continue;
    const host = hostForLink(s.link);
    const r = Math.max(isNum(s.radius_m) ? s.radius_m : 0.02, 0.003);
    const n = new THREE.Vector3(...(s.insert_axis || [0, 0, -1]));
    if (n.lengthSq() < 1e-9) n.set(0, 0, -1);
    n.normalize();
    const disc = tagged(new THREE.Mesh(new THREE.CircleGeometry(r, 32), new THREE.MeshBasicMaterial({ color: COL.slot, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false })), 'slot');
    disc.position.set(...s.pos);
    disc.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), n);
    addOv('slots', disc, host);
    const ring = tagged(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(Array.from({ length: 48 }, (_, i) => new THREE.Vector3(Math.cos(i / 48 * Math.PI * 2) * r, Math.sin(i / 48 * Math.PI * 2) * r, 0))), lineMat('slot')), 'slot');
    ring.position.copy(disc.position);
    ring.quaternion.copy(disc.quaternion);
    addOv('slots', ring, host);
    let anchor = ring;
    if (r <= 0.05) {
      const sph = tagged(new THREE.Mesh(new THREE.SphereGeometry(r, 16, 10), new THREE.MeshBasicMaterial({ color: COL.slot, wireframe: true, transparent: true, opacity: 0.55 })), 'slot');
      sph.position.set(...s.pos);
      addOv('slots', sph, host);
      anchor = sph;
    }
    const alen = clamp(r * 1.6, Math.min(0.02, hyp * 0.05), 0.25);
    addOv('slots', mkArrow(s.pos, n.clone().negate().toArray(), alen, 'slot', alen * 0.3), host);
    addLabel({ object: anchor, text: `${s.name} · ${humanize(s.kind || 'slot').toLowerCase()}`, cls: 'k-slot', layer: 'slots',
      title: `${s.name}${s.part ? ` (${s.part})` : ''}\nFits: ${(s.fits || []).join(', ') || '—'}\nRadius ${num(r * 1000, 1)} mm · capture ${isNum(s.capture_m) ? `${num(s.capture_m * 1000, 1)} mm` : '—'}${s.access?.length ? `\nNeeds: ${s.access.join(', ')}` : ''}`,
      sizeWorld: r * 2, minPx: 16 });
  }
  // panel readouts (digital displays)
  for (const r of b.readouts) {
    if (!Array.isArray(r.center) || !Array.isArray(r.size_m)) continue;
    const [w, h] = r.size_m;
    const plane = tagged(new THREE.Mesh(new THREE.PlaneGeometry(Math.max(w, 1e-4), Math.max(h, 1e-4)), new THREE.MeshBasicMaterial({ color: COL.readout, transparent: true, opacity: 0.45, side: THREE.DoubleSide, depthTest: false })), 'readout');
    const normal = new THREE.Vector3(...(r.normal || [0, -1, 0])).normalize(), up = new THREE.Vector3(...(r.up || [0, 0, 1])).normalize();
    const right = new THREE.Vector3().crossVectors(up, normal).normalize();
    if (right.lengthSq() > 0.5) plane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, up, normal));
    plane.position.set(...r.center);
    plane.renderOrder = 5;
    const e = mkEdges(plane.geometry, 'readout-edge');
    e.position.copy(plane.position);
    e.quaternion.copy(plane.quaternion);
    const host = hostForLink(r.link);
    addOv('readouts', plane, host);
    addOv('readouts', e, host);
    addLabel({ object: plane, text: `${r.name} · ${r.digits ?? '?'} digits${r.unit ? ` · ${r.unit}` : ''}`, cls: 'k-readout', layer: 'readouts', title: `Readout ${r.name}\nSource: ${readoutSource(r.source)}`, sizeWorld: Math.max(w, h), minPx: 46 });
  }
  // placement / grasp frames (the per-joint frames are skipped: they duplicate the joint origins)
  const frameSize = clamp(maxDim * 0.05, 0.01, 0.06);
  for (const [name, pos] of Object.entries(b.frames || {})) {
    if (name.startsWith('joint_') || !Array.isArray(pos)) continue;
    const ax = new THREE.AxesHelper(frameSize);
    ax.position.set(...pos);
    addOv('frames', ax);
    addLabel({ object: ax, text: name, cls: 'k-frame', layer: 'frames' });
  }
  // layout: footprint, clearance (front/back/left/right relative to the declared front), robot standoff, work height
  if (b.layout) {
    const L = b.layout, bmin = b.bbox_min || [0, 0, 0], bmax = b.bbox_max || [0, 0, 0];
    const cx = (bmin[0] + bmax[0]) / 2, cy = (bmin[1] + bmax[1]) / 2;
    const [fw, fd] = Array.isArray(L.footprint_m) ? L.footprint_m : [bmax[0] - bmin[0], bmax[1] - bmin[1]];
    const lift = maxDim * 0.002;
    addOv('layout', mkRect(cx - fw / 2, cy - fd / 2, cx + fw / 2, cy + fd / 2, lift, 'frame'));
    const f = layoutFront(), rv = rightVec(f), cl = L.clearance_m || {};
    let x0 = cx - fw / 2, x1 = cx + fw / 2, y0 = cy - fd / 2, y1 = cy + fd / 2;
    const grow = (d, amt) => {
      if (!isNum(amt) || !amt) return;
      if (Math.abs(d.x) >= Math.abs(d.y)) { if (d.x > 0) x1 += amt; else x0 -= amt; } else if (d.y > 0) y1 += amt; else y0 -= amt;
    };
    grow(f, cl.front); grow(f.clone().negate(), cl.back); grow(rv, cl.right); grow(rv.clone().negate(), cl.left);
    addOv('layout', mkRect(x0, y0, x1, y1, lift * 1.5, 'layout', true));
    const half = Math.abs(f.x) >= Math.abs(f.y) ? fw / 2 : fd / 2;
    const alen = half + Math.max(maxDim * 0.25, 0.03);
    addOv('layout', mkArrow([cx, cy, lift * 2], f.toArray(), alen, 'layout', Math.min(alen * 0.15, 0.06)));
    const tip = new THREE.Vector3(cx, cy, 0).addScaledVector(f, alen);
    addLabel({ pos: tip, text: `Front ${axisName(L.front || [0, -1, 0])} · ${humanize(L.mount || 'unspecified mount').toLowerCase()} · ${humanize(L.weight_class || '').toLowerCase() || 'weight class n/a'}`, cls: 'k-frame', layer: 'layout' });
    if (isNum(L.robot_standoff_m) && L.robot_standoff_m > 0) {
      const disc = tagged(new THREE.Mesh(new THREE.CylinderGeometry(maxDim * 0.03, maxDim * 0.03, maxDim * 0.006, 24), new THREE.MeshBasicMaterial({ color: COL.layout })), 'layout');
      disc.rotation.x = Math.PI / 2;
      disc.position.copy(new THREE.Vector3(cx, cy, 0).addScaledVector(f, half + L.robot_standoff_m));
      addOv('layout', disc);
      addLabel({ object: disc, text: `Robot standoff ${num(L.robot_standoff_m, 3)} m`, cls: 'k-frame', layer: 'layout' });
    }
    if (isNum(L.work_height_m) && L.work_height_m > 0) {
      addOv('layout', mkRect(cx - fw / 2, cy - fd / 2, cx + fw / 2, cy + fd / 2, L.work_height_m, 'measured', true));
      addLabel({ pos: new THREE.Vector3(cx - fw / 2, cy - fd / 2, L.work_height_m), text: `Work height ${num(L.work_height_m, 3)} m`, cls: 'k-frame', layer: 'layout' });
    }
  }
  // declared bounding box (solid) and the box measured from the GLB (dashed)
  if (Array.isArray(b.bbox_min) && Array.isArray(b.bbox_max)) addOv('bbox', mkBox(b.bbox_min, b.bbox_max, 'frame'));
  if (S.bounds && !S.bounds.isEmpty()) addOv('bbox', mkBox(S.bounds.min.toArray(), S.bounds.max.toArray(), 'measured', true));
  // world axes at the asset origin, named relative to the instrument's front
  const axLen = clamp(maxDim * 0.3, 0.03, 0.5);
  addOv('axes', new THREE.AxesHelper(axLen));
  for (const [axis, nm] of [[[1, 0, 0], '+X'], [[0, 1, 0], '+Y'], [[0, 0, 1], '+Z']]) {
    const side = sideName(axis);
    addLabel({ pos: new THREE.Vector3(...axis).multiplyScalar(axLen * 1.05), text: side ? `${nm} ${side}` : nm, cls: 'k-frame', layer: 'axes' });
  }
}
function readoutSource(src) {
  if (!src || typeof src !== 'object') return '—';
  const parts = [];
  if (src.bridge) parts.push(`runtime ${[].concat(src.bridge).join(', ')}`);
  if (src.state) parts.push(`state ${src.state}`);
  if (isNum(src.scale) && src.scale !== 1) parts.push(`× ${num(src.scale, 4)}`);
  if (isNum(src.offset) && src.offset !== 0) parts.push(`${src.offset < 0 ? '−' : '+'} ${num(Math.abs(src.offset), 3)}`);
  return parts.length ? parts.join(' ') : JSON.stringify(src);
}

function applyLayers() {
  for (const [k, g] of Object.entries(overlay)) g.visible = !!S.layers[k];
  assetRoot.traverse((o) => { if (o.userData.layer) o.visible = !!S.layers[o.userData.layer]; });
  for (const m of meshes) m.visible = !!S.layers.mesh;
  applyMaterials();
}
// material state is derived from the original material + X-ray + wireframe + selection, so every toggle is reversible
function applyMaterials() {
  for (const m of meshes) {
    const sel = !!S.selected && nodeToComp.get(m) === S.selected;
    for (const mat of matsOf(m)) {
      const o = mat.userData.orig;
      if (!o) continue;
      const ghost = !!S.layers.ghost && !sel;
      const transparent = ghost ? true : o.transparent;
      if (mat.transparent !== transparent) { mat.transparent = transparent; mat.needsUpdate = true; }
      mat.opacity = ghost ? Math.min(o.opacity, 0.22) : o.opacity;
      mat.depthWrite = ghost ? false : o.depthWrite;
      mat.wireframe = !!S.layers.wire;
      if (mat.emissive && o.emissive) {
        if (sel) { mat.emissive.copy(COL.select); mat.emissiveIntensity = 0.55; } else { mat.emissive.copy(o.emissive); mat.emissiveIntensity = o.emissiveIntensity; }
      }
    }
  }
  invalidate();
}

/* ======================================================================================= selection */
function selectComponent(cid, { focus = false, from = 'view' } = {}) {
  const b = S.bundle;
  const c = b?.components.find((x) => x.id === cid);
  if (!c) return;
  S.selected = cid;
  applyMaterials();
  const pc = $('#pick-card');
  pc.innerHTML = `<div class="pc-top">${componentHead(c)}<button type="button" class="pc-close" data-act="deselect" aria-label="Clear selection">${ICON.close}</button></div>${componentBody(c, true)}${S.tab !== 'components' ? '<div class="pc-more"><button type="button" class="linkish" data-act="show-comp">Show in the Components tab</button></div>' : ''}`;
  pc.hidden = false;
  if (focus) {
    const cm = compMeshes.get(cid);
    if (cm?.meshes.length) { const bb = new THREE.Box3(); for (const m of cm.meshes) bb.expandByObject(m); focusOn(bb); }
  }
  if (S.tab === 'components') {
    if (from === 'table') markSelectedRows(); else { rerenderTab(); $(`#tab-body tr[data-comp="${CSS.escape(cid)}"]`)?.scrollIntoView({ block: 'nearest' }); }
  }
}
function clearSelection() {
  if (!S.selected && $('#pick-card').hidden) return;
  S.selected = null;
  applyMaterials();
  $('#pick-card').hidden = true;
  if (S.tab === 'components') rerenderTab();
}
function markSelectedRows() { for (const tr of $$('#tab-body tr[data-comp]')) tr.classList.toggle('sel', tr.dataset.comp === S.selected); }
function selectJoint(name) {
  const p = pivots.get(name);
  if (p?.compId) selectComponent(p.compId, { focus: true });
  openJoints(true);
  const row = $(`#jp-body [data-joint="${CSS.escape(name)}"]`);
  if (row) {
    row.scrollIntoView({ block: 'nearest' });
    row.classList.add('flash');
    setTimeout(() => row.classList.remove('flash'), 1400);
  }
}
const raycaster = new THREE.Raycaster();
let downAt = null;
canvas.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; $('#hint').classList.add('gone'); closeLayers(); });
canvas.addEventListener('pointerup', (e) => {
  if (!GL || !downAt || !model || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4) return;
  if (e.button !== 0 && e.pointerType === 'mouse') return;
  const r = canvas.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hits = raycaster.intersectObjects(meshes.filter((m) => m.visible), false);
  if (!hits.length) { clearSelection(); return; }
  const m = hits[0].object, cid = nodeToComp.get(m);
  if (cid) { selectComponent(cid); return; }
  S.selected = null;
  applyMaterials();
  const pc = $('#pick-card');
  pc.innerHTML = `<div class="pc-top"><div class="cc-head"><h4>${esc(m.name || 'Unnamed mesh')}</h4></div><button type="button" class="pc-close" data-act="deselect" aria-label="Close">${ICON.close}</button></div><p class="cc-p muted">This mesh is not listed in the instrument's component table.</p>`;
  pc.hidden = false;
});

/* ---------------------------------------------------------------------------- joint sliders */
const jointValueText = (j, q) => (j.type === 'prismatic' ? `${(q * 1000).toFixed(1)} mm` : deg(q));
function jointLimits(j) {
  if (j.type === 'continuous') return [-Math.PI, Math.PI];
  let lo = isNum(j.lower) ? j.lower : (j.type === 'prismatic' ? 0 : -Math.PI), hi = isNum(j.upper) ? j.upper : (j.type === 'prismatic' ? 0.05 : Math.PI);
  if (hi < lo) [lo, hi] = [hi, lo];
  if (hi === lo) hi = lo + (j.type === 'prismatic' ? 0.001 : 0.01);
  return [lo, hi];
}
function buildJointPanel(b) {
  const js = b.joints.filter((j) => j.type !== 'fixed');
  const panel = $('#joint-panel');
  if (!js.length) { panel.hidden = true; return; }
  const movable = js.filter((j) => pivots.get(j.name)?.hasMesh).length;
  $('#jp-count').textContent = movable === js.length ? `${js.length}` : `${movable} of ${js.length}`;
  $('#jp-count').title = movable === js.length ? `${js.length} joints` : `${movable} of ${js.length} joints can be moved in this viewer`;
  let html = `<div class="jp-actions"><button type="button" class="btn small" data-act="zero">Reset all</button>${js.some((j) => kindOf(j) === 'door' && pivots.get(j.name)?.hasMesh) ? '<button type="button" class="btn small" data-act="open-doors">Open doors &amp; lids</button>' : ''}</div>`;
  for (const k of KIND_ORDER) {
    const list = js.filter((j) => kindOf(j) === k);
    if (!list.length) continue;
    html += `<div class="jp-grp"><span class="dot" style="background:var(--ov-${k})"></span>${KIND_GROUP[k]} <span class="faint">${list.length}</span></div>`;
    for (const j of list) {
      const has = !!pivots.get(j.name)?.hasMesh, [lo, hi] = jointLimits(j), q = S.jointValues[j.name] || 0;
      html += `<div class="jrow" data-joint="${esc(j.name)}"><span class="jn${has ? '' : ' nomesh'}" title="${esc(j.name)} → ${esc(j.child)}${has ? '' : ' (no separate mesh in the GLB)'}">${esc(j.name)}</span><input type="range" min="${lo}" max="${hi}" step="${(hi - lo) / 200}" value="${clamp(q, lo, hi)}" aria-label="${esc(j.name)}" aria-valuetext="${esc(jointValueText(j, q))}"${has ? '' : ' disabled'}><span class="jv">${jointValueText(j, q)}</span></div>`;
    }
  }
  if (movable < js.length) html += '<p class="jp-note">Greyed-out joints have no separate mesh in the GLB, so they cannot be moved in this viewer.</p>';
  $('#jp-body').innerHTML = html;
  panel.hidden = false;
  openJoints(S.jointsOpen);
}
function openJoints(open) {
  S.jointsOpen = open;
  $('#jp-toggle').setAttribute('aria-expanded', String(open));
  $('#jp-body').hidden = !open;
}
$('#jp-toggle').addEventListener('click', () => openJoints(!S.jointsOpen));
$('#jp-body').addEventListener('input', (e) => {
  const inp = e.target.closest('input[type=range]');
  if (!inp) return;
  const row = inp.closest('.jrow'), j = pivots.get(row.dataset.joint)?.joint;
  if (!j) return;
  const q = parseFloat(inp.value);
  setJoint(j.name, q);
  const t = jointValueText(j, q);
  row.querySelector('.jv').textContent = t;
  inp.setAttribute('aria-valuetext', t);
});
$('#jp-body').addEventListener('click', (e) => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (!act || !S.bundle) return;
  for (const j of S.bundle.joints) {
    if (j.type === 'fixed' || !pivots.get(j.name)) continue;
    if (act === 'zero') setJoint(j.name, 0);
    else if (act === 'open-doors' && kindOf(j) === 'door') setJoint(j.name, jointLimits(j)[1]);
  }
  buildJointPanel(S.bundle);
});

/* ======================================================================================= header, list and filters */
function setupHeader() {
  const site = $('#link-website');
  if (WEBSITE_URL) { site.href = WEBSITE_URL; site.hidden = false; }
  $('#link-dataset').href = DATASET_URL;
  if (!ON_HUB) {
    const n = $('#base-note');
    n.textContent = `data: ${DATA_BASE.replace(/^https?:\/\//, '').replace(/\/$/, '')}`;
    n.title = `Models, previews and files are read from ${DATA_BASE} instead of the Hugging Face dataset.`;
    n.hidden = false;
  }
  updateThemeButton();
  $('#theme-btn').addEventListener('click', () => setThemeMode({ system: 'light', light: 'dark', dark: 'system' }[themeMode()]));
  darkMQ.addEventListener?.('change', () => { if (themeMode() === 'system') themeChanged(); });
  for (const b of $$('.seg [data-view]')) b.addEventListener('click', () => setView(b.dataset.view));
  let t = null;
  $('#search').addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { S.search = e.target.value; applyFilter(); }, 90); });
  $('#search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {                       // Enter opens the first match
      S.search = e.target.value;
      applyFilter();
      const first = filtered()[0];
      if (first) selectInstrument(first.tid, { user: true });
    }
  });
  $('#domain').addEventListener('change', (e) => { S.domain = e.target.value; applyFilter(); });
  $('#list-toggle').addEventListener('click', () => toggleList());
}
function toggleList(open = !$('#sidebar').classList.contains('open')) {
  $('#sidebar').classList.toggle('open', open);
  $('#list-toggle').setAttribute('aria-expanded', String(open));
  if (open) requestAnimationFrame(() => scrollActiveIntoView(true));
}
const byChem = (a, b) => (a.chem_id && b.chem_id ? a.chem_id.localeCompare(b.chem_id) : a.chem_id ? -1 : b.chem_id ? 1 : a.slug.localeCompare(b.slug));
function buildDomainSelect() {
  $('#domain').innerHTML = `<option value="">All domains (${S.index.length})</option>${S.domains.map((d) => `<option value="${esc(d.key)}">${esc(d.name)} (${d.count ?? S.index.filter((a) => a.domain === d.key).length})</option>`).join('')}`;
}
function itemHtml(a) {
  const mm = [a.maker, a.model].filter(Boolean).join(' · ');
  return `<li data-tid="${esc(a.tid)}"><a class="item" href="#${esc(a.tid)}" data-tid="${esc(a.tid)}" aria-current="false">
    <img src="${thumbUrl(a.slug)}" alt="" width="400" height="300" loading="lazy" decoding="async">
    <span class="it-text"><span class="it-name">${esc(a.name)}</span><span class="it-mm" title="${esc(mm)}">${esc(mm)}</span>
    <span class="it-meta">${a.chem_id ? `<span class="cid">${esc(a.chem_id)}</span>` : '<span class="cid" title="First-generation asset without a CHEM ID">1st gen</span>'}<span>${plural(a.n_joints ?? 0, 'joint')}</span><span title="${a.n_components ?? 0} components">${plural(a.n_components ?? 0, 'part')}</span></span></span></a></li>`;
}
function buildList() {
  const known = new Set(S.domains.map((d) => d.key));
  const groups = [...S.domains, ...[...new Set(S.index.map((a) => a.domain).filter((d) => !known.has(d)))].map((k) => ({ key: k, name: humanize(k || 'other') }))];
  $('#asset-list').innerHTML = groups.map((d) => {
    const items = S.index.filter((a) => a.domain === d.key).sort(byChem);
    if (!items.length) return '';
    return `<li class="dgroup" data-domain="${esc(d.key)}"><div class="grp"><span>${esc(d.name)}</span><span class="n">${items.length}</span></div><ul class="items">${items.map(itemHtml).join('')}</ul></li>`;
  }).join('') + '<li class="list-msg" id="list-empty" hidden>No instrument matches the search.</li>';
}
$('#asset-list').addEventListener('click', (e) => {
  const a = e.target.closest('a.item');
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button > 0) return;   // let modified clicks open a new tab
  e.preventDefault();
  selectInstrument(a.dataset.tid, { user: true });
});
function filtered() {
  const toks = S.search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return S.index.filter((a) => (!S.domain || a.domain === S.domain) && toks.every((t) => a._hay.includes(t)));
}
function applyFilter() {
  const rows = filtered(), vis = new Set(rows.map((a) => a.tid));
  for (const li of $$('#asset-list li[data-tid]')) li.hidden = !vis.has(li.dataset.tid);
  for (const g of $$('#asset-list .dgroup')) {
    const n = $$('li[data-tid]', g).filter((li) => !li.hidden).length;
    g.hidden = !n;
    $('.grp .n', g).textContent = n;
  }
  $('#list-empty').hidden = rows.length > 0;
  const txt = rows.length === S.index.length ? `${rows.length} instruments` : `${rows.length} of ${S.index.length}`;
  $('#asset-count').textContent = txt;
  $('#tv-count').textContent = txt;
  if (S.view === 'table') renderTable();
}
function markActive() {
  for (const a of $$('#asset-list a.item')) a.setAttribute('aria-current', String(a.dataset.tid === S.tid));
  for (const tr of $$('#tv-table tbody tr')) tr.setAttribute('aria-current', String(tr.dataset.tid === S.tid));
}
function scrollActiveIntoView(force = false) {
  const el = $(`#asset-list a.item[aria-current="true"]`);
  const wrap = $('#list-wrap');
  if (!el || !wrap || (narrowMQ.matches && !force)) return;
  const r = el.getBoundingClientRect(), w = wrap.getBoundingClientRect();
  if (r.top < w.top + 30 || r.bottom > w.bottom) wrap.scrollTop += r.top - w.top - w.height / 3;
}
function afterUserSelect() {
  if (narrowMQ.matches) {
    toggleList(false);
    $('#viewer').scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
  }
}
function renderTitle(a) {
  $('#inst-id').textContent = [a.chem_id, a.tid].filter(Boolean).join(' · ');
  $('#inst-name').textContent = a.name || a.slug;
  $('#inst-mm').textContent = [a.maker, a.model].filter(Boolean).join(' · ');
  const z = $('#inst-zh');
  z.textContent = a.name_zh || '';
  z.title = a.name_zh || '';
  $('#inst-chips').innerHTML = `<span class="chip plain">${esc(domainName(a.domain))}</span>${isaacPill(a.isaac_lab, true)}${a.chem_id ? '' : '<span class="chip plain" title="First-generation asset (September 2026), described by physics_annotations.json and its own runtime">First generation</span>'}`;
  document.title = `${a.name}${a.model ? ` · ${a.model}` : ''} | ChemLab-Assets Explorer`;
}

/* ---------------------------------------------------------------------------- deep links */
function resolveTid(s) {
  if (!s) return null;
  const t = s.trim();
  if (S.byTid.has(t)) return t;
  if (S.byTid.has(`instrument.${t}`)) return `instrument.${t}`;
  return null;
}
function tidFromLocation() {
  let h = location.hash.replace(/^#/, '');
  try { h = decodeURIComponent(h); } catch { /* keep raw */ }
  const asked = [h, QUERY.get('id')].filter(Boolean);
  for (const c of asked) { const t = resolveTid(c); if (t) return { tid: t }; }
  return { tid: null, asked: asked[0] };
}
function writeLocation(tid, notifyParent) {
  const u = new URL(location.href);
  if (u.searchParams.has('id')) u.searchParams.delete('id');
  u.hash = tid;
  try { history.replaceState(history.state, '', `${u.pathname}${u.search}${u.hash}`); } catch { /* sandboxed */ }
  // On huggingface.co the app runs in an iframe; this message lets the Space page update its own URL (Hub docs:
  // "How to handle URL parameters in Spaces"). Elsewhere the target origin does not match and nothing happens.
  if (notifyParent && window.parent !== window) {
    const qs = new URLSearchParams(u.search);
    for (const k of [...qs.keys()]) if (k.startsWith('__')) qs.delete(k);
    const q = qs.toString();
    try { window.parent.postMessage({ queryString: q ? `?${q}` : '', hash: `#${tid}` }, HUB_ORIGIN); } catch { /* ignore */ }
  }
}
window.addEventListener('hashchange', () => {
  const { tid, asked } = tidFromLocation();
  if (tid && tid !== S.tid) selectInstrument(tid);
  else if (!tid && asked) toast(`“${asked}” is not an instrument in this release.`);
});

/* ======================================================================================= views: 3D viewer / table */
function setView(v) {
  S.view = v;
  document.body.classList.toggle('view-table', v === 'table');
  $('#table-view').hidden = v !== 'table';
  for (const b of $$('.seg [data-view]')) b.setAttribute('aria-pressed', String(b.dataset.view === v));
  if (v === 'table') renderTable();
  invalidate();
}
const volume = (s) => (Array.isArray(s) && s.length === 3 ? s[0] * s[1] * s[2] : -1);
const COLS = [
  { key: 'chem_id', label: 'CHEM ID', sort: (a) => (a.chem_id ? parseInt(a.chem_id.replace(/\D+/g, ''), 10) || 0 : 1e6), cell: (a) => `<td class="cid">${a.chem_id ? esc(a.chem_id) : '<span class="faint" title="First-generation asset">—</span>'}</td>` },
  { key: 'name', label: 'Instrument', sort: (a) => (a.name || '').toLowerCase(), cell: (a) => `<td><div class="ti"><img src="${thumbUrl(a.slug)}" alt="" width="48" height="36" loading="lazy" decoding="async"><div><b>${esc(a.name)}</b><span>${esc([a.maker, a.model].filter(Boolean).join(' · '))}</span></div></div></td>` },
  { key: 'domain', label: 'Domain', sort: (a) => domainName(a.domain), cell: (a) => `<td class="dom">${esc(domainName(a.domain))}</td>` },
  { key: 'size', label: 'Size (mm)', r: true, title: 'Width × depth × height; sorted by volume', sort: (a) => volume(a.size_m), cell: (a) => `<td class="r">${esc(sizeMM(a.size_m).replace(' mm', ''))}</td>` },
  { key: 'n_components', label: 'Components', r: true },
  { key: 'n_joints', label: 'Joints', r: true },
  { key: 'prismatic', label: 'Prismatic', r: true, sort: (a) => a.joint_types?.prismatic || 0 },
  { key: 'revolute', label: 'Revolute', r: true, title: 'Revolute and continuous joints', sort: (a) => (a.joint_types?.revolute || 0) + (a.joint_types?.continuous || 0) },
  { key: 'n_state_slots', label: 'State slots', r: true },
  { key: 'n_capabilities', label: 'Capabilities', r: true },
  { key: 'n_readouts', label: 'Readouts', r: true },
  { key: 'n_interface_points', label: 'Interface points', r: true },
  { key: 'isaac_lab', label: 'Isaac Lab', sort: (a) => ({ full: 0, partial: 1, bridged: 2 }[a.isaac_lab] ?? 3), cell: (a) => `<td>${a.isaac_lab ? pill(a.isaac_lab) : NA}</td>` },
];
for (const c of COLS) {
  if (!c.sort) c.sort = (a) => (isNum(a[c.key]) ? a[c.key] : -1);
  if (!c.cell) c.cell = (a) => { const v = c.sort(a); return `<td class="r">${v < 0 ? NA : esc(num(v))}</td>`; };
}
function renderTable() {
  const col = COLS.find((c) => c.key === S.sort.key) || COLS[0], dir = S.sort.dir;
  const rows = filtered().slice().sort((x, y) => {
    const a = col.sort(x), b = col.sort(y);
    const d = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
    return d * dir || byChem(x, y);
  });
  $('#tv-table thead').innerHTML = `<tr>${COLS.map((c) => {
    const on = c.key === S.sort.key;
    return `<th scope="col" class="${c.r ? 'r' : ''}"${on ? ` aria-sort="${dir > 0 ? 'ascending' : 'descending'}"` : ''}><button type="button" data-sort="${c.key}"${c.title ? ` title="${esc(c.title)}"` : ''}>${esc(c.label)}<span class="arr" aria-hidden="true">${on ? (dir > 0 ? '▲' : '▼') : ''}</span></button></th>`;
  }).join('')}</tr>`;
  $('#tv-table tbody').innerHTML = rows.length
    ? rows.map((a) => `<tr data-tid="${esc(a.tid)}" tabindex="0" aria-current="${a.tid === S.tid}">${COLS.map((c) => c.cell(a)).join('')}</tr>`).join('')
    : `<tr><td class="tv-empty" colspan="${COLS.length}">No instrument matches the search or domain filter.</td></tr>`;
}
$('#tv-table').addEventListener('click', (e) => {
  const th = e.target.closest('button[data-sort]');
  if (th) {
    const k = th.dataset.sort;
    if (S.sort.key === k) S.sort.dir = -S.sort.dir;
    else S.sort = { key: k, dir: COLS.find((c) => c.key === k)?.r ? -1 : 1 };
    renderTable();
    $(`#tv-table button[data-sort="${k}"]`)?.focus();
    return;
  }
  const tr = e.target.closest('tbody tr[data-tid]');
  if (tr) openFromTable(tr.dataset.tid);
});
$('#tv-table').addEventListener('keydown', (e) => {
  const tr = e.target.closest('tbody tr[data-tid]');
  if (tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openFromTable(tr.dataset.tid); }
});
function openFromTable(tid) {
  setView('viewer');
  selectInstrument(tid, { user: true });
  requestAnimationFrame(() => scrollActiveIntoView());
}

/* ======================================================================================= right panel */
const TAB_TITLES = { overview: 'Overview', components: 'Components', joints: 'Joints & controls', interfaces: 'Interfaces & readouts', capabilities: 'Capabilities & mechanisms', files: 'Files' };
function setTab(t, focus = false) {
  if (!TAB_TITLES[t]) return;
  S.tab = t;
  for (const btn of $$('#tabs [role=tab]')) {
    const on = btn.dataset.tab === t;
    btn.setAttribute('aria-selected', String(on));
    btn.tabIndex = on ? 0 : -1;
    if (on && focus) btn.focus();
  }
  $('#tab-body').setAttribute('aria-labelledby', `tab-${t}`);
  renderTab();
  $('#tab-body').scrollTop = 0;
  if (S.selected && !$('#pick-card').hidden) { const c = S.bundle?.components.find((x) => x.id === S.selected); if (c) selectComponent(c.id, { from: 'table' }); }
}
$('#tabs').addEventListener('click', (e) => { const b = e.target.closest('[role=tab]'); if (b) setTab(b.dataset.tab); });
$('#tabs').addEventListener('keydown', (e) => {
  const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
  if (!(e.key in keys) && e.key !== 'Home' && e.key !== 'End') return;
  e.preventDefault();
  const ids = Object.keys(TAB_TITLES), i = ids.indexOf(S.tab);
  const n = e.key === 'Home' ? 0 : e.key === 'End' ? ids.length - 1 : (i + keys[e.key] + ids.length) % ids.length;
  setTab(ids[n], true);
});
function renderTab() {
  const body = $('#tab-body'), b = S.bundle;
  if (!b) {
    body.innerHTML = S.bundleError ? `<p class="empty">The data for this instrument could not be loaded. ${esc(describeError(S.bundleError))}</p>` : '<p class="empty">Loading…</p>';
    return;
  }
  const fn = { overview: tabOverview, components: tabComponents, joints: tabJoints, interfaces: tabInterfaces, capabilities: tabCapabilities, files: tabFiles }[S.tab];
  body.innerHTML = fn(b);
  if (S.tab === 'components') applyCompFilter();
  if (S.tab === 'capabilities') applyCapFilter();
}
function rerenderTab() { const body = $('#tab-body'), y = body.scrollTop; renderTab(); body.scrollTop = y; }
const secH = (title, aside = '') => `<div class="sec-h"><h3>${esc(title)}</h3>${aside ? `<span class="aside">${aside}</span>` : ''}</div>`;
const fact = (k, v, small = '', wide = false) => `<div${wide ? ' class="wide"' : ''}><dt>${esc(k)}</dt><dd>${v}${small ? `<small>${small}</small>` : ''}</dd></div>`;

function tabOverview(b) {
  const a = S.byTid.get(b.tid) || {};
  const jt = countBy(b.joints, 'type'), jk = countBy(b.joints.map((j) => ({ k: kindOf(j) })), 'k');
  const caps = Object.values(b.capabilities), av = countBy(caps, 'availability');
  const cov = b.checks.coverage || {}, st = cov.status || a.isaac_lab;
  const legacy = !b.has_contract;
  const nState = Object.keys(b.state_slots).length;
  const front = b.layout?.front;
  const jointSmall = [['prismatic', 'revolute', 'continuous'].filter((k) => jt[k]).map((k) => `${jt[k]} ${k}`).join(' · '), KIND_ORDER.filter((k) => jk[k]).map((k) => kindText(jk[k], k)).join(' · ')].filter(Boolean).join('<br>');
  const capSmall = ['implemented', 'unsupported', 'declared'].filter((k) => av[k]).map((k) => `${av[k]} ${k}`).join(' · ');
  const facts = [
    fact('Size W × D × H', esc(sizeMM(b.size_m))),
    fact('Mass', isNum(b.mass_kg) ? `${num(b.mass_kg, 2)} kg` : '—'),
    fact('Components', String(b.components.length)),
    fact('Joints', String(b.joints.length), jointSmall),
    fact('State slots', legacy && !nState ? '—' : String(nState), legacy && !nState ? 'kept in physics_annotations.json' : ''),
    fact('Capabilities', String(caps.length), capSmall),
    fact('Readouts', String(b.readouts.length), b.readouts.length ? esc(b.readouts.map((r) => r.name).join(', ')) : ''),
    fact('Interface points', String(b.slots.length), b.layout?.primary_slot ? `primary: ${esc(b.layout.primary_slot)}` : ''),
    fact('Frame', 'Z up · front −Y', `metres, kilograms, radians${front && axisName(front) !== '−Y' ? ` · layout lists front ${esc(axisName(front))}` : ''}`),
    fact('Isaac Lab', st ? pill(st) : '—', st ? esc(ISAAC[st] || '') : ''),
  ].join('');
  const gallery = b.previews.length ? `${secH('Preview renders', `${b.previews.length} images`)}<div class="gallery">${b.previews.map((p, i) => `<button type="button" class="shot" data-shot="${i}" aria-label="Open preview: ${esc(previewCaption(p))}"><img src="${esc(dataUrl(p))}" alt="" width="1600" height="1200" loading="lazy" decoding="async"><span>${esc(previewCaption(p))}</span></button>`).join('')}</div>` : '';
  const c = b.completeness || {};
  const bar = (label, n, d) => (isNum(n) && d ? `<div class="bar-row"><span>${esc(label)}</span><span class="track" role="img" aria-label="${n} of ${d}"><i style="width:${Math.round(100 * n / d)}%"></i></span><span class="num">${n} / ${d}</span></div>` : '');
  const coverage = c.components ? `${secH('Annotation coverage', 'per component')}<div class="bars">${bar('Collision proxies', c.with_collisions, c.components)}${bar('Mapped to GLB meshes', c.with_objects, c.components)}${bar('Function described', c.with_function, c.components)}${bar('Mass given', c.with_mass, c.components)}</div>` : '';
  const sources = b.sources.length ? `${secH('Sources', String(b.sources.length))}<ul class="zh-list">${b.sources.map((s) => { const t = s.title || s.id || s.url || 'source'; return `<li>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(t)}</a>` : esc(t)}</li>`; }).join('')}</ul>` : '';
  return `${secH('Key facts', esc(domainName(b.domain)))}<dl class="facts">${facts}</dl>
    ${legacy ? '<p class="note-box" style="margin-top:12px">First-generation asset (September 2026). It is described by <code>physics_annotations.json</code> and its own runtime instead of an asset contract.</p>' : ''}
    ${b.description_zh ? `${secH('Description', '<span class="tag-zh">in Chinese</span>')}${zh(b.description_zh, 'p', 'prose')}` : ''}
    ${secH('Download')}<div class="dl">${downloadLinks(b)}</div>
    <div class="more-links"><a href="${esc(hubTree(b.path || b.asset_dir || ''))}" target="_blank" rel="noopener">Browse the folder on Hugging Face ↗</a><button type="button" class="linkish" data-act="tab-files">All ${b.files.length} files</button></div>
    ${gallery}${coverage}
    ${cov.note_zh ? `${secH('Isaac Lab integration', '<span class="tag-zh">notes in Chinese</span>')}${zh(cov.note_zh, 'p', 'prose')}` : ''}
    ${sources}`;
}
function previewCaption(p) {
  const n = p.split('/').pop().replace(/\.[a-z0-9]+$/i, '').replace(/^preview_/, '');
  return humanize(n.replace(/^isaac_rtx_/, 'isaac rtx ').replace(/env(\d)/g, 'env $1'));
}
function downloadLinks(b) {
  const size = (p) => b.files.find((f) => f.path === p)?.bytes;
  const items = [];
  if (b.glb) items.push([b.glb, 'Visual model (glTF binary)']);
  if (b.urdf) items.push([b.urdf, 'Articulation with collision proxies (URDF)']);
  if (b.contract) items.push([b.contract, 'Asset contract: parts, state slots, capabilities']);
  else { const pa = b.files.find((f) => /physics_annotations\.json$/.test(f.path)); if (pa) items.push([pa.path, 'Physics annotations (first-generation format)']); }
  if (b.runtime) items.push([b.runtime, 'Python behaviour runtime']);
  if (!items.length) return '<p class="muted">No downloadable files are listed for this instrument.</p>';
  const tgt = ON_HUB ? '' : ' target="_blank" rel="noopener"';
  return items.map(([p, what]) => `<a href="${esc(downloadUrl(p))}" download${tgt}>${ICON.download}<span><span class="fn">${esc(p.split('/').pop())}</span><span class="what">${esc(what)}</span></span><span class="sz">${esc(bytes(size(p)))}</span></a>`).join('');
}

function componentHead(c) { return `<div class="cc-head"><h4>${esc(c.id)}</h4>${zh(c.name_zh, 'div', 'cc-zh')}</div>`; }
function collisionText(k) {
  const at = Array.isArray(k.origin_m) ? ` at ${vec(k.origin_m)} m` : '';
  if (k.shape === 'box' && Array.isArray(k.size_m)) return `Box ${k.size_m.map(mmv).join(' × ')} mm${at}`;
  if ((k.shape === 'cylinder' || k.shape === 'capsule') && isNum(k.radius_m)) return `${humanize(k.shape)} r ${mmv(k.radius_m)} mm × ${mmv(k.length_m)} mm${at}`;
  if (k.shape === 'sphere' && isNum(k.radius_m)) return `Sphere r ${mmv(k.radius_m)} mm${at}`;
  return `${humanize(k.shape || 'shape')}${at}`;
}
function materialText(m) {
  if (!m) return '';
  if (typeof m === 'string') return esc(m);
  if (typeof m !== 'object') return esc(m);
  const c = Array.isArray(m.base_color) ? m.base_color : null;
  const sw = c ? `<span class="swatch" style="background:rgb(${c.slice(0, 3).map((v) => Math.round(clamp(v, 0, 1) * 255)).join(' ')})"></span>` : '';
  const bits = [isNum(m.metallic) ? `metallic ${num(m.metallic, 2)}` : '', isNum(m.roughness) ? `roughness ${num(m.roughness, 2)}` : '', isNum(m.alpha) && m.alpha < 1 ? `alpha ${num(m.alpha, 2)}` : '', m.name ? esc(m.name) : ''].filter(Boolean);
  return `${sw}${bits.join(' · ') || '—'}`;
}
function componentBody(c, compact = false) {
  const rows = [];
  const add = (k, v) => { if (v !== '' && v != null) rows.push(`<dt>${esc(k)}</dt><dd>${v}</dd>`); };
  if (c.subsystem) add('Subsystem', esc(humanize(c.subsystem)));
  add('Parent', `<span class="mono">${esc(c.parent || '—')}</span>${Array.isArray(c.origin_m) ? ` · origin ${esc(vec(c.origin_m))} m` : ''}`);
  if (c.joint) {
    const L = Array.isArray(c.joint.limits) && c.joint.limits.length === 2 && c.joint.limits.every(isNum) ? c.joint.limits : null;
    const unit = c.joint.limit_unit || (c.joint.type === 'prismatic' ? 'm' : 'rad');
    const lim = !L ? '' : unit === 'm' ? `${num(L[0] * 1000, 1)} – ${num(L[1] * 1000, 1)} mm` : unit === 'rad' ? `${deg(L[0])} – ${deg(L[1])}` : `${num(L[0], 4)} – ${num(L[1], 4)} ${unit}`;
    add('Joint', `${esc(c.joint.type || '')}${Array.isArray(c.joint.axis) ? `, axis ${esc(vec(c.joint.axis, 2))}` : ''}${lim ? `, ${esc(lim)}` : ''} ${pill(c.joint.evidence)}`);
  }
  add('Mass', `${isNum(c.mass_kg) ? `${num(c.mass_kg, 3)} kg` : '—'} ${pill(c.mass_evidence)}`);
  if (c.material) add('Material', `${materialText(c.material)}${isNum(c.friction) ? ` · friction ${num(c.friction, 2)}` : ''}${isNum(c.restitution) ? ` · restitution ${num(c.restitution, 2)}` : ''}`);
  if (c.geometry_evidence || c.geometry_note_zh) add('Geometry', `${pill(c.geometry_evidence)} ${compact ? '' : zh(c.geometry_note_zh)}`);
  if (c.interaction) add('Interaction', `${esc(humanize(c.interaction.category || ''))} ${c.interaction.graspable ? '<span class="pill t-good">graspable</span>' : ''}${c.interaction.removable ? '<span class="pill">removable</span>' : ''}`);
  if (c.actions?.length) add('Actions', c.actions.map((x) => `<span class="pill">${esc(x)}</span>`).join(''));
  if (c.capability_ids?.length) {
    const ids = compact ? c.capability_ids.slice(0, 5) : c.capability_ids;
    add('Capabilities', `<span class="mono" style="font-size:.74rem">${esc(ids.join(', '))}</span>${ids.length < c.capability_ids.length ? ` <span class="faint">+ ${c.capability_ids.length - ids.length} more</span>` : ''}`);
  }
  if (!compact && c.preconditions?.length) add('Preconditions', zh([].concat(...c.preconditions.map((p) => (Array.isArray(p) ? p : [typeof p === 'object' ? (p.description_zh || JSON.stringify(p)) : p]))).join('；')));
  if (c.anchor_m) add('Anchor', `${esc(vec(c.anchor_m))} m (component frame)`);
  if (c.physical_quantities?.length) add('Quantities', c.physical_quantities.map((q) => `<span class="mono">${esc(q.name)}</span> [${esc(q.unit || '')}] = ${fmt(q.value)} ${pill(q.evidence)}`).join('<br>'));
  add(`Collision (${c.collisions.length})`, c.collisions.length ? c.collisions.slice(0, compact ? 3 : 50).map((k) => esc(collisionText(k))).join('<br>') + (compact && c.collisions.length > 3 ? `<br><span class="faint">+ ${c.collisions.length - 3} more</span>` : '') : '<span class="faint">none</span>');
  const lim = compact ? 4 : 40;
  add(`GLB meshes (${c.objects.length})`, c.objects.length ? `<span class="mono" style="font-size:.72rem">${esc(c.objects.slice(0, lim).join(', '))}${c.objects.length > lim ? ' …' : ''}</span>` : '<span class="faint">none found</span>');
  return `${c.function_zh ? `<p class="cc-p"><b>Function</b>${zh(c.function_zh)}</p>` : ''}${!compact && c.physical_meaning_zh && c.physical_meaning_zh !== c.function_zh ? `<p class="cc-p"><b>Physical meaning</b>${zh(c.physical_meaning_zh)}</p>` : ''}<dl class="kv">${rows.join('')}</dl>`;
}
function tabComponents(b) {
  const sel = S.selected && b.components.find((c) => c.id === S.selected);
  const rows = b.components.map((c) => `<tr class="row${c.id === S.selected ? ' sel' : ''}" data-comp="${esc(c.id)}" tabindex="0"><td><span class="idc">${esc(c.id)}</span>${zh(c.name_zh, 'span', 'sub')}</td><td class="num">${c.collisions.length || '<span class="faint">0</span>'}</td><td class="num">${c.objects.length || '<span class="faint">0</span>'}</td><td>${c.joint ? esc(c.joint.type || '') : ''}</td><td class="num">${isNum(c.mass_kg) ? num(c.mass_kg, 3) : ''}</td></tr>`).join('');
  return `<div class="panel-title">Components</div><p class="lede">${b.components.length} parts. Select a row to highlight the part in 3D; click a part in the viewer to find it here.</p>
    <input class="filter" id="comp-filter" type="search" placeholder="Filter components" aria-label="Filter components" value="${esc(S.compFilter)}">
    ${sel ? `<div class="card" id="comp-card">${componentHead(sel)}${componentBody(sel)}</div>` : ''}
    <div class="tw"><table class="t" id="comp-table"><thead><tr><th scope="col">Component</th><th scope="col" class="num" title="Collision proxies">Coll.</th><th scope="col" class="num" title="Meshes in the GLB">Meshes</th><th scope="col">Joint</th><th scope="col" class="num">kg</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="lede" style="margin-top:10px">Assembled from ${esc(b.component_source || 'the asset files')}.</p>`;
}
function applyCompFilter() {
  const q = S.compFilter.trim().toLowerCase();
  for (const tr of $$('#comp-table tbody tr')) tr.hidden = !!q && !tr.textContent.toLowerCase().includes(q);
}
function tabJoints(b) {
  const jt = countBy(b.joints, 'type'), jk = countBy(b.joints.map((j) => ({ k: kindOf(j) })), 'k');
  const items = b.joints.map((j) => {
    const p = pivots.get(j.name), k = kindOf(j);
    const here = !S.modelReady ? '' : p?.hasMesh ? '<span class="pill t-good" title="A slider under the viewer moves this joint">movable in 3D</span>' : '<span class="pill t-neutral" title="No separate mesh in the GLB">no mesh</span>';
    return `<li class="cap row" data-joint="${esc(j.name)}" tabindex="0"><div class="cap-top"><span class="cap-id"><span class="dot" style="background:var(--ov-${k})"></span> ${esc(j.name)}</span><span class="pill">${esc(KIND[k])}</span><span class="pill">${esc(j.type)}</span>${here}</div>
      <div class="cap-meta"><span class="mono">${esc(j.parent)} → ${esc(j.child)}</span></div>
      <div class="cap-meta"><span>${esc(rangeText(j))}</span><span>axis <span class="mono">${esc(vec(j.axis, 2))}</span></span><span>at <span class="mono">${esc(vec(j.origin_m))}</span> m</span></div></li>`;
  }).join('');
  const ctl = b.controls || {}, nb = (ctl.buttons || []).length, nk = (ctl.knobs || []).length;
  const typeLine = ['prismatic', 'revolute', 'continuous', 'fixed'].filter((t) => jt[t]).map((t) => `${jt[t]} ${t}`).join(' · ') || 'none';
  const kindLine = KIND_ORDER.filter((k) => jk[k]).map((k) => kindText(jk[k], k)).join(' · ') || 'none';
  return `<div class="panel-title">Joints &amp; controls</div><p class="lede">Prismatic ranges in millimetres, revolute ranges in degrees. Select a joint to find it in 3D; move it with the sliders under the viewer.</p>
    <dl class="kv"><dt>Joint types</dt><dd>${esc(typeLine)}</dd><dt>Kinds</dt><dd>${esc(kindLine)}</dd>
    ${nb || nk ? `<dt>Panel controls</dt><dd>${nb ? `${plural(nb, 'button')} cut from the panel as spring-loaded joints (1.5 mm travel)` : ''}${nb && nk ? '; ' : ''}${nk ? `${plural(nk, 'knob')} (±150°)` : ''} in the simulator articulation</dd>` : ''}
    ${b.urdf ? `<dt>URDF</dt><dd><a href="${esc(downloadUrl(b.urdf))}"${ON_HUB ? '' : ' target="_blank" rel="noopener"'}>${esc(b.urdf.split('/').pop())}</a></dd>` : ''}</dl>
    ${secH(`Joints (${b.joints.length})`, S.modelReady ? `${[...pivots.values()].filter((p) => p.hasMesh).length} movable in 3D` : '')}
    ${b.joints.length ? `<ul class="caps">${items}</ul>` : '<p class="muted">This instrument has no joints.</p>'}
    ${nb || nk ? `<details class="more"><summary>Panel control names (${nb + nk})</summary><p class="mono" style="font-size:.72rem;line-height:1.6;overflow-wrap:anywhere">${esc([...(ctl.buttons || []), ...(ctl.knobs || [])].join(', '))}</p></details>` : ''}`;
}
function tabInterfaces(b) {
  const L = b.layout;
  const slotItems = b.slots.map((s) => `<li class="cap"><div class="cap-top"><span class="cap-id">${esc(s.name)}</span><span class="pill">${esc(humanize(s.kind || 'point'))}</span>${isNum(s.radius_m) ? `<span class="pill" title="Radius">r ${esc(num(s.radius_m * 1000, 1))} mm</span>` : ''}</div>
    <div class="cap-meta"><span>at <span class="mono">${esc(vec(s.pos))}</span> m</span>${s.part ? `<span>part <span class="mono">${esc(s.part)}</span></span>` : ''}</div>
    <div class="cap-meta"><span>fits ${esc((s.fits || []).join(', ') || '—')}</span>${s.access?.length ? `<span>needs <span class="mono">${esc(s.access.join(', '))}</span></span>` : ''}${isNum(s.capture_m) ? `<span>capture ${esc(num(s.capture_m * 1000, 1))} mm</span>` : ''}</div></li>`).join('');
  const roItems = b.readouts.map((r) => `<li class="cap"><div class="cap-top"><span class="cap-id">${esc(r.name)}</span>${isNum(r.digits) ? `<span class="pill">${plural(r.digits, 'digit')}${r.decimals ? ` · ${plural(r.decimals, 'decimal')}` : ''}</span>` : ''}${r.unit ? `<span class="pill">${esc(r.unit)}</span>` : ''}</div>
    <div class="cap-meta"><span>source <span class="mono">${esc(readoutSource(r.source))}</span></span>${Array.isArray(r.size_m) ? `<span>${esc(r.size_m.map(mmv).join(' × '))} mm</span>` : ''}${r.blank_channel ? `<span>blanked by <span class="mono">${esc(r.blank_channel)}</span></span>` : ''}</div>
    ${r.part ? `<div class="cap-meta"><span>part <span class="mono">${esc(r.part)}</span></span></div>` : ''}</li>`).join('');
  const frames = Object.entries(b.frames).filter(([k, v]) => !k.startsWith('joint_') && Array.isArray(v));
  const cl = L?.clearance_m || {};
  return `<div class="panel-title">Interfaces &amp; readouts</div><p class="lede">Interface points are where a robot places, inserts or connects things. Readouts are the panel displays the runtime drives.</p>
    ${secH(`Interface points (${b.slots.length})`)}
    ${b.slots.length ? `<ul class="caps">${slotItems}</ul>` : '<p class="muted">No interface points: nothing can be placed in or attached to this instrument.</p>'}
    ${b.hidden_parts.length ? `<p class="lede" style="margin-top:8px">Modelled placeholders hidden in simulation: <span class="mono">${esc(b.hidden_parts.join(', '))}</span></p>` : ''}
    ${secH(`Panel readouts (${b.readouts.length})`)}
    ${b.readouts.length ? `<ul class="caps">${roItems}</ul>` : '<p class="muted">No digital readouts (the instrument has no numeric display, or uses a menu or touch screen).</p>'}
    ${secH(`Placement frames (${frames.length})`)}
    ${frames.length ? `<div class="tw"><table class="t"><tbody>${frames.map(([k, v]) => `<tr><td class="idc">${esc(k)}</td><td class="mono nw" style="font-size:.74rem">${esc(vec(v))} m</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">None.</p>'}
    ${secH('Layout')}
    ${L ? `<dl class="kv"><dt>Front</dt><dd>${esc(axisName(L.front))}${L.front_source ? ` <span class="faint">(${esc(humanize(L.front_source))})</span>` : ''}</dd>
      <dt>Mount</dt><dd>${esc(humanize(L.mount || '—'))} · ${esc(humanize(L.weight_class || '—'))}${isNum(L.mass_kg) ? ` · ${num(L.mass_kg, 2)} kg` : ''}</dd>
      <dt>Footprint</dt><dd>${Array.isArray(L.footprint_m) ? esc(L.footprint_m.map(mmv).join(' × ')) : '—'} mm, height ${esc(mmv(L.height_m))} mm</dd>
      <dt>Clearance</dt><dd>${['front', 'back', 'left', 'right', 'top'].filter((k) => isNum(cl[k])).map((k) => `${k} ${mmv(cl[k])}`).join(' · ') || '—'} mm</dd>
      <dt>Robot</dt><dd>standoff ${isNum(L.robot_standoff_m) ? `${mmv(L.robot_standoff_m)} mm` : '—'} · work height ${isNum(L.work_height_m) ? `${mmv(L.work_height_m)} mm` : '—'}</dd>
      <dt>Primary point</dt><dd class="mono">${esc(L.primary_slot || '—')}</dd>
      ${L.access?.length ? `<dt>Opened by</dt><dd class="mono">${esc(L.access.join(', '))}</dd>` : ''}</dl>
      ${L.placement_evidence ? `<details class="more"><summary>Placement evidence</summary><p class="cc-p">${esc(L.placement_evidence)}</p></details>` : ''}` : '<p class="muted">No layout metadata.</p>'}`;
}

/* visibility (agent / evaluator) is only in the asset contract, which is fetched when this tab is first opened */
const contracts = new Map();
function contractInfo(b) {
  if (!b.contract) return { status: 'none' };
  let e = contracts.get(b.tid);
  if (!e) {
    e = { status: 'loading' };
    contracts.set(b.tid, e);
    fetch(dataUrl(b.contract))
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((c) => {
        e.state = new Map(Object.entries(c.state_slots || {}).map(([k, v]) => [k, v?.visibility]));
        e.caps = new Map(Object.entries(c.capabilities || {}).map(([k, v]) => [k, v?.visibility]));
        e.status = 'ready';
      })
      .catch((err) => { e.status = 'error'; e.error = err; })
      .finally(() => { if (S.bundle?.tid === b.tid && S.tab === 'capabilities') rerenderTab(); });
  }
  return e;
}
function visCell(info, map, key) {
  if (info.status === 'ready') { const v = info[map].get(key); return v ? pill(v) : NA; }
  if (info.status === 'loading') return '<span class="faint">…</span>';
  return NA;
}
function tabCapabilities(b) {
  const info = contractInfo(b);
  const caps = Object.entries(b.capabilities), av = countBy(caps.map(([, c]) => c), 'availability');
  const cov = b.checks.coverage || {}, inIsaac = new Set(cov.implemented || []), notIsaac = new Set(cov.unsupported || []);
  const states = Object.entries(b.state_slots), mechs = Object.entries(b.mechanisms);
  const visNote = info.status === 'none' ? 'This first-generation asset has no contract, so state visibility is not recorded.'
    : info.status === 'loading' ? 'Reading visibility from the asset contract…'
      : info.status === 'error' ? `Visibility could not be read from the asset contract (${esc(describeError(info.error))}) <button type="button" class="linkish" data-act="retry-contract">Try again</button>`
        : 'Visibility: <b>agent</b> = the robot or policy may read it; <b>evaluator</b> = hidden truth for scoring and set-up.';
  const capItems = caps.map(([k, c]) => `<li class="cap" data-q="${esc(`${k} ${c.function_zh || ''} ${c.kind || ''}`.toLowerCase())}"><div class="cap-top"><span class="cap-id">${esc(k)}</span>${pill(c.availability)}${inIsaac.has(k) ? '<span class="pill t-info" title="Available in the Isaac Lab scene">Isaac Lab</span>' : notIsaac.has(k) ? '<span class="pill t-neutral" title="Not available in the Isaac Lab scene">not in Isaac Lab</span>' : ''}${info.status === 'ready' && info.caps.get(k) ? pill(info.caps.get(k)) : ''}</div>
    ${c.function_zh ? zh(c.function_zh, 'div', 'cap-fn') : ''}
    <div class="cap-meta">${c.kind ? `<span>${esc(humanize(c.kind))}</span>` : ''}${c.input?.length ? `<span>inputs <span class="mono">${esc(c.input.join(', '))}</span></span>` : ''}${c.implementation ? LEVELS.filter((l) => c.implementation[l]).map((l) => `<span class="lv t-${toneOf(c.implementation[l])}" title="${esc(l)}: ${esc(c.implementation[l])}"><i></i>${esc(humanize(l))} ${esc(label(c.implementation[l]).toLowerCase())}</span>`).join('') : ''}</div></li>`).join('');
  const stateRows = states.map(([k, s]) => `<tr data-q="${esc(`${k} ${s.meaning_zh || ''}`.toLowerCase())}"><td><span class="idc">${esc(k)}</span>${zh(s.meaning_zh, 'span', 'sub')}</td><td class="mono" style="font-size:.74rem">${esc(s.unit ?? '') || NA}</td><td>${visCell(info, 'state', k)}</td><td class="nw">${esc(label(s.access || '—'))}<span class="sub">${esc(label(s.origin || ''))}</span>${s.write_via ? `<span class="sub mono" title="Written through this capability">via ${esc(s.write_via)}</span>` : ''}</td></tr>`).join('');
  const mechHtml = mechs.map(([k, m]) => `<div class="card"><h4><span class="mono">${esc(k)}</span>${m.implementation ? pill(m.implementation) : ''}</h4>${m.meaning_zh ? zh(m.meaning_zh, 'p', 'cc-p') : ''}${(m.equations || []).map((e) => `<div class="eq">${esc(e)}</div>`).join('')}
    ${Object.keys(m.parameters || {}).length ? `<details class="more"><summary>Parameters (${Object.keys(m.parameters).length})</summary><div class="tw"><table class="t"><thead><tr><th scope="col">Parameter</th><th scope="col" class="num">Value</th><th scope="col">Unit</th><th scope="col">Evidence</th></tr></thead><tbody>${Object.entries(m.parameters).map(([pk, pv]) => `<tr><td class="idc" title="${esc(pv?.meaning_zh || '')}">${esc(pk)}${pv?.meaning_zh ? zh(pv.meaning_zh, 'span', 'sub') : ''}</td><td class="num">${fmt(pv?.value, 4)}</td><td>${esc(pv?.unit || '')}</td><td>${pill(pv?.evidence)}</td></tr>`).join('')}</tbody></table></div></details>` : ''}</div>`).join('');
  return `<div class="panel-title">Capabilities &amp; mechanisms</div><p class="lede">What the behaviour runtime can do (capabilities), what it exposes (state slots) and the physics behind it (mechanisms).</p>
    <input class="filter" id="cap-filter" type="search" placeholder="Filter capabilities and state slots" aria-label="Filter capabilities and state slots" value="${esc(S.capFilter)}">
    ${secH(`Capabilities (${caps.length})`, ['implemented', 'unsupported', 'declared'].filter((k) => av[k]).map((k) => `${av[k]} ${k}`).join(' · '))}
    ${caps.length ? `<ul class="caps" id="cap-list">${capItems}</ul>` : '<p class="muted">No capabilities are listed.</p>'}
    ${secH(`State slots (${states.length})`)}
    <p class="lede">${visNote}</p>
    ${states.length ? `<div class="tw"><table class="t" id="state-table"><thead><tr><th scope="col">State</th><th scope="col">Unit</th><th scope="col">Visibility</th><th scope="col">Access</th></tr></thead><tbody>${stateRows}</tbody></table></div>` : `<p class="muted">${b.has_contract ? 'No state slots are listed.' : 'State is described in physics_annotations.json for this first-generation asset.'}</p>`}
    ${secH(`Mechanisms (${mechs.length})`)}
    ${mechHtml || '<p class="muted">No mechanisms are listed.</p>'}
    ${b.limitations.length ? `${secH(`Known limitations (${b.limitations.length})`, '<span class="tag-zh">in Chinese</span>')}<ul class="zh-list" lang="zh">${b.limitations.map((l) => `<li>${esc(typeof l === 'string' ? l : JSON.stringify(l))}</li>`).join('')}</ul>` : ''}`;
}
function applyCapFilter() {
  const q = S.capFilter.trim().toLowerCase();
  for (const el of $$('#cap-list .cap, #state-table tbody tr')) el.hidden = !!q && !(el.dataset.q || '').includes(q);
}
const TEXT_EXT = /\.(json|urdf|md|py|txt|html|xml|csv|yaml|yml)$/i;
function tabFiles(b) {
  const files = [...b.files].sort((x, y) => x.name.localeCompare(y.name));
  const total = files.reduce((s, f) => s + (f.bytes || 0), 0);
  const tgt = ON_HUB ? '' : ' target="_blank" rel="noopener"';
  const row = (f, note = '') => `<tr><td><a href="${esc(downloadUrl(f.path))}" download${tgt}><span class="idc">${esc(f.name)}</span></a>${note ? `<span class="sub">${note}</span>` : ''}</td><td class="num">${esc(bytes(f.bytes)) || NA}</td><td class="nw">${TEXT_EXT.test(f.name) ? `<a href="${esc(hubBlob(f.path))}" target="_blank" rel="noopener">view</a>` : ''}</td></tr>`;
  const extra = [];
  if (b.runtime) extra.push(row({ name: b.runtime.split('/').pop(), path: b.runtime }, `runtime module · <span class="mono">${esc(b.runtime)}</span>`));
  return `<div class="panel-title">Files</div><p class="lede">Everything the dataset ships for this instrument. Files download from the dataset repository.</p>
    <dl class="kv"><dt>Folder</dt><dd class="mono" style="font-size:.76rem">${esc(b.path || b.asset_dir || '—')}</dd><dt>On the Hub</dt><dd><a href="${esc(hubTree(b.path || b.asset_dir || ''))}" target="_blank" rel="noopener">Browse the folder ↗</a></dd></dl>
    ${secH(`Asset folder (${files.length})`, esc(bytes(total)))}
    <div class="tw"><table class="t"><thead><tr><th scope="col">File</th><th scope="col" class="num">Size</th><th scope="col"><span class="sr">View</span></th></tr></thead><tbody>${files.map((f) => row(f, f.path.split('/').slice(0, -1).join('/') !== (b.path || '') ? `<span class="mono">${esc(f.path)}</span>` : '')).join('')}</tbody></table></div>
    ${extra.length ? `${secH('Shared code')}<div class="tw"><table class="t"><tbody>${extra.join('')}</tbody></table></div>` : ''}
    ${b.previews.length ? `${secH(`Preview renders (${b.previews.length})`)}<p class="cc-p"><a href="${esc(hubTree(`previews/${b.slug}`))}" target="_blank" rel="noopener">previews/${esc(b.slug)}/ on the Hub ↗</a></p>` : ''}
    <p class="lede" style="margin-top:14px">Assets are licensed CC BY-NC 4.0; see the dataset card for terms and citation.</p>`;
}

// delegated events inside the panel
$('#tab-body').addEventListener('click', (e) => {
  const t = e.target;
  const act = t.closest('[data-act]')?.dataset.act;
  if (act === 'tab-files') { setTab('files'); return; }
  if (act === 'retry-contract' && S.bundle) { contracts.delete(S.bundle.tid); rerenderTab(); return; }
  const shot = t.closest('.shot');
  if (shot && S.bundle) { openLightbox(S.bundle.previews.map((p) => ({ src: dataUrl(p), cap: previewCaption(p) })), Number(shot.dataset.shot)); return; }
  const comp = t.closest('tr[data-comp]');
  if (comp) { selectComponent(comp.dataset.comp, { focus: true, from: 'table' }); return; }
  const jr = t.closest('[data-joint]');
  if (jr) selectJoint(jr.dataset.joint);
});
$('#tab-body').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const tr = e.target.closest('tr[data-comp], [data-joint]');
  if (!tr || e.target !== tr) return;
  e.preventDefault();
  tr.click();
});
$('#tab-body').addEventListener('input', (e) => {
  if (e.target.id === 'comp-filter') { S.compFilter = e.target.value; applyCompFilter(); }
  if (e.target.id === 'cap-filter') { S.capFilter = e.target.value; applyCapFilter(); }
});
$('#pick-card').addEventListener('click', (e) => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'deselect') clearSelection();
  if (act === 'show-comp') { setTab('components'); $(`#tab-body tr[data-comp="${CSS.escape(S.selected || '')}"]`)?.scrollIntoView({ block: 'nearest' }); }
});
$('#viewer-error').addEventListener('click', (e) => {
  if (e.target.closest('[data-act="retry"]')) {
    const tid = S.tid;
    S.tid = null;
    if (S.bundleError) bundleCache.delete(tid);
    selectInstrument(tid);
  }
});

/* ======================================================================================= viewer toolbar */
function buildLayers() {
  const groups = [...new Set(LAYERS.map((l) => l.group))];
  $('#layers').innerHTML = groups.map((g) => `<fieldset><legend>${esc(g)}</legend>${LAYERS.filter((l) => l.group === g).map((l) => `<label><input type="checkbox" data-layer="${l.key}"${S.layers[l.key] ? ' checked' : ''}>${esc(l.label)}${l.sw ? `<span class="sw" aria-hidden="true">${l.sw.map((r) => `<i style="background:var(--ov-${r})"></i>`).join('')}</span>` : ''}</label>`).join('')}</fieldset>`).join('')
    + '<p class="note">Labels hide when their part is small on screen; zoom in to see them.</p>';
  updateLayerCount();
}
function updateLayerCount() {
  const n = LAYERS.filter((l) => l.group === 'Annotations' && l.key !== 'labels' && S.layers[l.key]).length;
  $('#layers-count').textContent = String(n);
  $('#layers-count').title = `${n} annotation layers shown`;
}
function closeLayers() { if (!$('#layers').hidden) { $('#layers').hidden = true; $('#layers-btn').setAttribute('aria-expanded', 'false'); } }
$('#layers-btn').addEventListener('click', () => {
  const open = $('#layers').hidden;
  $('#layers').hidden = !open;
  $('#layers-btn').setAttribute('aria-expanded', String(open));
});
$('#layers').addEventListener('change', (e) => {
  const k = e.target.dataset?.layer;
  if (!k) return;
  S.layers[k] = e.target.checked;
  applyLayers();
  updateLayerCount();
});
document.addEventListener('pointerdown', (e) => { if (!e.target.closest('.vt-left')) closeLayers(); });
for (const b of $$('.vt-right [data-cam]')) b.addEventListener('click', () => fitView(b.dataset.cam));
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || $('#lightbox').open) return;
  if (!$('#layers').hidden) { closeLayers(); $('#layers-btn').focus(); return; }
  clearSelection();
});
setTimeout(() => $('#hint').classList.add('gone'), 12000);

/* ---------------------------------------------------------------------------- preview lightbox */
const LB = { items: [], i: 0 };
function openLightbox(items, i) {
  if (!items.length) return;
  LB.items = items;
  LB.i = clamp(i || 0, 0, items.length - 1);
  showLightboxItem();
  const d = $('#lightbox');
  if (!d.open) d.showModal();
}
function showLightboxItem() {
  const it = LB.items[LB.i];
  $('#lb-img').src = it.src;
  $('#lb-img').alt = it.cap;
  $('#lb-cap').textContent = `${it.cap} · ${LB.i + 1} of ${LB.items.length}`;
  $('#lb-prev').hidden = $('#lb-next').hidden = LB.items.length < 2;
}
const stepLightbox = (d) => { LB.i = (LB.i + d + LB.items.length) % LB.items.length; showLightboxItem(); };
$('#lb-prev').addEventListener('click', () => stepLightbox(-1));
$('#lb-next').addEventListener('click', () => stepLightbox(1));
$('#lb-close').addEventListener('click', () => $('#lightbox').close());
$('#lightbox').addEventListener('click', (e) => { if (e.target === e.currentTarget || e.target.classList.contains('lb-fig')) $('#lightbox').close(); });
$('#lightbox').addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') { e.preventDefault(); stepLightbox(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); stepLightbox(1); }
});
$('#lightbox').addEventListener('close', () => { $('#lb-img').removeAttribute('src'); });

/* ---------------------------------------------------------------------------- toast */
let toastTimer = null;
function toast(msg, ms = 6000) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

/* ======================================================================================= start */
async function init() {
  setupHeader();
  buildLayers();
  if (!GL) $('#hint').hidden = true;
  let idx;
  try {
    const r = await fetch('data/index.json');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    idx = await r.json();
  } catch (e) {
    $('#asset-list').innerHTML = `<li class="list-msg">The instrument catalogue could not be loaded. ${esc(describeError(e))}</li>`;
    $('#inst-name').textContent = 'Catalogue unavailable';
    $('#tab-body').innerHTML = '<p class="empty">Reload the page to try again.</p>';
    showViewerError('The catalogue could not be loaded', `data/index.json could not be read. ${describeError(e)}`);
    return;
  }
  S.index = Array.isArray(idx.assets) ? idx.assets : [];
  S.domains = Array.isArray(idx.domains) ? idx.domains : [];
  for (const d of S.domains) S.domainMap.set(d.key, d);
  for (const a of S.index) {
    S.byTid.set(a.tid, a);
    a._hay = [a.name, a.maker, a.model, a.name_zh, a.chem_id, a.slug, a.tid, domainName(a.domain)].filter(Boolean).join(' ').toLowerCase();
  }
  $('#tagline').textContent = `${S.index.length} simulation-ready chemistry lab instruments in 3D`;
  buildDomainSelect();
  buildList();
  applyFilter();
  const { tid, asked } = tidFromLocation();
  if (!tid && asked) toast(`“${asked}” is not an instrument in this release. Showing the fume hood instead.`);
  const start = tid || (S.byTid.has(DEFAULT_TID) ? DEFAULT_TID : S.index[0]?.tid);
  if (!start) return;
  const p = selectInstrument(start);
  requestAnimationFrame(() => scrollActiveIntoView());
  await p;
}
init();

// Small read-only handle for automated checks and the browser console.
window.__explorer = {
  version: '1.0', dataBase: DATA_BASE, space: SPACE_ID, state: S, THREE, camera, scene, assetRoot,
  get controls() { return controls; }, get pivots() { return pivots; }, get meshes() { return meshes; }, get compMeshes() { return compMeshes; },
  setJoint, fitView, selectInstrument, selectComponent, setView, setTab,
};
