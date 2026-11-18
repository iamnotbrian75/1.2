// TITAN GARAGE - Animagraffs-style 3D build guides.
//   * real 3D files (GLB / glTF / OBJ) or built-in schematic shapes
//   * MAP PARTS: link the meshes of any downloaded model to the guide's parts
//   * labels with pointer lines, animated build steps, auto-play
//   * moving colour flows (coolant, air, boost) and a cutaway slice
//   * studio lighting with physically based materials
// Loaded as an ES module by index.html. three.js lives in /vendor (offline).
import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";
import { STLExporter } from "/vendor/STLExporter.js";
import { GLTFLoader } from "/vendor/GLTFLoader.js";
import { OBJLoader } from "/vendor/OBJLoader.js";
import { RoomEnvironment } from "/vendor/RoomEnvironment.js";

const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
// ink colours that read on cream paper
const STATUS = {
  owned: ["#2f7a4a", "OWNED"], considering: ["#b07418", "CONSIDERING"], check: ["#b0301c", "CHECK"],
  ordered: ["#2f4f9a", "ORDERED"], fitted: ["#3a2716", "DONE"], needed: ["#c2561a", "NEEDED"],
};
const NONE = ["#8a7660", "INFO"];
const DEG = Math.PI / 180;
const REMOVE_DISTANCE = 1.9;          // how far (in explode vectors) a removed part slides away
const INK = "#3a2716", INK_ACCENT = "#8a3b1c";
const INK_LAYER = 1;                  // ink outlines: seen by the main view AND the line-only elevations
const MAX_INK_TRIANGLES = 400000;     // skip outlines on very heavy downloaded models
function inkFor(mesh) {
  const lines = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 28),
    new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.75 }));
  lines.userData.edge = true; lines.layers.set(INK_LAYER);
  mesh.add(lines);
  return lines;
}

const g = {
  open: false, spec: null, project: null, items: {}, assets: [],
  parts: {},            // id -> {part, group, base, ex, meshes, removal, target, placeholder}
  assetMeshes: [],      // every mesh of a loaded real model
  meshKey: new Map(),   // mesh -> stable key "index:name"
  step: -1, selected: null, explode: 0.6, labels: "auto", flowsOn: true, cut: 0,
  map: { on: false, part: null, edits: null, rotate: [0, 0, 0], scale: 1 },
  renderer: null, scene: null, camera: null, controls: null, root: null, flows: [], plane: null,
  camGoal: null, clock: new THREE.Clock(), raf: 0, playTimer: 0,
};

// ---------- materials ----------
function stripes(color, dark, vertical, gap) {
  const c = document.createElement("canvas"); c.width = c.height = 128;
  const x = c.getContext("2d"); x.fillStyle = color; x.fillRect(0, 0, 128, 128); x.fillStyle = dark;
  for (let i = 0; i < 128; i += gap) vertical ? x.fillRect(i, 0, gap / 2, 128) : x.fillRect(0, i, 128, gap / 2);
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(6, 6); t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
function honeycomb() {
  const c = document.createElement("canvas"); c.width = c.height = 128;
  const x = c.getContext("2d"); x.fillStyle = "#0b0b0d"; x.fillRect(0, 0, 128, 128); x.strokeStyle = "#2a2a2e"; x.lineWidth = 3;
  for (let row = 0; row < 9; row++) for (let col = 0; col < 9; col++) {
    const cx = col * 16 + (row % 2) * 8, cy = row * 14; x.beginPath();
    for (let k = 0; k < 6; k++) { const a = Math.PI / 3 * k + Math.PI / 6; x.lineTo(cx + 7 * Math.cos(a), cy + 7 * Math.sin(a)); }
    x.closePath(); x.stroke();
  }
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(5, 2); t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
function materialFor(part) {
  const id = part.id + " " + (part.name || "").toLowerCase();
  const base = { color: part.color || "#5b5d63", transparent: true, side: THREE.DoubleSide };
  if (/headlight/.test(id)) return new THREE.MeshPhysicalMaterial({ ...base, color: "#dfe7ee", metalness: 0.1, roughness: 0.05, transmission: 0.6, thickness: 20, clearcoat: 1 });
  if (/radiator|condenser|intercooler/.test(id)) return new THREE.MeshStandardMaterial({ ...base, color: "#b9bec6", map: stripes("#b9bec6", "#6e737b", true, 6), metalness: 0.8, roughness: 0.45 });
  if (/grille|kidney/.test(id)) return new THREE.MeshStandardMaterial({ ...base, color: "#d8d2c8", map: honeycomb(), metalness: 0.3, roughness: 0.5 });
  // illustration palette: satin gunmetal paint, bright chrome/steel, warm dark plastics
  if (/bumper|bonnet|wing/.test(id)) return new THREE.MeshPhysicalMaterial({ ...base, color: "#8a8e95", metalness: 0.45, roughness: 0.38, clearcoat: 0.7, clearcoatRoughness: 0.3 });
  if (/carrier|crash|slam|leg|latch|crumpled/.test(id)) return new THREE.MeshStandardMaterial({ ...base, color: "#c9ccd1", metalness: 0.9, roughness: 0.25 });
  if (/pipe|fan|snorkel|airbox|frame|unit|canbus|obd/.test(id)) return new THREE.MeshStandardMaterial({ ...base, color: "#5a4a3c", metalness: 0.1, roughness: 0.6 });
  return new THREE.MeshStandardMaterial({ ...base, metalness: 0.4, roughness: 0.5 });
}

// ---------- schematic geometry from the JSON spec ----------
function geometry(shape) {
  switch (shape.type) {
    case "box": {
      const geo = new THREE.BoxGeometry(shape.w, shape.h, shape.d, 1, 1, 1);
      return geo;
    }
    case "cylinder": return new THREE.CylinderGeometry(shape.rTop ?? shape.r, shape.r, shape.h, 48);
    case "sphere": return new THREE.SphereGeometry(shape.r, 32, 16);
    case "tube": {
      const a = shape.r_in, b = shape.r_out, h = shape.h / 2;
      return new THREE.LatheGeometry([[a, -h], [b, -h], [b, h], [a, h], [a, -h]].map(([x, y]) => new THREE.Vector2(x, y)), 72);
    }
    case "plate": {
      const w = shape.w / 2, h = shape.h / 2, r = Math.min(shape.r || 0, w, h), s = new THREE.Shape();
      s.moveTo(-w + r, -h); s.lineTo(w - r, -h); s.quadraticCurveTo(w, -h, w, -h + r); s.lineTo(w, h - r);
      s.quadraticCurveTo(w, h, w - r, h); s.lineTo(-w + r, h); s.quadraticCurveTo(-w, h, -w, h - r);
      s.lineTo(-w, -h + r); s.quadraticCurveTo(-w, -h, -w + r, -h);
      for (const [x, y, rad] of shape.holes || []) { const p = new THREE.Path(); p.absarc(x, y, rad, 0, Math.PI * 2, true); s.holes.push(p); }
      const geo = new THREE.ExtrudeGeometry(s, { depth: shape.t, bevelEnabled: false, curveSegments: 32 });
      geo.translate(0, 0, -shape.t / 2);
      return geo;
    }
    default: return new THREE.BoxGeometry(10, 10, 10);
  }
}
function statusOf(part) {
  const item = part.item && g.items[part.item];
  return item ? (STATUS[item.status] || NONE) : NONE;
}

// ---------- scene ----------
function ensureRenderer() {
  if (g.renderer) return;
  const canvas = $("#gCanvas");
  g.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  g.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  g.renderer.toneMapping = THREE.ACESFilmicToneMapping; g.renderer.toneMappingExposure = 1.25;
  g.renderer.autoClear = false;
  g.renderer.outputColorSpace = THREE.SRGBColorSpace;
  g.renderer.localClippingEnabled = true;
  g.scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(g.renderer);
  g.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;      // studio reflections
  g.camera = new THREE.PerspectiveCamera(35, 1, 1, 50000);
  g.camera.layers.enable(INK_LAYER);
  g.ortho = { side: new THREE.OrthographicCamera(), front: new THREE.OrthographicCamera() };
  for (const cam of Object.values(g.ortho)) cam.layers.set(INK_LAYER);         // line-only drawings
  g.controls = new OrbitControls(g.camera, canvas);
  g.controls.enableDamping = true; g.controls.dampingFactor = 0.08;
  g.controls.addEventListener("start", () => { g.camGoal = null; });
  // warm "illustration" lighting to sit on aged paper
  g.scene.add(new THREE.HemisphereLight(0xfff6e6, 0x8a6a48, 1.1));
  const key = new THREE.DirectionalLight(0xfff1dc, 1.7); key.position.set(1, 2, 1.5); g.scene.add(key);
  const fill = new THREE.DirectionalLight(0xe8dcc4, 0.6); fill.position.set(-1.5, 0.6, 1); g.scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffd9a8, 0.4); rim.position.set(-2, 0.2, -2.5); g.scene.add(rim);
  g.plane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 1e9);
  canvas.addEventListener("pointerup", pick);
  new ResizeObserver(resize).observe(canvas);
}
function resize() {
  const c = $("#gCanvas"), w = c.clientWidth, h = c.clientHeight;
  if (!w || !h || !g.renderer) return;
  g.renderer.setSize(w, h, false); g.camera.aspect = w / h; g.camera.updateProjectionMatrix();
}
function dispose(obj) {
  obj.traverse((o) => {
    o.geometry?.dispose?.();
    for (const m of [].concat(o.material || [])) { m.map?.dispose?.(); m.dispose?.(); }
  });
}
function forMaterials(mesh, fn) { for (const m of [].concat(mesh.material)) fn(m); }

function buildSchematic() {
  const parts = {};
  for (const part of g.spec.parts) {
    const group = new THREE.Group();
    const mesh = new THREE.Mesh(geometry(part.shape), materialFor(part));
    const [rx, ry, rz] = part.rot || [0, 0, 0];
    mesh.rotation.set(rx * DEG, ry * DEG, rz * DEG);
    inkFor(mesh);
    mesh.userData.partId = part.id;
    group.add(mesh);
    parts[part.id] = { part, group, base: new THREE.Vector3(...(part.pos || [0, 0, 0])), ex: new THREE.Vector3(...(part.explode || [0, 0, 0])),
                       meshes: [mesh], removal: 0, target: 0, placeholder: true };
  }
  return parts;
}

async function loadAsset(spec) {
  const r = await fetch("/api/garage/asset?name=" + encodeURIComponent(spec.asset), { headers: { "X-Titan-Token": window.titanToken() } });
  if (!r.ok) throw new Error("couldn't download the 3D file");
  const buf = await r.arrayBuffer();
  if (/\.obj$/i.test(spec.asset)) return new OBJLoader().parse(new TextDecoder().decode(buf));
  const gltf = await new Promise((res, rej) => new GLTFLoader().parse(buf, "", res, rej));
  return gltf.scene;
}

function specBox(parts) {
  const box = new THREE.Box3();
  for (const p of Object.values(parts)) { p.group.position.copy(p.base); p.group.updateMatrixWorld(true); box.expandByObject(p.group); }
  return box;
}

async function build(spec) {
  if (g.root) { g.scene.remove(g.root); dispose(g.root); }
  for (const f of g.flows) { g.scene.remove(f.obj); dispose(f.obj); }
  g.root = new THREE.Group(); g.flows = []; g.assetMeshes = []; g.meshKey = new Map();
  g.parts = buildSchematic();
  for (const p of Object.values(g.parts)) g.root.add(p.group);
  const target = specBox(g.parts);
  $("#gAssetNote").textContent = "";

  if (spec.asset) {
    try {
      $("#gAssetNote").textContent = "Loading 3D file…";
      const model = await loadAsset(spec);
      const rot = g.map.on && g.map.edits ? g.map.rotate : (spec.asset_rotate || [0, 0, 0]);
      model.rotation.set(rot[0] * DEG, rot[1] * DEG, rot[2] * DEG);
      model.updateMatrixWorld(true);
      let box = new THREE.Box3().setFromObject(model);
      const manual = g.map.on && g.map.edits ? g.map.scale : (spec.asset_scale || 1);
      const auto = (target.getSize(new THREE.Vector3()).x || 1) / (box.getSize(new THREE.Vector3()).x || 1);
      model.scale.setScalar(auto * manual);
      model.updateMatrixWorld(true);
      box = new THREE.Box3().setFromObject(model);
      const shift = target.getCenter(new THREE.Vector3()).sub(box.getCenter(new THREE.Vector3()));
      shift.y = target.min.y - box.min.y;
      model.position.add(shift);
      g.root.add(model); model.updateMatrixWorld(true);
      let i = 0, triangles = 0;
      model.traverse((o) => {
        if (!o.isMesh) return;
        o.material = Array.isArray(o.material) ? o.material.map((m) => m.clone()) : o.material.clone();
        forMaterials(o, (m) => { m.transparent = true; });
        g.meshKey.set(o, `${i++}:${o.name || o.parent?.name || "mesh"}`);
        g.assetMeshes.push(o);
        triangles += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3;
      });
      if (triangles <= MAX_INK_TRIANGLES) for (const mesh of g.assetMeshes) inkFor(mesh);
      // move each mapped mesh into its part's group so explode / steps / labels work on it
      const map = g.map.on && g.map.edits ? g.map.edits : (spec.mesh_map || {});
      for (const p of Object.values(g.parts)) p.assetMeshes = [];
      for (const mesh of g.assetMeshes) {
        const id = map[g.meshKey.get(mesh)];
        if (id && g.parts[id]) g.parts[id].assetMeshes.push(mesh);
      }
      for (const p of Object.values(g.parts)) {
        if (!p.assetMeshes.length) { p.group.visible = g.map.on; p.ghost = true; continue; }
        const holder = new THREE.Group();
        g.root.add(holder); holder.updateMatrixWorld(true);
        for (const mesh of p.assetMeshes) { holder.attach(mesh); mesh.userData.partId = p.part.id; }
        g.root.remove(p.group); dispose(p.group);
        p.group = holder; p.base = new THREE.Vector3(); p.meshes = p.assetMeshes; p.placeholder = false; p.ghost = false;
      }
      const mapped = Object.values(g.parts).filter((p) => !p.placeholder).length;
      $("#gAssetNote").textContent = `${spec.asset} · ${mapped}/${spec.parts.length} parts mapped` + (mapped ? "" : " · use MAP PARTS to link them");
    } catch (err) {
      $("#gAssetNote").textContent = `Couldn't load ${spec.asset}: ${err.message}. Showing the schematic instead.`;
    }
  }
  g.scene.add(g.root);
  buildFlows(spec);
  applyClip();
  for (const p of Object.values(g.parts)) { p.removal = p.target = 0; }
  applyStepTargets(true);
  placeParts();
  frameCamera(true);
}

// ---------- flows (coolant, air, boost) ----------
function buildFlows(spec) {
  for (const f of spec.flows || []) {
    const curve = new THREE.CatmullRomCurve3(f.path.map((p) => new THREE.Vector3(...p)), false, "centripetal");
    const obj = new THREE.Group();
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 120, f.radius || 9, 10, false),
      new THREE.MeshBasicMaterial({ color: f.color, transparent: true, opacity: 0.22, depthWrite: false }));
    obj.add(tube);
    const count = f.particles || 36;
    const dots = new THREE.InstancedMesh(new THREE.SphereGeometry((f.radius || 9) * 1.3, 10, 8),
      new THREE.MeshBasicMaterial({ color: f.color, transparent: true, opacity: 0.95, depthWrite: false }), count);
    obj.add(dots);
    g.scene.add(obj);
    g.flows.push({ spec: f, curve, obj, dots, count, t: 0 });
  }
  renderFlowLegend();
}
function updateFlows(dt) {
  const active = g.step >= 0 ? new Set(g.spec.steps[g.step].flows || []) : null;
  const m = new THREE.Matrix4();
  for (const f of g.flows) {
    const show = g.flowsOn && (!active || active.has(f.spec.id));
    f.obj.visible = show;
    if (!show) continue;
    f.t = (f.t + dt * (f.spec.speed || 0.08)) % 1;
    for (let i = 0; i < f.count; i++) {
      const p = f.curve.getPointAt((f.t + i / f.count) % 1);
      m.makeTranslation(p.x, p.y, p.z); f.dots.setMatrixAt(i, m);
    }
    f.dots.instanceMatrix.needsUpdate = true;
  }
}
function renderFlowLegend() {
  const box = $("#gFlowLegend"); box.textContent = "";
  for (const f of g.flows) {
    const s = el("span"); const i = el("i"); i.style.background = f.spec.color; i.style.boxShadow = `0 0 8px ${f.spec.color}`;
    s.append(i, f.spec.name); box.append(s);
  }
  $("#gFlows").hidden = !g.flows.length;
}

// ---------- cutaway ----------
function applyClip() {
  const planes = g.cut > 0 ? [g.plane] : [];
  if (g.cut > 0 && g.root) {
    const box = new THREE.Box3().setFromObject(g.root), size = box.getSize(new THREE.Vector3());
    g.plane.constant = box.max.x - g.cut * size.x;
  }
  g.root?.traverse((o) => { if (o.material) for (const m of [].concat(o.material)) { m.clippingPlanes = planes; m.clipShadows = true; m.needsUpdate = true; } });
}

// ---------- steps (animated) ----------
function stepOff() {
  return g.step >= 0 ? new Set(g.spec.steps[g.step].off || []) : new Set();
}
function applyStepTargets(instant) {
  const off = stepOff();
  for (const [id, p] of Object.entries(g.parts)) {
    p.target = off.has(id) ? 1 : 0;
    if (instant) p.removal = p.target;
  }
  applyFocus();
}
function placeParts() {
  for (const p of Object.values(g.parts)) {
    const k = g.explode + p.removal * REMOVE_DISTANCE;
    p.group.position.copy(p.base).addScaledVector(p.ex, g.step >= 0 ? p.removal * REMOVE_DISTANCE : k);
    p.group.visible = !(p.ghost && !g.map.on) && p.removal < 0.98;
  }
}
function applyFocus() {
  const stepParts = g.step >= 0 ? new Set(g.spec.steps[g.step].parts || []) : null;
  for (const [id, p] of Object.entries(g.parts)) {
    const sel = g.selected === id, mapSel = g.map.on && g.map.part === id;
    const ghost = (stepParts && !stepParts.has(id) && !sel) || (!stepParts && g.selected && !sel);
    for (const mesh of p.meshes) {
      forMaterials(mesh, (m) => {
        m.userData.baseOpacity ??= m.opacity;
        m.opacity = (p.ghost ? 0.25 : ghost ? 0.14 : m.userData.baseOpacity) * (1 - p.removal * 0.9);
        m.wireframe = !!(p.ghost && g.map.on);
        if (m.emissive) { m.emissive.set(sel || mapSel ? "#ff3b30" : stepParts && !ghost ? "#2a0806" : "#000000"); m.emissiveIntensity = sel || mapSel ? 0.6 : 1; }
      });
      mesh.children.forEach((c) => {
        if (!c.userData.edge) return;
        c.material.opacity = ghost ? 0.12 : 0.75;
        c.material.color.set(sel || mapSel ? INK_ACCENT : INK);
      });
    }
  }
}
function animate(dt) {
  let moving = false;
  const k = 1 - Math.exp(-dt * 4.5);
  for (const p of Object.values(g.parts)) {
    if (Math.abs(p.target - p.removal) > 0.001) { p.removal += (p.target - p.removal) * k; moving = true; }
    else p.removal = p.target;
  }
  if (moving) { placeParts(); applyFocus(); }
  if (g.camGoal) {
    g.camera.position.lerp(g.camGoal.pos, k); g.controls.target.lerp(g.camGoal.target, k);
    if (g.camera.position.distanceTo(g.camGoal.pos) < 1) g.camGoal = null;
  }
}
function setStep(i) {
  g.step = i; g.selected = null; $("#gNote").hidden = true;
  applyStepTargets(false); renderSteps(); focusCamera();
}
function play() {
  const btn = $("#gPlay");
  if (g.playTimer) { clearInterval(g.playTimer); g.playTimer = 0; btn.textContent = "▶ PLAY"; return; }
  btn.textContent = "❚❚ PAUSE";
  const steps = g.spec.steps || [];
  setStep(g.step < 0 || g.step >= steps.length - 1 ? 0 : g.step + 1);
  g.playTimer = setInterval(() => {
    if (g.step >= steps.length - 1) { play(); return; }
    setStep(g.step + 1);
  }, 4500);
}

// ---------- camera ----------
function fitFor(box) {
  const sphere = box.getBoundingSphere(new THREE.Sphere()), r = sphere.radius || 100;
  const vFov = g.camera.fov * DEG, hFov = 2 * Math.atan(Math.tan(vFov / 2) * g.camera.aspect);
  const dist = (r / Math.sin(Math.min(vFov, hFov) / 2)) * 1.08;
  return { target: sphere.center.clone(), pos: sphere.center.clone().add(new THREE.Vector3(1.25, 0.8, 1.6).normalize().multiplyScalar(dist)), r };
}
function frameCamera(instant) {
  const box = new THREE.Box3();
  for (const p of Object.values(g.parts)) if (p.group.visible) box.expandByObject(p.group);
  if (box.isEmpty()) box.setFromObject(g.root);
  const f = fitFor(box);
  g.camera.near = f.r / 100; g.camera.far = f.r * 60; g.camera.updateProjectionMatrix();
  if (instant) { g.camera.position.copy(f.pos); g.controls.target.copy(f.target); g.controls.update(); g.camGoal = null; }
  else g.camGoal = f;
}
function focusCamera() {
  if (g.step < 0) { frameCamera(false); return; }
  const ids = g.spec.steps[g.step].parts || [], box = new THREE.Box3();
  for (const id of ids) { const p = g.parts[id]; if (p && !p.ghost) box.expandByObject(p.group); }
  if (box.isEmpty()) { frameCamera(false); return; }
  const f = fitFor(box.expandByScalar(box.getSize(new THREE.Vector3()).length() * 0.35));
  g.camGoal = f;
}

// ---------- labels with pointer lines ----------
const tmp = new THREE.Vector3();
function labelParts() {
  if (g.labels === "off" || g.map.on) return [];
  let ids;
  if (g.step >= 0) ids = g.spec.steps[g.step].parts || [];
  else if (g.labels === "all") ids = Object.keys(g.parts);
  else {
    const seen = new Set(); ids = [];
    for (const [id, p] of Object.entries(g.parts)) {           // one label per project item, max 14
      const key = p.part.item || id;
      if (seen.has(key)) continue; seen.add(key); ids.push(id);
    }
    ids = ids.slice(0, $("#gCanvas").clientWidth < 760 ? 8 : 14);       // fewer callouts on a phone
  }
  if (g.selected && !ids.includes(g.selected)) ids = [g.selected, ...ids];
  return ids.map((id) => g.parts[id]).filter((p) => p && p.group.visible && !p.ghost && p.removal < 0.5);
}
const toScreen = (v, w, h) => { const p = v.clone().project(g.camera); return { x: (p.x * 0.5 + 0.5) * w, y: (-p.y * 0.5 + 0.5) * h, z: p.z }; };
function visibleBox() {
  const box = new THREE.Box3();
  for (const p of Object.values(g.parts)) if (p.group.visible && !p.ghost && p.removal < 0.5) box.expandByObject(p.group);
  return box;
}
let sheetKey = "";

function updateLabels() {
  const layer = $("#gLabels"), svg = $("#gLines"), canvas = $("#gCanvas");
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const parts = labelParts();
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const box = visibleBox();
  const centre = box.isEmpty() ? { x: w / 2, y: h / 2 } : toScreen(box.getCenter(new THREE.Vector3()), w, h);

  // callouts pushed outward from the model's centre, then nudged apart, like a drawing sheet
  const pts = [];
  parts.forEach((p, i) => {
    const a = toScreen(new THREE.Box3().setFromObject(p.group).getCenter(new THREE.Vector3()), w, h);
    if (a.z > 1) return;
    let dx = a.x - centre.x, dy = a.y - centre.y;
    const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
    if (Math.abs(dx) < 0.25) dx = dx < 0 ? -0.25 : 0.25;                   // keep callouts off the top/bottom centre
    pts.push({ p, n: i + 1, ax: a.x, ay: a.y, x: a.x + dx * (80 + 0.25 * len), y: a.y + dy * (60 + 0.2 * len), side: dx < 0 ? "left" : "right" });
  });
  for (const side of ["left", "right"]) {
    const list = pts.filter((q) => q.side === side).sort((a, b) => a.y - b.y);
    for (let i = 1; i < list.length; i++) if (list[i].y - list[i - 1].y < 24) list[i].y = list[i - 1].y + 24;
    for (const q of list) { q.y = Math.max(16, Math.min(h - 16, q.y)); q.x = Math.max(60, Math.min(w - 60, q.x)); }
  }
  // boxes on the sheet (schedule, elevations, title block, part card) that callouts must not cover
  const canvasRect = canvas.getBoundingClientRect();
  const obstacles = ["#gSchedule", "#gInsetSide", "#gInsetFront", "#gTitleBlock", "#gNote", ".g-legend", "#gDisclaimer", "#gAssetNote"]
    .flatMap((s) => [...document.querySelectorAll(s)])
    .filter((n) => !n.hidden && n.offsetParent !== null)
    .map((n) => { const r = n.getBoundingClientRect(); return { l: r.left - canvasRect.left - 6, t: r.top - canvasRect.top - 6, r: r.right - canvasRect.left + 6, b: r.bottom - canvasRect.top + 6 }; });

  let svgHtml = "";
  const want = new Set();
  for (const q of pts) {
    const [color, statusLabel] = statusOf(q.p.part);
    want.add(q.p.part.id);
    let tag = layer.querySelector(`[data-id="${CSS.escape(q.p.part.id)}"]`);
    if (!tag) {
      tag = el("button", "g-label"); tag.type = "button"; tag.dataset.id = q.p.part.id;
      tag.addEventListener("click", () => select(tag.dataset.id));
      layer.append(tag);
    }
    const key = `${q.n}|${q.p.part.name}|${color}|${q.side}`;
    if (tag.dataset.key !== key) {
      tag.dataset.key = key; tag.textContent = "";
      const num = el("span", "n", String(q.n)), dot = el("i"); dot.style.background = color; dot.title = statusLabel;
      const name = el("span", null, q.p.part.name);
      q.side === "left" ? tag.append(name, dot, num) : tag.append(num, dot, name);
    }
    tag.classList.toggle("on", g.selected === q.p.part.id);
    tag.classList.toggle("right", q.side === "right");
    const tagW = tag.offsetWidth || 120;
    // slide the callout towards the model until it's clear of every box on the sheet
    const span = (x) => (q.side === "left" ? [x - 18 - tagW, x] : [x, x + 18 + tagW]);
    const hits = (x, y) => {
      const [l, r] = span(x);
      return l < 6 || r > w - 6 || y < 12 || y > h - 12 ||
        obstacles.some((o) => l < o.r && r > o.l && y - 11 < o.b && y + 11 > o.t);
    };
    // keep the whole callout on the sheet
    const [l0, r0] = span(q.x);
    if (l0 < 6) q.x += 6 - l0;
    if (r0 > w - 6) q.x -= r0 - (w - 6);
    if (hits(q.x, q.y)) {
      // first slide towards the model, then try rows further and further above/below
      const baseX = q.x, baseY = q.y;
      let found = false;
      for (let k = 0; k <= 16 && !found; k++) {
        const dy = k === 0 ? 0 : (k % 2 ? 1 : -1) * 26 * Math.ceil(k / 2);
        for (let s = 0; s <= 10 && !found; s++) {
          const towards = Math.sign(q.ax - baseX) || (q.side === "left" ? 1 : -1);
          const x = baseX + towards * 12 * s, y = baseY + dy;
          if (!hits(x, y)) { q.x = x; q.y = y; found = true; }
        }
      }
    }
    const tick = q.side === "left" ? q.x - 14 : q.x + 14;
    tag.style.top = `${q.y - 11}px`;
    tag.style.left = `${q.side === "left" ? tick - 4 - tagW : tick + 4}px`;
    const [pl, pr] = span(q.x);
    obstacles.push({ l: pl - 4, r: pr + 4, t: q.y - 13, b: q.y + 13 });      // later callouts avoid this one
    const ink = g.selected === q.p.part.id ? INK_ACCENT : INK;
    svgHtml += `<polyline points="${q.ax},${q.ay} ${q.x},${q.y} ${tick},${q.y}" fill="none" stroke="${ink}" stroke-width="1.1" stroke-linecap="round"/>`;
    svgHtml += `<circle cx="${q.ax}" cy="${q.ay}" r="2.6" fill="${ink}"/>`;
  }
  svgHtml += dimensions(box, w, h);
  svg.innerHTML = svgHtml;
  for (const tag of [...layer.querySelectorAll(".g-label")]) if (!want.has(tag.dataset.id)) tag.remove();
  updateSheet(pts);
}

// dimension lines with arrowheads: only when assembled (EXPLODE near zero), like a
// general-arrangement drawing, so the numbers are the real assembled size
function dimensions(box, w, h) {
  if (g.step >= 0 || g.map.on || box.isEmpty() || !g.spec || g.explode > 0.08) return "";
  const size = box.getSize(new THREE.Vector3()), unit = g.spec.units || "mm";
  const a = toScreen(new THREE.Vector3(box.min.x, box.min.y, box.max.z), w, h);
  const b = toScreen(new THREE.Vector3(box.max.x, box.min.y, box.max.z), w, h);
  const c = toScreen(new THREE.Vector3(box.max.x, box.max.y, box.max.z), w, h);
  const line = (p, q, off, label) => {
    const dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1, nx = -dy / len * off, ny = dx / len * off;
    const P = { x: p.x + nx, y: p.y + ny }, Q = { x: q.x + nx, y: q.y + ny }, ux = dx / len * 9, uy = dy / len * 9;
    const head = (X, sgn) => `M${X.x},${X.y} l${sgn * ux - uy * 0.35},${sgn * uy + ux * 0.35} M${X.x},${X.y} l${sgn * ux + uy * 0.35},${sgn * uy - ux * 0.35}`;
    const mx = (P.x + Q.x) / 2 + nx * 0.6, my = (P.y + Q.y) / 2 + ny * 0.6, ang = Math.atan2(dy, dx) * 180 / Math.PI;
    const upright = ang > 90 || ang < -90 ? ang + 180 : ang;
    return `<path d="M${p.x},${p.y} L${P.x},${P.y} M${q.x},${q.y} L${Q.x},${Q.y}" stroke="${INK}" stroke-opacity=".45" stroke-width=".8" fill="none"/>` +
      `<path d="M${P.x},${P.y} L${Q.x},${Q.y} ${head(P, 1)} ${head(Q, -1)}" stroke="${INK}" stroke-width="1" fill="none"/>` +
      `<text x="${mx}" y="${my}" fill="${INK}" font-size="12" font-family="Segoe Print, Bradley Hand, cursive" text-anchor="middle" dominant-baseline="middle" transform="rotate(${upright} ${mx} ${my})">${label}</text>`;
  };
  const fmt = (v) => `≈ ${Math.round(v).toLocaleString("en-GB")} ${unit}`;
  return line(a, b, 30, fmt(size.x)) + line(b, c, -30, fmt(size.y));
}

// parts schedule (matches the numbered callouts) and title block
function updateSheet(pts) {
  if (!g.spec) return;
  const step = g.step >= 0 ? g.spec.steps[g.step] : null;
  const key = JSON.stringify([g.spec.title, g.step, pts.map((q) => [q.n, q.p.part.id, statusOf(q.p.part)[1]])]);
  if (key === sheetKey) return;
  sheetKey = key;
  const rows = $("#gScheduleRows"); rows.textContent = "";
  for (const q of [...pts].sort((a, b) => a.n - b.n)) {
    const [color, label] = statusOf(q.p.part), item = q.p.part.item && g.items[q.p.part.item];
    const tr = el("tr");
    const status = el("td", null, label.toLowerCase()); status.style.color = color;
    tr.append(el("td", null, String(q.n)), el("td", null, q.p.part.name), status, el("td", null, item && item.price ? "£" + item.price : ""));
    rows.append(tr);
  }
  $("#gSchedule").hidden = !pts.length;
  const tb = $("#gTitleBlock"); tb.textContent = "";
  const row = (a, b) => { const d = el("div"); d.append(el("span", null, a), el("span", null, b || "")); tb.append(d); };
  const title = el("div", null, "TITAN WORKS"); tb.append(title);
  row(g.spec.title);
  row(`DRG No. TW-${(g.spec.id || "x").toUpperCase().slice(0, 14)}`, "SCALE: NTS");
  row(step ? `STEP ${g.step + 1}/${g.spec.steps.length}: ${step.title}` : "GENERAL ARRANGEMENT", step ? (step.who === "mechanic" ? "MECHANIC" : "YOU") : "");
  row(`DRAWN: TITAN`, new Date().toLocaleDateString("en-GB"));
}

// line-only side and front elevations, drawn into the boxes on the sheet
function renderInsets() {
  if (g.map.on || !g.root) return;
  const box = visibleBox();
  if (box.isEmpty()) return;
  const canvasRect = $("#gCanvas").getBoundingClientRect();
  const centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  for (const [id, cam, dir, spanW] of [["#gInsetSide", g.ortho.side, new THREE.Vector3(1, 0, 0), size.z],
                                        ["#gInsetFront", g.ortho.front, new THREE.Vector3(0, 0, 1), size.x]]) {
    const node = $(id);
    if (!node || node.offsetParent === null) continue;
    const r = node.getBoundingClientRect();
    const x = r.left - canvasRect.left, y = canvasRect.bottom - r.bottom, wv = r.width, hv = r.height - 16;
    if (wv < 20 || hv < 20) continue;
    const aspect = wv / hv;
    let halfW = spanW * 0.58, halfH = size.y * 0.58;
    if (halfW / halfH < aspect) halfW = halfH * aspect; else halfH = halfW / aspect;
    Object.assign(cam, { left: -halfW, right: halfW, top: halfH, bottom: -halfH, near: 1, far: Math.max(size.x, size.z) * 10 });
    cam.position.copy(centre).addScaledVector(dir, Math.max(size.x, size.z) * 3);
    cam.up.set(0, 1, 0); cam.lookAt(centre); cam.updateProjectionMatrix();
    g.renderer.setViewport(x, y + 16, wv, hv); g.renderer.setScissor(x, y + 16, wv, hv); g.renderer.setScissorTest(true);
    g.renderer.render(g.scene, cam);
  }
  g.renderer.setScissorTest(false);
  const c = $("#gCanvas"); g.renderer.setViewport(0, 0, c.clientWidth, c.clientHeight);
}

// ---------- render loop ----------
function loop() {
  if (!g.open) return;
  const dt = Math.min(0.05, g.clock.getDelta());
  animate(dt); updateFlows(dt); g.controls.update();
  g.renderer.clear();
  g.renderer.render(g.scene, g.camera);
  renderInsets();
  updateLabels();
  g.raf = requestAnimationFrame(loop);
}

// ---------- picking ----------
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
let downAt = null;
document.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; });
function pick(e) {
  if (downAt && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 6) return;
  const r = e.target.getBoundingClientRect();
  ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ptr, g.camera);
  const candidates = g.map.on ? g.assetMeshes : Object.values(g.parts).filter((p) => p.group.visible).flatMap((p) => p.meshes);
  const hit = ray.intersectObjects(candidates, false).find((h) => [].concat(h.object.material)[0].opacity > 0.3 || g.map.on);
  if (g.map.on) { if (hit) toggleMapping(hit.object); return; }
  select(hit ? hit.object.userData.partId : null);
}
function select(id) {
  g.selected = g.selected === id ? null : id;
  applyFocus();
  for (const row of document.querySelectorAll("#gParts .g-row")) row.classList.toggle("on", row.dataset.id === g.selected);
  const note = $("#gNote");
  if (!g.selected) { note.hidden = true; return; }
  const part = g.parts[g.selected].part, item = part.item && g.items[part.item], [color, label] = statusOf(part);
  note.textContent = "";
  note.append(el("b", null, part.name));
  const badge = el("span", "g-badge", label); badge.style.borderColor = color; badge.style.color = color; note.append(badge);
  if (item && item.price) note.append(el("span", "g-price", "£" + item.price));
  if (item && item.who === "mechanic") note.append(el("span", "g-badge", "MECHANIC"));
  for (const t of [part.info, item && item.notes].filter(Boolean)) note.append(el("p", null, t));
  note.hidden = false;
}

// ---------- MAP PARTS (link a real model's meshes to the guide's parts) ----------
function startMap() {
  if (!g.spec.asset) { $("#gAssetNote").textContent = "Add a 3D file first, then map its parts."; return; }
  g.map.on = true;
  g.map.edits = { ...(g.spec.mesh_map || {}) };
  g.map.rotate = [...(g.spec.asset_rotate || [0, 0, 0])]; g.map.scale = g.spec.asset_scale || 1;
  g.map.part = g.spec.parts[0].id;
  g.map.savedExplode = g.explode; g.explode = 0;           // outlines line up with the assembled model
  g.step = -1;
  $("#garage").classList.add("mapping");
  build(g.spec).then(() => { renderMap(); });
}
function stopMap(save) {
  const finish = async () => {
    if (save) {
      const r = await window.titanApi("/api/garage/override", { model: g.spec.id, mesh_map: g.map.edits, rotate: g.map.rotate, scale: g.map.scale });
      if (!r.ok) { $("#gAssetNote").textContent = r.data.error || "Couldn't save the mapping."; return; }
    }
    g.map.on = false; g.map.edits = null;
    g.explode = g.map.savedExplode ?? 0.6;
    $("#garage").classList.remove("mapping");
    await show(g.spec.id);
  };
  finish();
}
function toggleMapping(mesh) {
  const key = g.meshKey.get(mesh);
  if (!key || !g.map.part) return;
  if (g.map.edits[key] === g.map.part) delete g.map.edits[key]; else g.map.edits[key] = g.map.part;
  paintMap(); renderMap();
}
function paintMap() {
  for (const mesh of g.assetMeshes) {
    const owner = g.map.edits[g.meshKey.get(mesh)];
    forMaterials(mesh, (m) => {
      m.userData.baseOpacity ??= m.opacity;
      if (m.emissive) { m.emissive.set(owner === g.map.part ? "#ff3b30" : owner ? "#3a2a08" : "#000000"); m.emissiveIntensity = owner === g.map.part ? 0.7 : 1; }
    });
  }
}
function renderMap() {
  paintMap(); applyFocus();
  const box = $("#gMap"); box.textContent = "";
  box.append(el("p", "mini", "Pick a part, then tap the matching pieces on the model (tap again to unlink). The faint outline shows roughly where the part should be."));
  const tools = el("div", "g-nav");
  const rot = (axis) => { const b = el("button", "pill", `TURN ${"XYZ"[axis]} 90°`); b.onclick = () => { g.map.rotate[axis] = (g.map.rotate[axis] + 90) % 360; build(g.spec).then(renderMap); }; return b; };
  const bigger = el("button", "pill", "BIGGER"), smaller = el("button", "pill", "SMALLER");
  bigger.onclick = () => { g.map.scale *= 1.25; build(g.spec).then(renderMap); };
  smaller.onclick = () => { g.map.scale /= 1.25; build(g.spec).then(renderMap); };
  tools.append(rot(0), rot(1), rot(2), bigger, smaller);
  box.append(tools);
  const counts = {};
  for (const id of Object.values(g.map.edits)) counts[id] = (counts[id] || 0) + 1;
  for (const part of g.spec.parts) {
    const row = el("button", "g-row" + (g.map.part === part.id ? " on" : "")); row.type = "button";
    const dot = el("i", "g-dot"); dot.style.background = counts[part.id] ? "#45f0a0" : "#5e3633";
    row.append(dot, el("span", "g-name", part.name), el("span", "g-meta", counts[part.id] ? `${counts[part.id]} PIECE${counts[part.id] > 1 ? "S" : ""}` : "—"));
    row.onclick = () => { g.map.part = part.id; renderMap(); };
    box.append(row);
  }
  const done = el("div", "g-nav");
  const save = el("button", "pill active", "SAVE MAPPING"), cancel = el("button", "pill", "CANCEL");
  save.onclick = () => stopMap(true); cancel.onclick = () => stopMap(false);
  done.append(save, cancel); box.append(done);
}

// ---------- side panel ----------
function renderParts() {
  const box = $("#gParts"); box.textContent = "";
  for (const part of g.spec.parts) {
    const [color, label] = statusOf(part), item = part.item && g.items[part.item];
    const row = el("button", "g-row"); row.type = "button"; row.dataset.id = part.id;
    const dot = el("i", "g-dot"); dot.style.background = color; dot.style.boxShadow = `0 0 8px ${color}`;
    const meta = el("span", "g-meta", (item && item.price ? "£" + item.price + " · " : "") + label); meta.style.color = color;
    row.append(dot, el("span", "g-name", part.name), meta);
    row.addEventListener("click", () => select(part.id));
    box.append(row);
  }
}
const checkKey = (i, j) => `titan.check.${g.spec.title}.${i}.${j}`;
function renderSteps() {
  const box = $("#gSteps"); box.textContent = "";
  const steps = g.spec.steps || [];
  const nav = el("div", "g-nav");
  const prev = el("button", "pill", "◀ PREV"), next = el("button", "pill", "NEXT ▶"), all = el("button", "pill", "OVERVIEW");
  prev.onclick = () => setStep(Math.max(0, g.step - 1)); next.onclick = () => setStep(Math.min(steps.length - 1, g.step + 1));
  all.onclick = () => setStep(-1);
  nav.append(prev, el("span", "g-count", g.step >= 0 ? `STEP ${g.step + 1} / ${steps.length}` : `${steps.length} STEPS`), next, all);
  box.append(nav);
  steps.forEach((s, i) => {
    const card = el("div", "g-step" + (i === g.step ? " on" : ""));
    const head = el("div", "g-step-h");
    head.append(el("b", null, `${i + 1}. ${s.title}`));
    const who = el("span", "g-badge", s.who === "mechanic" ? "MECHANIC" : "YOU");
    if (s.who === "mechanic") { who.style.color = "#ffb547"; who.style.borderColor = "#ffb547"; }
    head.append(who); card.append(head);
    head.addEventListener("click", () => setStep(i));
    if (i === g.step || g.step < 0) {
      card.append(el("p", null, s.text));
      (s.checks || []).forEach((c, j) => {
        const lab = el("label", "g-check"), cb = el("input"); cb.type = "checkbox";
        try { cb.checked = localStorage.getItem(checkKey(i, j)) === "1"; } catch (e) { /* ignore */ }
        cb.onchange = () => { try { localStorage.setItem(checkKey(i, j), cb.checked ? "1" : "0"); } catch (e) { /* ignore */ } };
        lab.append(cb, el("span", null, c)); card.append(lab);
      });
    }
    box.append(card);
  });
  if (g.step >= 0) box.querySelector(".g-step.on")?.scrollIntoView({ block: "nearest" });
}
function renderProject() {
  const box = $("#gProject"); box.textContent = "";
  const p = g.project; if (!p) return;
  box.append(el("div", "g-title", p.title));
  let spent = 0, considering = 0;
  const totals = el("div", "g-totals"); box.append(totals);
  const moves = el("div", "g-moves"); moves.append(el("div", "sub-h", "NEXT MOVES"));
  (p.next_moves || []).forEach((m, i) => {
    const row = el("div", "g-move"); row.append(el("b", null, `${i + 1}. ${m.title}`));
    row.append(el("span", "g-badge", m.who === "mechanic" ? "MECHANIC" : "YOU"), el("p", null, m.detail)); moves.append(row);
  });
  box.append(moves);
  for (const status of Object.keys(STATUS)) {
    const items = p.items.filter((it) => it.status === status);
    if (!items.length) continue;
    const [color, label] = STATUS[status];
    const h = el("div", "sub-h", label); h.style.color = color; box.append(h);
    for (const it of items) {
      const row = el("div", "g-item"); row.append(el("span", null, it.name), el("span", "g-price", it.price ? "£" + it.price : "")); box.append(row);
      if (["owned", "ordered", "fitted"].includes(status)) spent += it.price || 0;
      if (["considering", "needed"].includes(status)) considering += it.price || 0;
    }
  }
  totals.append(el("span", null, `SPENT £${spent}`), el("span", null, `CONSIDERING £${considering}`));
}
function renderAssets() {
  const sel = $("#gAsset"); sel.textContent = "";
  sel.append(Object.assign(el("option", null, "Schematic shapes"), { value: "" }));
  for (const a of g.assets) sel.append(Object.assign(el("option", null, `${a.name} (${a.mb} MB)`), { value: a.name }));
  sel.value = g.spec?.asset || "";
  $("#gMapBtn").hidden = !g.spec?.asset;
}

// ---------- open / close / files / export ----------
async function loadList(selectId) {
  const r = await window.titanApi("/api/garage");
  if (!r.ok) return false;
  g.project = r.data.project; g.assets = r.data.assets || [];
  g.items = Object.fromEntries((g.project.items || []).map((it) => [it.id, it]));
  const sel = $("#gModel"); sel.textContent = "";
  for (const m of r.data.models) { const o = el("option", null, (m.kind === "custom" ? "✎ " : "") + m.title); o.value = m.id; sel.append(o); }
  if (selectId) sel.value = selectId;
  return true;
}
async function show(modelId) {
  const r = await window.titanApi("/api/garage/model?id=" + encodeURIComponent(modelId));
  if (!r.ok) { $("#gSub").textContent = "Couldn't load that model."; return; }
  if (g.playTimer) play();
  g.spec = r.data; g.step = -1; g.selected = null;
  $("#gSub").textContent = g.spec.subtitle || "";
  $("#gDisclaimer").textContent = g.spec.disclaimer || "";
  $("#gExport").hidden = !g.spec.printable;
  $("#gNote").hidden = true;
  renderAssets(); renderParts(); renderSteps(); renderProject();
  await build(g.spec);
}
async function open(modelId) {
  ensureRenderer();
  $("#garage").hidden = false; g.open = true;
  document.body.classList.add("garage-open");
  if (!(await loadList(modelId))) { $("#gSub").textContent = "Sign in to open the garage."; return; }
  resize();
  cancelAnimationFrame(g.raf); g.clock.getDelta(); loop();
  await show(modelId || $("#gModel").value);
}
function close() {
  if (g.playTimer) play();
  g.open = false; cancelAnimationFrame(g.raf);
  $("#garage").hidden = true; document.body.classList.remove("garage-open");
}
async function useAsset(name) {
  const r = await window.titanApi("/api/garage/override", { model: g.spec.id, asset: name || null });
  if (!r.ok) { $("#gAssetNote").textContent = r.data.error || "Couldn't use that file."; return; }
  await loadList(g.spec.id); await show(g.spec.id);
}
async function upload(file) {
  if (!file) return;
  $("#gAssetNote").textContent = `Uploading ${file.name} (${(file.size / 1024 ** 2).toFixed(1)} MB)…`;
  const res = await fetch("/api/garage/upload?name=" + encodeURIComponent(file.name), {
    method: "POST", headers: { "X-Titan-Token": window.titanToken(), "Content-Type": "application/octet-stream" }, body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { $("#gAssetNote").textContent = data.error || "Upload failed."; return; }
  await useAsset(data.name);
}
function exportStl() {
  if (!g.spec || !g.spec.printable) return;
  const solid = new THREE.Group();
  for (const p of Object.values(g.parts)) for (const m of p.meshes) {
    const c = new THREE.Mesh(m.geometry); c.position.copy(p.base); c.rotation.copy(m.rotation); solid.add(c);
  }
  solid.updateMatrixWorld(true);
  const data = new STLExporter().parse(solid, { binary: true });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([data], { type: "model/stl" }));
  a.download = (g.spec.title || "titan-part").replace(/[^a-z0-9]+/gi, "-").toLowerCase() + ".stl";
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ---------- wiring ----------
$("#gClose").addEventListener("click", close);
$("#gModel").addEventListener("change", (e) => show(e.target.value));
$("#gExplode").addEventListener("input", (e) => { g.explode = e.target.value / 100; if (g.step < 0) placeParts(); });
$("#gCut").addEventListener("input", (e) => { g.cut = e.target.value / 100; applyClip(); });
$("#gExport").addEventListener("click", exportStl);
$("#gFit").addEventListener("click", () => frameCamera(false));
$("#gPlay").addEventListener("click", play);
$("#gLabelsBtn").addEventListener("click", (e) => {
  g.labels = { auto: "all", all: "off", off: "auto" }[g.labels];
  e.currentTarget.textContent = { auto: "LABELS: KEY", all: "LABELS: ALL", off: "LABELS: OFF" }[g.labels];
});
$("#gFlows").addEventListener("click", (e) => { g.flowsOn = !g.flowsOn; e.currentTarget.classList.toggle("active", g.flowsOn); });
$("#gAsset").addEventListener("change", (e) => useAsset(e.target.value));
$("#gAddFile").addEventListener("click", () => $("#gFile").click());
$("#gFile").addEventListener("change", (e) => { upload(e.target.files[0]); e.target.value = ""; });
$("#gMapBtn").addEventListener("click", () => (g.map.on ? stopMap(false) : startMap()));
const view = $(".g-view");
view.addEventListener("dragover", (e) => { e.preventDefault(); view.classList.add("drop"); });
view.addEventListener("dragleave", () => view.classList.remove("drop"));
view.addEventListener("drop", (e) => { e.preventDefault(); view.classList.remove("drop"); upload(e.dataTransfer.files[0]); });
for (const b of document.querySelectorAll(".g-tabs .pill")) {
  b.addEventListener("click", () => {
    for (const x of document.querySelectorAll(".g-tabs .pill")) x.classList.toggle("active", x === b);
    for (const id of ["gParts", "gSteps", "gProject"]) $("#" + id).hidden = id !== "g" + b.dataset.tab;
  });
}
window.addEventListener("keydown", (e) => {
  if (!g.open) return;
  if (e.key === "Escape") close();
  if (e.key === "ArrowRight" && g.spec?.steps?.length) setStep(Math.min(g.spec.steps.length - 1, g.step + 1));
  if (e.key === "ArrowLeft" && g.spec?.steps?.length) setStep(Math.max(0, g.step - 1));
});
window.TitanGarage = { open, close };
