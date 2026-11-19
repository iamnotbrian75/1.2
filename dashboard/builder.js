// =============================================================================
// TITAN BUILDER — blueprints you can follow (loaded by index.html)
// -----------------------------------------------------------------------------
// Ask "how do I wire a boost gauge?" and Titan drafts a guide; this file draws
// every part as a white-line blueprint symbol on blueprint paper, then a
// pointer flies from part to part while Titan reads each step aloud.
// No 3D library: plain SVG, light enough for a phone over Tailscale.
// Only the STL export (printable parts) loads three.js, on demand.
// Text from guides (including AI drafts) is only ever set with textContent.
// =============================================================================
const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const sv = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
  if (parent) parent.append(e);
  return e;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

// status colours chosen to read on blueprint blue
const STATUS = {
  owned: ["#7dffb4", "OWNED"], considering: ["#ffd36e", "CONSIDERING"], check: ["#ff8f86", "CHECK"],
  ordered: ["#a9d1ff", "ORDERED"], fitted: ["#ffffff", "DONE"], needed: ["#ffb072", "NEEDED"],
};
const CELL_W = 236, CELL_H = 152, BOX_W = 150, BOX_H = 74, MARGIN = 70, ZONE_H = 44;
const TABLE_W = 360, ROW_H = 21;
const MAX_ROWS_AUTO = 4;

const B = {
  open: false, guide: null, guides: [], project: null, items: {},
  step: -1, playing: false, playGen: 0, pollTimer: 0, layout: null,
  vb: null, sheet: null, pointer: { x: 0, y: 0, shown: false, gen: 0 }, autoplay: false,
};

// ---------------------------------------------------------------------------
// 1. LAYOUT: grid positions from the guide, or worked out from the links
// ---------------------------------------------------------------------------
function autoLayout(guide) {
  const parts = guide.parts, ids = parts.map((p) => p.id);
  const order = [];
  for (const s of guide.steps || []) for (const id of s.parts || []) if (!order.includes(id)) order.push(id);
  for (const id of ids) if (!order.includes(id)) order.push(id);
  const nbr = Object.fromEntries(ids.map((id) => [id, []]));
  for (const l of guide.links || []) if (nbr[l.a] && nbr[l.b]) { nbr[l.a].push(l.b); nbr[l.b].push(l.a); }
  const layer = {};
  let base = 0;
  for (const start of order) {                 // BFS from each unplaced part, in step order
    if (start in layer) continue;
    layer[start] = base; const queue = [start]; let deepest = base;
    while (queue.length) {
      const cur = queue.shift();
      for (const n of nbr[cur]) if (!(n in layer)) { layer[n] = layer[cur] + 1; deepest = Math.max(deepest, layer[n]); queue.push(n); }
    }
    base = deepest + 1;
  }
  const columns = [];
  for (const id of order) {
    let col = layer[id];
    while (true) {
      columns[col] = columns[col] || [];
      if (columns[col].length < MAX_ROWS_AUTO) { columns[col].push(id); break; }
      col += 0.5;                               // spill into a half-step column; compacted below
    }
  }
  const keys = Object.keys(columns).map(Number).sort((a, b) => a - b);
  const at = {};
  keys.forEach((k, ci) => columns[k].forEach((id, ri) => { at[id] = [ci, ri]; }));
  return at;
}

function layout(guide) {
  const explicit = guide.parts.every((p) => Array.isArray(p.at));
  const at = explicit ? Object.fromEntries(guide.parts.map((p) => [p.id, p.at])) : autoLayout(guide);
  const cols = Math.max(...Object.values(at).map((a) => a[0])) + 1;
  const rows = Math.max(...Object.values(at).map((a) => a[1])) + 1;
  const top = MARGIN + (guide.zones ? ZONE_H : 10) + 40;
  const boxes = {};
  for (const p of guide.parts) {
    const [c, r] = at[p.id];
    const w = ["panel", "bar", "cooler"].includes(p.kind) ? BOX_W + 20 : BOX_W;
    const cx = MARGIN + 40 + c * CELL_W + CELL_W / 2, cy = top + r * CELL_H + CELL_H / 2 - 12;
    boxes[p.id] = { x: cx - w / 2, y: cy - BOX_H / 2, w, h: BOX_H, cx, cy, col: c, row: r };
  }
  const drawW = cols * CELL_W + 80, drawH = rows * CELL_H + (top - MARGIN);
  const tableX = MARGIN + drawW + 30;
  const tableH = 34 + ROW_H * (guide.parts.length + 1);
  const notesH = 44 + 15 * noteLines(guide).length;
  const titleH = 190;
  const width = tableX + TABLE_W + MARGIN;
  const height = Math.max(drawH + MARGIN, MARGIN + tableH + 20 + notesH + 24 + titleH) + MARGIN;
  return { at, boxes, cols, rows, top, drawW, drawH, tableX, tableH, notesH, titleH, width, height };
}

// ---------------------------------------------------------------------------
// 2. BLUEPRINT SYMBOLS — one small line drawing per kind of part
// ---------------------------------------------------------------------------
function symbol(g, kind, w, h) {
  const L = (attrs) => sv(attrs.d ? "path" : attrs.r != null ? "circle" : attrs.points ? "polyline" : attrs.x2 != null ? "line" : "rect",
    { ...attrs, pathLength: 1, class: (attrs.class || "") + " ink" }, g);
  const cx = w / 2, cy = h / 2;
  const ticks = (x0, x1, y0, y1, n, vertical) => { for (let i = 1; i < n; i++) { const t = i / n; vertical ? L({ x1: x0 + (x1 - x0) * t, y1: y0, x2: x0 + (x1 - x0) * t, y2: y1, class: "thin" }) : L({ x1: x0, y1: y0 + (y1 - y0) * t, x2: x1, y2: y0 + (y1 - y0) * t, class: "thin" }); } };
  const hole = (x, y, r = 4) => { L({ cx: x, cy: y, r }); L({ x1: x - r - 3, y1: y, x2: x + r + 3, y2: y, class: "thin" }); L({ x1: x, y1: y - r - 3, x2: x, y2: y + r + 3, class: "thin" }); };
  switch (kind) {
    case "panel":
      L({ d: `M6 ${h - 8} Q6 8 ${w * 0.35} 8 L${w - 14} 8 Q${w - 4} 8 ${w - 4} ${h * 0.45} L${w - 10} ${h - 8} Z` });
      L({ d: `M18 ${h - 18} Q20 20 ${w * 0.38} 20 L${w - 22} 20`, class: "thin" }); break;
    case "grille":
      L({ x: 6, y: 6, width: w - 12, height: h - 12, rx: 10 }); ticks(14, w - 14, 10, h - 10, 9, true); break;
    case "bracket":
      L({ d: `M10 8 L30 8 L30 ${h - 26} L${w - 10} ${h - 26} L${w - 10} ${h - 8} L10 ${h - 8} Z` });
      hole(20, 22, 3.5); hole(w - 40, h - 17, 3.5); hole(w - 70, h - 17, 3.5); break;
    case "bar":
      L({ x: 4, y: cy - 14, width: w - 8, height: 28, rx: 3 }); L({ x1: 8, y1: cy, x2: w - 8, y2: cy, class: "thin dash" });
      for (let i = 0; i < 4; i++) hole(22 + i * (w - 44) / 3, cy, 5); break;
    case "cooler":
      L({ x: 16, y: 6, width: w - 32, height: h - 12 }); L({ x: 4, y: 10, width: 12, height: h - 20, rx: 3 }); L({ x: w - 16, y: 10, width: 12, height: h - 20, rx: 3 });
      ticks(16, w - 16, 7, h - 7, 16, true); break;
    case "fan":
      L({ cx, cy, r: h / 2 - 4 }); L({ cx, cy, r: 7 });
      for (let i = 0; i < 5; i++) { const a = i * 72 * Math.PI / 180, r = h / 2 - 8; L({ d: `M${cx + 7 * Math.cos(a)} ${cy + 7 * Math.sin(a)} Q${cx + r * Math.cos(a + 0.9)} ${cy + r * Math.sin(a + 0.9)} ${cx + r * Math.cos(a + 0.35)} ${cy + r * Math.sin(a + 0.35)}` }); }
      L({ x: cx - h / 2 - 8, y: 2, width: h + 16, height: h - 4, class: "thin dash" }); break;
    case "pipe": case "hose":
      L({ d: `M6 ${cy - 8} C${w * 0.35} ${cy - 30} ${w * 0.6} ${cy + 14} ${w - 6} ${cy - 8}` });
      L({ d: `M6 ${cy + 8} C${w * 0.35} ${cy - 14} ${w * 0.6} ${cy + 30} ${w - 6} ${cy + 8}` });
      L({ x1: 6, y1: cy - 8, x2: 6, y2: cy + 8 }); L({ x1: w - 6, y1: cy - 8, x2: w - 6, y2: cy + 8 });
      if (kind === "hose") for (let i = 1; i < 6; i++) { const x = 6 + i * (w - 12) / 6; L({ x1: x, y1: cy - 15, x2: x, y2: cy + 15, class: "thin" }); }
      else { L({ x: 14, y: cy - 13, width: 6, height: 24, class: "thin" }); L({ x: w - 22, y: cy - 13, width: 6, height: 24, class: "thin" }); }
      break;
    case "filter":
      L({ d: `M10 ${cy - 12} L10 ${cy + 12} L${w * 0.35} ${cy + 12} L${w - 10} ${cy + 26} L${w - 10} ${cy - 26} L${w * 0.35} ${cy - 12} Z` });
      for (let i = 1; i < 7; i++) { const x = w * 0.35 + i * (w * 0.65 - 10) / 7; const s = 12 + 14 * i / 7; L({ x1: x, y1: cy - s, x2: x, y2: cy + s, class: "thin" }); } break;
    case "light":
      L({ d: `M6 12 L${w - 10} 6 Q${w - 2} ${cy} ${w - 10} ${h - 6} L6 ${h - 12} Z` });
      L({ cx: w * 0.32, cy, r: 16 }); L({ cx: w * 0.62, cy, r: 13 }); L({ cx: w * 0.32, cy, r: 6, class: "thin" }); L({ cx: w * 0.62, cy, r: 5, class: "thin" }); break;
    case "latch":
      L({ x: 10, y: 10, width: w * 0.45, height: h - 20, rx: 4 }); L({ d: `M${w * 0.45 + 10} ${cy} L${w - 30} ${cy} Q${w - 12} ${cy} ${w - 12} ${cy + 14}` });
      L({ d: `M${w * 0.2} ${cy + 4} q8 -14 16 0`, class: "thin" }); hole(22, 22, 3); break;
    case "screen":
      L({ x: 6, y: 6, width: w - 12, height: h - 12, rx: 6 }); L({ x: 16, y: 13, width: w - 32, height: h - 30, class: "thin" });
      L({ cx: cx, cy: h - 11, r: 3, class: "thin" }); break;
    case "board":
      L({ x: 6, y: 8, width: w - 12, height: h - 16, rx: 3 }); L({ x: cx - 18, y: cy - 14, width: 36, height: 28 });
      for (let i = 0; i < 10; i++) { L({ x1: 16 + i * (w - 32) / 9, y1: 8, x2: 16 + i * (w - 32) / 9, y2: 14, class: "thin" }); }
      hole(16, h - 18, 3); hole(w - 16, h - 18, 3); break;
    case "module": case "box":
      L({ x: 8, y: 8, width: w - 16, height: h - 16, rx: 4 }); L({ x: 18, y: 18, width: w - 36, height: h - 36, class: "thin dash" });
      if (kind === "module") for (let i = 0; i < 6; i++) L({ x: w - 8, y: 16 + i * (h - 32) / 5 - 2, width: 6, height: 4, class: "thin" });
      break;
    case "connector":
      L({ x: 10, y: cy - 16, width: w * 0.45, height: 32, rx: 4 }); for (let i = 0; i < 4; i++) L({ x1: w * 0.45 + 10, y1: cy - 10 + i * 7, x2: w * 0.45 + 24, y2: cy - 10 + i * 7 });
      L({ d: `M10 ${cy} C-4 ${cy} 0 ${h - 4} ${w * 0.6} ${h - 6} L${w - 8} ${h - 6}`, class: "thin" }); break;
    case "cable": case "wire":
      L({ d: `M8 ${cy} C${w * 0.25} ${cy - 30} ${w * 0.45} ${cy + 30} ${w * 0.65} ${cy} S${w - 20} ${cy - 20} ${w - 10} ${cy}` });
      L({ x: 2, y: cy - 6, width: 10, height: 12, class: "thin" }); L({ x: w - 12, y: cy - 6, width: 10, height: 12, class: "thin" }); break;
    case "sensor":
      L({ x: 10, y: cy - 12, width: w * 0.35, height: 24, rx: 4 }); L({ d: `M${w * 0.35 + 10} ${cy - 6} L${w * 0.55} ${cy - 6} L${w * 0.55} ${cy + 6} L${w * 0.35 + 10} ${cy + 6}` });
      ticks(w * 0.35 + 10, w * 0.55, cy - 6, cy + 6, 4, true); L({ d: `M10 ${cy} C-2 ${cy} 4 ${h - 6} ${w * 0.4} ${h - 8}`, class: "thin" }); break;
    case "gauge":
      L({ cx, cy, r: h / 2 - 4 }); L({ cx, cy, r: h / 2 - 10, class: "thin" });
      for (let i = 0; i <= 8; i++) { const a = (135 + i * 33.75) * Math.PI / 180, r1 = h / 2 - 10, r2 = h / 2 - 16; L({ x1: cx + r1 * Math.cos(a), y1: cy + r1 * Math.sin(a), x2: cx + r2 * Math.cos(a), y2: cy + r2 * Math.sin(a), class: "thin" }); }
      L({ x1: cx, y1: cy, x2: cx + 16, y2: cy - 14 }); L({ cx, cy, r: 3 }); break;
    case "fuse":
      L({ x: cx - 30, y: cy - 14, width: 60, height: 28, rx: 8 }); L({ x1: cx - 46, y1: cy, x2: cx - 30, y2: cy }); L({ x1: cx + 30, y1: cy, x2: cx + 46, y2: cy });
      L({ d: `M${cx - 22} ${cy} q11 -12 22 0 t22 0`, class: "thin" }); break;
    case "switch":
      L({ x: 12, y: cy - 4, width: w - 24, height: h / 2 - 2, rx: 4 }); L({ x1: cx, y1: cy - 4, x2: cx + 22, y2: 8 }); L({ cx: cx + 22, cy: 8, r: 4 }); break;
    case "relay":
      L({ x: 12, y: 8, width: w - 24, height: h - 16, rx: 3 }); L({ d: `M24 ${cy} q6 -10 12 0 q6 10 12 0 q6 -10 12 0`, class: "thin" }); L({ x1: w * 0.6, y1: cy + 8, x2: w * 0.78, y2: cy - 8 }); break;
    case "battery":
      L({ x: 10, y: 14, width: w - 20, height: h - 22, rx: 3 }); L({ x: 24, y: 6, width: 18, height: 8 }); L({ x: w - 42, y: 6, width: 18, height: 8 });
      L({ x1: 27, y1: cy, x2: 39, y2: cy, class: "thin" }); L({ x1: 33, y1: cy - 6, x2: 33, y2: cy + 6, class: "thin" }); L({ x1: w - 39, y1: cy, x2: w - 27, y2: cy, class: "thin" }); break;
    case "bolt":
      L({ d: `M14 ${cy - 14} L26 ${cy - 14} L32 ${cy} L26 ${cy + 14} L14 ${cy + 14} L8 ${cy} Z` }); L({ x: 32, y: cy - 7, width: w - 50, height: 14 });
      for (let i = 0; i < 9; i++) L({ x1: 38 + i * (w - 60) / 9, y1: cy - 7, x2: 44 + i * (w - 60) / 9, y2: cy + 7, class: "thin" }); break;
    case "printed":
      L({ x: 8, y: 8, width: w - 16, height: h - 16, rx: 6 });
      for (let i = -h; i < w; i += 12) L({ x1: Math.max(8, i), y1: Math.max(8, Math.min(h - 8, h - 8 - (Math.max(8, i) - i))), x2: Math.min(w - 8, i + h - 16), y2: Math.max(8, h - 8 - (Math.min(w - 8, i + h - 16) - i)), class: "thin faint" });
      break;
    case "tool":
      L({ d: `M10 ${cy + 6} L${w - 40} ${cy + 6} L${w - 40} ${cy - 6} L10 ${cy - 6} Z` }); L({ d: `M${w - 40} ${cy - 14} L${w - 10} ${cy - 14} L${w - 18} ${cy} L${w - 10} ${cy + 14} L${w - 40} ${cy + 14} Z` }); break;
    default:
      L({ x: 8, y: 8, width: w - 16, height: h - 16, rx: 4 });
  }
}

// ---------------------------------------------------------------------------
// 3. DRAWING THE SHEET
// ---------------------------------------------------------------------------
function wrap(text, max) {
  const words = String(text || "").toUpperCase().split(/\s+/), lines = [""];
  for (const w of words) {
    if ((lines[lines.length - 1] + " " + w).trim().length > max && lines[lines.length - 1]) lines.push(w);
    else lines[lines.length - 1] = (lines[lines.length - 1] + " " + w).trim();
  }
  if (lines.length > 2) { lines.length = 2; lines[1] = lines[1].slice(0, max - 1) + "…"; }
  return lines;
}
function textAt(parent, x, y, str, cls, anchor) {
  const t = sv("text", { x, y, class: cls || "lab", "text-anchor": anchor || "start" }, parent);
  t.textContent = str; return t;
}
function statusOf(part) {
  const item = part.item && B.items[part.item];
  return item ? STATUS[item.status] || null : null;
}
function anchorPoint(box, toward) {
  const dx = toward.cx - box.cx, dy = toward.cy - box.cy;
  if (Math.abs(dx) * box.h > Math.abs(dy) * box.w) return { x: dx > 0 ? box.x + box.w : box.x, y: box.cy, side: dx > 0 ? "r" : "l" };
  return { x: box.cx, y: dy > 0 ? box.y + box.h : box.y, side: dy > 0 ? "b" : "t" };
}
function linkPath(a, b, lane) {
  if (a.col === b.col) {                          // same column: a bracket out to the left
    const x = a.x - 22 - lane * 6;
    return { d: `M${a.x} ${a.cy} H${x} V${b.cy} H${b.x}`, mid: { x, y: (a.cy + b.cy) / 2 } };
  }
  const [l, r] = a.cx < b.cx ? [a, b] : [b, a];
  const x1 = l.x + l.w, x2 = r.x, gx = x1 + (CELL_W - l.w) / 2 + ((lane % 5) - 2) * 6;
  if (Math.abs(l.col - r.col) === 1 || l.cy === r.cy) return { d: `M${x1} ${l.cy} H${gx} V${r.cy} H${x2}`, mid: { x: gx, y: (l.cy + r.cy) / 2 } };
  const gy = Math.min(l.y, r.y) - 18 - (lane % 4) * 5;   // skip columns: go over the top
  return { d: `M${x1} ${l.cy} H${gx} V${gy} H${x2 - 16} V${r.cy} H${x2}`, mid: { x: (gx + x2) / 2, y: gy } };
}

function paper(svg, defs, W, H) {
  // blueprint grid: fine 20-unit squares, stronger every 100
  const pat = sv("pattern", { id: "bpGrid", width: 100, height: 100, patternUnits: "userSpaceOnUse" }, defs);
  for (let i = 20; i < 100; i += 20) {
    sv("line", { x1: i, y1: 0, x2: i, y2: 100, stroke: "rgba(234,244,255,.07)", "stroke-width": 0.8 }, pat);
    sv("line", { x1: 0, y1: i, x2: 100, y2: i, stroke: "rgba(234,244,255,.07)", "stroke-width": 0.8 }, pat);
  }
  sv("path", { d: "M100 0 V100 H0", fill: "none", stroke: "rgba(234,244,255,.15)", "stroke-width": 1 }, pat);
  sv("rect", { x: -W, y: -H, width: W * 3, height: H * 3, class: "paper-grid" }, svg);
}

function drawSheet(guide) {
  const svg = $("#bpSvg"); svg.textContent = "";
  const lay = B.layout = layout(guide);
  const W = lay.width, H = lay.height;
  B.sheet = { w: W, h: H };
  const defs = sv("defs", {}, svg);
  const glow = sv("filter", { id: "bpGlow", x: "-30%", y: "-30%", width: "160%", height: "160%" }, defs);
  sv("feGaussianBlur", { stdDeviation: 3.2, result: "b" }, glow);
  const merge = sv("feMerge", {}, glow); sv("feMergeNode", { in: "b" }, merge); sv("feMergeNode", { in: "SourceGraphic" }, merge);
  const arrow = sv("marker", { id: "bpArrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
  sv("path", { d: "M0 1 L10 5 L0 9 Z", class: "arrowhead" }, arrow);
  paper(svg, defs, W, H);

  // frame with zone ticks, like a real drawing
  const frame = sv("g", { class: "frame" }, svg);
  sv("rect", { x: 20, y: 20, width: W - 40, height: H - 40, class: "ink thick" }, frame);
  sv("rect", { x: 34, y: 34, width: W - 68, height: H - 68, class: "ink" }, frame);
  const nx = Math.max(4, Math.round(W / 320)), ny = Math.max(3, Math.round(H / 300));
  for (let i = 0; i < nx; i++) {
    const x = 34 + (W - 68) * (i + 0.5) / nx;
    textAt(frame, x, 31, String(i + 1), "tick", "middle"); textAt(frame, x, H - 23, String(i + 1), "tick", "middle");
    if (i) { const xx = 34 + (W - 68) * i / nx; sv("line", { x1: xx, y1: 20, x2: xx, y2: 34, class: "ink" }, frame); sv("line", { x1: xx, y1: H - 34, x2: xx, y2: H - 20, class: "ink" }, frame); }
  }
  for (let j = 0; j < ny; j++) {
    const y = 34 + (H - 68) * (j + 0.5) / ny, letter = String.fromCharCode(65 + j);
    textAt(frame, 27, y + 4, letter, "tick", "middle"); textAt(frame, W - 27, y + 4, letter, "tick", "middle");
    if (j) { const yy = 34 + (H - 68) * j / ny; sv("line", { x1: 20, y1: yy, x2: 34, y2: yy, class: "ink" }, frame); sv("line", { x1: W - 34, y1: yy, x2: W - 20, y2: yy, class: "ink" }, frame); }
  }

  // drawing title, top-left
  const head = sv("g", {}, svg);
  textAt(head, MARGIN, MARGIN + 6, (guide.title || "").toUpperCase(), "h1");
  textAt(head, MARGIN, MARGIN + 26, (guide.summary || "").slice(0, 150), "sub");

  // zones (dashed boxes per column, labelled)
  if (guide.zones) {
    for (const z of guide.zones) {
      const x = MARGIN + 40 + z.col * CELL_W + 8, y = lay.top - 30;
      const rows = Math.max(...guide.parts.filter((p) => lay.at[p.id][0] === z.col).map((p) => lay.at[p.id][1])) + 1;
      sv("rect", { x, y, width: CELL_W - 16, height: rows * CELL_H + 14, class: "zone" }, svg);
      textAt(svg, x + 8, y - 8, z.label, "zone-lab");
    }
  }

  // links underneath the parts
  const links = sv("g", { class: "links" }, svg);
  links.style.setProperty("--links-d", (calm ? 0 : 0.6 + guide.parts.length * 0.09) + "s");
  B.linkEls = [];
  (guide.links || []).forEach((l, i) => {
    const a = lay.boxes[l.a], b = lay.boxes[l.b];
    if (!a || !b) return;
    const { d, mid } = linkPath(a, b, i);
    const g = sv("g", { class: "link how-" + l.how, "data-a": l.a, "data-b": l.b }, links);
    if (l.how === "pipe") { sv("path", { d, class: "ink pipe-outer", pathLength: 1 }, g); sv("path", { d, class: "pipe-inner" }, g); }
    else sv("path", { d, class: "ink", pathLength: 1, "marker-end": l.how === "flow" ? "url(#bpArrow)" : null }, g);
    if (l.label) {
      const t = textAt(g, mid.x + 5, mid.y - 4, l.label.toUpperCase(), "link-lab");
      t.setAttribute("paint-order", "stroke");
    }
    B.linkEls.push(g);
  });

  // parts: symbol, name, balloon number, status
  const partsG = sv("g", { class: "parts" }, svg);
  B.partEls = {};
  guide.parts.forEach((p, i) => {
    const box = lay.boxes[p.id];
    const g = sv("g", { class: "part", "data-id": p.id, transform: `translate(${box.x} ${box.y})`, tabindex: 0, role: "button" }, partsG);
    g.style.setProperty("--d", (calm ? 0 : 0.15 + i * 0.09) + "s");
    sv("rect", { x: -8, y: -8, width: box.w + 16, height: box.h + 44, class: "hit" }, g);
    sv("rect", { x: -4, y: -4, width: box.w + 8, height: box.h + 8, class: "focus-box" }, g);
    symbol(g, p.kind, box.w, box.h);
    const lines = wrap(p.name, 24);
    lines.forEach((ln, k) => textAt(g, box.w / 2, box.h + 15 + k * 12, ln, "plab", "middle"));
    // balloon (item number) with a leader to the part
    const bx = -16, by = -14;
    sv("line", { x1: bx + 8, y1: by + 8, x2: 8, y2: 6, class: "ink thin" }, g);
    sv("circle", { cx: bx, cy: by, r: 11, class: "balloon" }, g);
    textAt(g, bx, by + 4, String(i + 1), "bnum", "middle");
    const st = statusOf(p);
    if (st) {
      const dot = sv("circle", { cx: bx + 9, cy: by - 9, r: 4.5, class: "sdot" }, g); dot.style.fill = st[0];
    }
    if (p.qty > 1) textAt(g, box.w - 2, -6, `×${p.qty}`, "qty", "end");
    g.addEventListener("click", () => selectPart(p.id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter") selectPart(p.id); });
    B.partEls[p.id] = g;
  });

  drawTable(svg, guide, lay);
  drawNotes(svg, guide, lay);
  drawTitleBlock(svg, guide, lay);
  if (guide.disclaimer) textAt(svg, MARGIN, H - 46, guide.disclaimer.toUpperCase(), "disc");

  // pointer last so it flies over everything (Clicky-style triangle)
  const ptr = sv("g", { id: "bpPointer", class: "pointer hidden" }, svg);
  const inner = sv("g", { class: "pointer-inner" }, ptr);
  sv("path", { d: "M0 0 L26 10 L12 13 L7 27 Z", class: "pointer-shape" }, inner);
  B.pointer.el = ptr; B.pointer.inner = inner; B.pointer.shown = false;
  svg.classList.remove("drawn"); void svg.getBoundingClientRect(); svg.classList.add("drawn");
}

function drawTable(svg, guide, lay) {
  const x = lay.tableX, y = MARGIN, w = TABLE_W, g = sv("g", { class: "table" }, svg);
  sv("rect", { x, y, width: w, height: lay.tableH, class: "ink" }, g);
  textAt(g, x + w / 2, y + 22, "LIST OF MATERIAL", "th1", "middle");
  sv("line", { x1: x, y1: y + 32, x2: x + w, y2: y + 32, class: "ink" }, g);
  const cols = [x + 34, x + w - 108, x + w - 68];
  const hy = y + 32;
  textAt(g, x + 17, hy + 15, "ITEM", "th", "middle"); textAt(g, x + 42, hy + 15, "DESCRIPTION", "th");
  textAt(g, cols[1] + 20, hy + 15, "QTY", "th", "middle"); textAt(g, cols[2] + 34, hy + 15, "£ / STATUS", "th", "middle");
  for (const cx of cols) sv("line", { x1: cx, y1: hy, x2: cx, y2: y + lay.tableH, class: "ink thin" }, g);
  guide.parts.forEach((p, i) => {
    const ry = hy + ROW_H * (i + 1);
    sv("line", { x1: x, y1: ry, x2: x + w, y2: ry, class: "ink thin" }, g);
    const row = sv("g", { class: "trow", "data-id": p.id }, g);
    sv("rect", { x: x + 1, y: ry + 1, width: w - 2, height: ROW_H - 2, class: "trow-bg" }, row);
    textAt(row, x + 17, ry + 15, String(i + 1), "td", "middle");
    textAt(row, x + 42, ry + 15, p.name.toUpperCase().slice(0, 30), "td");
    textAt(row, cols[1] + 20, ry + 15, String(p.qty || 1), "td", "middle");
    const st = statusOf(p), item = p.item && B.items[p.item];
    const price = (item && item.price) || p.price;
    const cell = textAt(row, cols[2] + 34, ry + 15, (price ? "£" + Math.round(price) + " " : "") + (st ? st[1] : price ? "EST." : "—"), "td", "middle");
    if (st) cell.style.fill = st[0];
    row.addEventListener("click", () => selectPart(p.id));
  });
}

function noteLines(guide) {
  const notes = [];
  if ((guide.tools || []).length) notes.push("TOOLS: " + guide.tools.join(", "));
  for (const s of guide.safety || []) notes.push(s);
  const out = [];
  notes.slice(0, 5).forEach((ln, i) => out.push(...wrapAll(`${i + 1}. ${ln}`, 56)));
  return out.slice(0, 16);
}
function wrapAll(text, max) {
  const lines = [""];
  for (const w of String(text).toUpperCase().split(/\s+/)) {
    if ((lines[lines.length - 1] + " " + w).trim().length > max && lines[lines.length - 1]) lines.push("   " + w);
    else lines[lines.length - 1] = (lines[lines.length - 1] + " " + w).trim();
  }
  return lines;
}
function drawNotes(svg, guide, lay) {
  const x = lay.tableX, y = MARGIN + lay.tableH + 20, w = TABLE_W, g = sv("g", { class: "notes" }, svg);
  sv("rect", { x, y, width: w, height: lay.notesH, class: "ink" }, g);
  textAt(g, x + 12, y + 20, "GENERAL NOTES", "th1");
  noteLines(guide).forEach((ln, i) => textAt(g, x + 12, y + 40 + i * 15, ln, "note"));
}

function drawTitleBlock(svg, guide, lay) {
  const w = TABLE_W, h = lay.titleH, x = lay.tableX, y = lay.height - MARGIN - h, g = sv("g", { class: "title" }, svg);
  sv("rect", { x, y, width: w, height: h, class: "ink thick" }, g);
  const src = guide.source === "ai" && guide.flagged ? `AI DRAFT · ${guide.flagged} STEP${guide.flagged > 1 ? "S" : ""} FLAGGED`
    : { checked: "CHECKED GUIDE", ai: "AI DRAFT · VERIFY", printable: "PRINTABLE PART" }[guide.source] || "GUIDE";
  const rows = [
    ["TITLE", (guide.title || "").toUpperCase().slice(0, 34)],
    ["DRG NO.", guide.drawing_no || ("TTN-" + String(guide.id || "").replace(/^ai-/, "").slice(0, 12).toUpperCase())],
    ["SOURCE", src],
    ["DIFFICULTY", (guide.difficulty || "—").toUpperCase()],
    ["TIME", (guide.time || "—").toUpperCase().slice(0, 30)],
    ["SCALE", (guide.units || "NTS").toUpperCase()],
    ["DRAWN", "TITAN · " + (guide.created || new Date().toISOString().slice(0, 10))],
  ];
  const rh = (h - 30) / rows.length;
  textAt(g, x + w / 2, y + 21, "T I T A N   W O R K S H O P", "th1", "middle");
  sv("line", { x1: x, y1: y + 30, x2: x + w, y2: y + 30, class: "ink" }, g);
  sv("line", { x1: x + 116, y1: y + 30, x2: x + 116, y2: y + h, class: "ink thin" }, g);
  rows.forEach(([k, v], i) => {
    const ry = y + 30 + rh * i;
    if (i) sv("line", { x1: x, y1: ry, x2: x + w, y2: ry, class: "ink thin" }, g);
    textAt(g, x + 10, ry + rh / 2 + 4, k, "tk");
    const t = textAt(g, x + 126, ry + rh / 2 + 4, v, "tv");
    if (k === "SOURCE" && guide.source === "ai") t.classList.add("warn");
  });
}

// ---------------------------------------------------------------------------
// 4. VIEW: pan, zoom, pinch, and smooth camera moves
// ---------------------------------------------------------------------------
function setVB(vb) { B.vb = vb; $("#bpSvg").setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`); }
function fitBox(x, y, w, h, pad = 40) {
  const view = $("#bpView").getBoundingClientRect(), aspect = view.width / Math.max(1, view.height);
  w += pad * 2; h += pad * 2; x -= pad; y -= pad;
  if (w / h < aspect) { const nw = h * aspect; x -= (nw - w) / 2; w = nw; } else { const nh = w / aspect; y -= (nh - h) / 2; h = nh; }
  return { x, y, w, h };
}
let camRaf = 0;
function moveCamera(goal, instant) {
  cancelAnimationFrame(camRaf);
  if (instant || calm || !B.vb) { setVB(goal); return; }
  const from = { ...B.vb }, t0 = performance.now(), dur = 650;
  const tick = (now) => {
    const t = Math.min(1, (now - t0) / dur), e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    setVB({ x: from.x + (goal.x - from.x) * e, y: from.y + (goal.y - from.y) * e, w: from.w + (goal.w - from.w) * e, h: from.h + (goal.h - from.h) * e });
    if (t < 1) camRaf = requestAnimationFrame(tick);
  };
  camRaf = requestAnimationFrame(tick);
}
function overview(instant) { if (B.sheet) moveCamera(fitBox(0, 0, B.sheet.w, B.sheet.h, 10), instant); }
function frameParts(ids) {
  const boxes = ids.map((id) => B.layout.boxes[id]).filter(Boolean);
  if (!boxes.length) return overview();
  let x0 = Math.min(...boxes.map((b) => b.x)), y0 = Math.min(...boxes.map((b) => b.y));
  let x1 = Math.max(...boxes.map((b) => b.x + b.w)), y1 = Math.max(...boxes.map((b) => b.y + b.h + 30));
  const minW = Math.min(B.sheet.w, 900);
  if (x1 - x0 < minW) { const c = (x0 + x1) / 2; x0 = c - minW / 2; x1 = c + minW / 2; }
  // room underneath for the PLAY bar
  moveCamera(fitBox(x0, y0 - 30, x1 - x0, y1 - y0 + 110, 50));
}
function svgPoint(clientX, clientY) {
  const r = $("#bpView").getBoundingClientRect();
  return { x: B.vb.x + (clientX - r.left) / r.width * B.vb.w, y: B.vb.y + (clientY - r.top) / r.height * B.vb.h };
}
function zoomAt(clientX, clientY, factor) {
  if (!B.vb) return;
  const p = svgPoint(clientX, clientY);
  const w = Math.min(Math.max(B.vb.w * factor, 200), (B.sheet?.w || 2000) * 2.5), k = w / B.vb.w;
  setVB({ x: p.x - (p.x - B.vb.x) * k, y: p.y - (p.y - B.vb.y) * k, w, h: B.vb.h * k });
}
const touches = new Map();
let panStart = null, pinchStart = null;
function initPanZoom() {
  const view = $("#bpView");
  view.addEventListener("wheel", (e) => { e.preventDefault(); cancelAnimationFrame(camRaf); zoomAt(e.clientX, e.clientY, Math.exp(e.deltaY * 0.0012)); }, { passive: false });
  view.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".part,.trow,.bp-callout")) return;
    view.setPointerCapture(e.pointerId); touches.set(e.pointerId, e);
    cancelAnimationFrame(camRaf);
    if (touches.size === 1) panStart = { x: e.clientX, y: e.clientY, vb: { ...B.vb } };
    if (touches.size === 2) { const [a, b] = [...touches.values()]; pinchStart = { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), vb: { ...B.vb } }; }
  });
  view.addEventListener("pointermove", (e) => {
    if (!touches.has(e.pointerId) || !B.vb) return;
    touches.set(e.pointerId, e);
    const r = view.getBoundingClientRect();
    if (touches.size === 1 && panStart) {
      const dx = (e.clientX - panStart.x) / r.width * panStart.vb.w, dy = (e.clientY - panStart.y) / r.height * panStart.vb.h;
      setVB({ ...panStart.vb, x: panStart.vb.x - dx, y: panStart.vb.y - dy });
    } else if (touches.size === 2 && pinchStart) {
      const [a, b] = [...touches.values()], d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      B.vb = { ...pinchStart.vb }; zoomAt((a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2, pinchStart.d / Math.max(d, 1));
    }
    placeCallout();
  });
  const up = (e) => { touches.delete(e.pointerId); if (touches.size < 2) pinchStart = null; if (!touches.size) panStart = null; };
  view.addEventListener("pointerup", up); view.addEventListener("pointercancel", up);
  new ResizeObserver(() => {
    if (!B.open || !B.vb) return;
    const s = B.guide?.steps?.[B.step];
    if (s) frameParts(s.parts); else overview(true);
  }).observe(view);
}

// ---------------------------------------------------------------------------
// 5. THE POINTER: flies to each part like Clicky's triangle
// ---------------------------------------------------------------------------
function pointerTarget(id) {
  const b = B.layout.boxes[id];
  return b ? { x: b.x + b.w * 0.62, y: b.y + b.h * 0.55 } : null;
}
function setPointer(x, y, angle, scale) {
  B.pointer.x = x; B.pointer.y = y;
  B.pointer.el.setAttribute("transform", `translate(${x} ${y})`);
  B.pointer.inner.setAttribute("transform", `rotate(${angle}) scale(${scale})`);
}
function flyTo(id) {
  const target = pointerTarget(id), P = B.pointer;
  if (!target || !P.el) return Promise.resolve();
  const gen = ++P.gen;
  if (!P.shown || calm) {
    P.shown = true; P.el.classList.remove("hidden");
    setPointer(target.x, target.y, 0, 1); placeCallout(); return Promise.resolve();
  }
  const from = { x: P.x, y: P.y }, dist = Math.hypot(target.x - from.x, target.y - from.y);
  const ctrl = { x: (from.x + target.x) / 2, y: Math.min(from.y, target.y) - Math.min(220, dist * 0.45) };
  const dur = Math.min(1100, 420 + dist * 0.6), t0 = performance.now();
  return new Promise((resolve) => {
    const tick = (now) => {
      if (gen !== P.gen) return resolve();
      const t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - t, 3);
      const x = (1 - e) ** 2 * from.x + 2 * (1 - e) * e * ctrl.x + e * e * target.x;
      const y = (1 - e) ** 2 * from.y + 2 * (1 - e) * e * ctrl.y + e * e * target.y;
      const tx = 2 * (1 - e) * (ctrl.x - from.x) + 2 * e * (target.x - ctrl.x), ty = 2 * (1 - e) * (ctrl.y - from.y) + 2 * e * (target.y - ctrl.y);
      const angle = t < 1 ? Math.atan2(ty, tx) * 180 / Math.PI - 22 : 0;
      setPointer(x, y, t < 0.92 ? angle : angle * (1 - (t - 0.92) / 0.08), 1 + 0.45 * Math.sin(Math.PI * t));
      placeCallout();
      if (t < 1) requestAnimationFrame(tick); else resolve();
    };
    requestAnimationFrame(tick);
  });
}
async function tourParts(ids, gen) {
  for (let k = 0; k < ids.length; k++) {
    if (gen !== B.playGen) return;
    for (const [pid, g] of Object.entries(B.partEls)) g.classList.toggle("on", pid === ids[k] || (ids.includes(pid) && k === ids.length));
    await flyTo(ids[k]);
    await sleep(ids.length > 1 ? 900 : 0);
  }
  if (gen === B.playGen) {
    for (const [pid, g] of Object.entries(B.partEls)) g.classList.toggle("on", ids.includes(pid));
    placeCallout();
  }
}
function placeCallout() {
  const box = $("#bpCallout");
  if (box.hidden || !B.vb || !B.pointer.shown) return;
  const r = $("#bpView").getBoundingClientRect();
  const px = (B.pointer.x - B.vb.x) / B.vb.w * r.width, py = (B.pointer.y - B.vb.y) / B.vb.h * r.height;
  const bw = box.offsetWidth, bh = box.offsetHeight;
  // keep the parts being talked about visible: try spots around the pointer, then the corners
  const toScreen = (b) => ({ x0: (b.x - 24 - B.vb.x) / B.vb.w * r.width, y0: (b.y - 24 - B.vb.y) / B.vb.h * r.height,
    x1: (b.x + b.w + 8 - B.vb.x) / B.vb.w * r.width, y1: (b.y + b.h + 34 - B.vb.y) / B.vb.h * r.height });
  const active = Object.entries(B.partEls || {}).filter(([, g]) => g.classList.contains("on")).map(([id]) => toScreen(B.layout.boxes[id]));
  const hits = (x, y) => active.some((a) => x < a.x1 && x + bw > a.x0 && y < a.y1 && y + bh > a.y0);
  const fits = (x, y) => x >= 10 && y >= 10 && x + bw <= r.width - 10 && y + bh <= r.height - 70;
  const spots = [[px + 30, py + 24], [px - bw - 18, py + 24], [px + 30, py - bh - 18], [px - bw - 18, py - bh - 18],
    [r.width - bw - 12, 12], [12, 12], [r.width - bw - 12, r.height - bh - 70], [12, r.height - bh - 70]];
  let spot = spots.find(([x, y]) => fits(x, y) && !hits(x, y)) || spots.find(([x, y]) => fits(x, y)) || [12, 12];
  box.style.transform = `translate(${Math.max(10, spot[0])}px, ${Math.max(10, spot[1])}px)`;
}

// ---------------------------------------------------------------------------
// 6. STEPS, PLAYBACK AND NARRATION
// ---------------------------------------------------------------------------
function narration(i) {
  const s = B.guide.steps[i];
  const who = s.who === "mechanic" ? " This one's for your mechanic." : "";
  return `Step ${i + 1}. ${s.title}. ${s.text}${s.warn ? " Careful: " + s.warn : ""}${who}${s.danger ? " " + s.danger : ""}`;
}
function say(text) {
  if (window.titanSpeech && window.titanSpeech.enabled()) return window.titanSpeech.say(text);
  return sleep(Math.min(14000, 2500 + text.length * 45));      // muted: give time to read
}
function setStep(i, opts = {}) {
  const steps = B.guide?.steps || [];
  B.step = i;
  B.playGen++;
  const gen = B.playGen;
  if (!opts.fromPlay && B.playing) stopPlay(false);
  const s = steps[i];
  for (const [pid, g] of Object.entries(B.partEls || {})) {
    g.classList.toggle("dim", !!s && !s.parts.includes(pid));
    g.classList.toggle("on", false);
  }
  for (const l of B.linkEls || []) l.classList.toggle("dim", !!s && !(s.parts.includes(l.dataset.a) && s.parts.includes(l.dataset.b)));
  const box = $("#bpCallout");
  if (!s) {
    box.hidden = true; B.pointer.el?.classList.add("hidden"); B.pointer.shown = false;
    overview(); renderSteps(); return Promise.resolve();
  }
  box.textContent = "";
  const head = el("div", "bp-c-head");
  head.append(el("b", null, `STEP ${i + 1} / ${steps.length}`), el("span", "bp-who" + (s.who === "mechanic" ? " mech" : ""), s.who === "mechanic" ? "MECHANIC" : "YOU"));
  box.append(head, el("div", "bp-c-title", s.title), el("p", null, s.text));
  if (s.warn) box.append(el("p", "bp-c-warn", "⚠ " + s.warn));
  if (s.danger) box.append(el("p", "bp-c-danger", "⛔ " + s.danger));
  box.hidden = false;
  frameParts(s.parts);
  renderSteps();
  const tour = sleep(calm ? 0 : 380).then(() => tourParts(s.parts, gen));
  if (opts.speak !== false && !opts.fromPlay && window.titanSpeech?.enabled()) window.titanSpeech.say(narration(i));
  return tour;
}
async function play() {
  if (B.playing) { stopPlay(true); return; }
  const steps = B.guide?.steps || [];
  if (!steps.length) return;
  B.playing = true; $("#bpPlay").textContent = "❚❚ PAUSE"; $("#bpPlay").classList.add("active");
  let i = B.step >= 0 && B.step < steps.length - 1 ? B.step : 0;
  if (B.step === steps.length - 1) i = 0;
  const run = ++B.runId;
  if (B.step < 0 && B.guide.summary && i === 0) await say(`${B.guide.title}. ${B.guide.summary}`);
  for (; i < steps.length && B.playing && run === B.runId; i++) {
    await Promise.all([setStep(i, { fromPlay: true }), say(narration(i))]);
    if (!B.playing || run !== B.runId) return;
    await sleep(500);
  }
  if (B.playing && run === B.runId) { stopPlay(false); await say("That's every step. Tap any part to see it again."); }
}
B.runId = 0;
function stopPlay(silence) {
  B.playing = false; B.runId++;
  $("#bpPlay").textContent = "▶ PLAY"; $("#bpPlay").classList.remove("active");
  if (silence && window.titanSpeech) window.titanSpeech.stop();
}

function selectPart(id) {
  const p = B.guide.parts.find((x) => x.id === id);
  if (!p) return;
  const box = $("#bpCallout"); box.textContent = "";
  const n = B.guide.parts.indexOf(p) + 1, st = statusOf(p), item = p.item && B.items[p.item];
  const head = el("div", "bp-c-head"); head.append(el("b", null, `ITEM ${n}`));
  if (st) { const s = el("span", "bp-who", st[1]); s.style.color = st[0]; s.style.borderColor = st[0]; head.append(s); }
  box.append(head, el("div", "bp-c-title", p.name));
  const facts = [p.qty > 1 ? `Qty ${p.qty}` : "", (item && item.price) || p.price ? `£${Math.round((item && item.price) || p.price)}${item ? "" : " (estimate)"}` : "", p.note || "", item && item.notes ? item.notes : ""].filter(Boolean);
  if (facts.length) box.append(el("p", null, facts.join(" · ")));
  const used = (B.guide.steps || []).map((s, i) => (s.parts.includes(id) ? i + 1 : 0)).filter(Boolean);
  if (used.length) box.append(el("p", "bp-c-dim", "Used in step " + used.join(", ")));
  box.hidden = false;
  for (const [pid, g] of Object.entries(B.partEls)) { g.classList.toggle("on", pid === id); g.classList.toggle("dim", false); }
  for (const l of B.linkEls) l.classList.toggle("dim", l.dataset.a !== id && l.dataset.b !== id);
  for (const r of document.querySelectorAll("#bpSvg .trow")) r.classList.toggle("on", r.dataset.id === id);
  B.playGen++; flyTo(id);
}

// ---------------------------------------------------------------------------
// 7. SIDE PANEL: steps, parts and the car project
// ---------------------------------------------------------------------------
const checkKey = (i, j) => `titan.check.${B.guide.id}.${i}.${j}`;
function renderSteps() {
  const box = $("#bpSteps"); box.textContent = "";
  const g = B.guide; if (!g) return;
  if (g.summary) box.append(el("p", "bp-summary", g.summary));
  (g.steps || []).forEach((s, i) => {
    const card = el("div", "bp-step" + (i === B.step ? " on" : ""));
    const head = el("button", "bp-step-h"); head.type = "button";
    head.append(el("span", "bp-num", String(i + 1)), el("b", null, s.title));
    if (s.who === "mechanic") head.append(el("span", "bp-who mech", "MECHANIC"));
    if (s.danger) head.append(el("span", "bp-who danger", "⛔ CHECK"));
    head.addEventListener("click", () => setStep(i));
    card.append(head);
    if (i === B.step) {
      card.append(el("p", null, s.text));
      if (s.warn) card.append(el("p", "bp-c-warn", "⚠ " + s.warn));
      if (s.danger) card.append(el("p", "bp-c-danger", "⛔ " + s.danger));
      (s.checks || []).forEach((c, j) => {
        const lab = el("label", "bp-check"), cb = el("input"); cb.type = "checkbox";
        try { cb.checked = localStorage.getItem(checkKey(i, j)) === "1"; } catch (e) { /* storage blocked */ }
        cb.onchange = () => { try { localStorage.setItem(checkKey(i, j), cb.checked ? "1" : "0"); } catch (e) { /* ignore */ } };
        lab.append(cb, el("span", null, c)); card.append(lab);
      });
    }
    box.append(card);
  });
  $("#bpCount").textContent = B.step >= 0 ? `STEP ${B.step + 1} / ${(g.steps || []).length}` : `${(g.steps || []).length} STEPS`;
  box.querySelector(".bp-step.on")?.scrollIntoView({ block: "nearest", behavior: calm ? "auto" : "smooth" });
}
function renderParts() {
  const box = $("#bpParts"); box.textContent = "";
  const g = B.guide; if (!g) return;
  g.parts.forEach((p, i) => {
    const row = el("button", "bp-row"); row.type = "button";
    const st = statusOf(p), item = p.item && B.items[p.item], price = (item && item.price) || p.price;
    const meta = el("span", "bp-meta", (price ? "£" + Math.round(price) + " · " : "") + (st ? st[1] : "INFO"));
    if (st) meta.style.color = st[0];
    row.append(el("span", "bp-num", String(i + 1)), el("span", null, p.name + (p.qty > 1 ? ` ×${p.qty}` : "")), meta);
    row.addEventListener("click", () => selectPart(p.id));
    box.append(row);
  });
  if ((g.tools || []).length) { box.append(el("div", "sub-h", "TOOLS")); for (const t of g.tools) box.append(el("div", "bp-item", t)); }
  if ((g.safety || []).length) { box.append(el("div", "sub-h", "SAFETY")); for (const t of g.safety) box.append(el("div", "bp-item warn", "⚠ " + t)); }
}
function renderProject() {
  const box = $("#bpProject"); box.textContent = "";
  const p = B.project; if (!p) return;
  box.append(el("div", "bp-c-title", p.title));
  let spent = 0, considering = 0;
  const totals = el("div", "bp-totals"); box.append(totals);
  box.append(el("div", "sub-h", "NEXT MOVES"));
  (p.next_moves || []).forEach((m, i) => {
    const row = el("div", "bp-move"); row.append(el("b", null, `${i + 1}. ${m.title}`));
    if (m.who === "mechanic") row.append(el("span", "bp-who mech", "MECHANIC"));
    row.append(el("p", null, m.detail)); box.append(row);
  });
  for (const status of Object.keys(STATUS)) {
    const items = p.items.filter((it) => it.status === status);
    if (!items.length) continue;
    const [color, label] = STATUS[status];
    const h = el("div", "sub-h", label); h.style.color = color; box.append(h);
    for (const it of items) {
      const row = el("div", "bp-item"); row.append(el("span", null, it.name), el("span", "bp-price", it.price ? "£" + it.price : "")); box.append(row);
      if (["owned", "ordered", "fitted"].includes(status)) spent += it.price || 0;
      if (["considering", "needed"].includes(status)) considering += it.price || 0;
    }
  }
  totals.append(el("span", null, `SPENT £${spent}`), el("span", null, `CONSIDERING £${considering}`));
}

// ---------------------------------------------------------------------------
// 8. LOADING GUIDES (and waiting for drafts)
// ---------------------------------------------------------------------------
async function loadList(selectId) {
  const r = await window.titanApi("/api/builder");
  if (!r.ok) return false;
  B.guides = r.data.guides || []; B.project = r.data.project || null;
  B.items = Object.fromEntries(((B.project && B.project.items) || []).map((it) => [it.id, it]));
  const sel = $("#bpGuide"); sel.textContent = "";
  const groups = { checked: "CHECKED GUIDES", ai: "YOUR BLUEPRINTS (AI DRAFTS)", printable: "PRINTABLE PARTS" };
  for (const [src, label] of Object.entries(groups)) {
    const list = B.guides.filter((g) => g.source === src);
    if (!list.length) continue;
    const og = el("optgroup"); og.label = label;
    for (const g of list) { const o = el("option", null, (g.state === "drafting" ? "⏳ " : g.state === "failed" ? "✕ " : "") + g.title); o.value = g.id; og.append(o); }
    sel.append(og);
  }
  if (selectId) sel.value = selectId;
  return true;
}
function showDrafting(guide) {
  const svg = $("#bpSvg"); svg.textContent = "";
  B.sheet = { w: 1100, h: 640 }; B.partEls = {}; B.linkEls = [];
  paper(svg, sv("defs", {}, svg), 1100, 640);
  sv("rect", { x: 20, y: 20, width: 1060, height: 600, class: "ink thick" }, svg);
  sv("rect", { x: 34, y: 34, width: 1032, height: 572, class: "ink" }, svg);
  textAt(svg, 550, 250, (guide.title || guide.question || "").toUpperCase().slice(0, 60), "h1", "middle");
  const msg = textAt(svg, 550, 300, guide.state === "failed" ? (guide.error || "Drafting failed.").toUpperCase() : "DRAFTING THE BLUEPRINT…", guide.state === "failed" ? "sub warn" : "sub", "middle");
  if (guide.state === "drafting") {
    const t0 = Date.now();
    const bar = sv("rect", { x: 400, y: 330, width: 0, height: 4, class: "progress" }, svg);
    sv("rect", { x: 400, y: 330, width: 300, height: 4, class: "ink thin" }, svg);
    const timer = textAt(svg, 550, 362, "", "tick", "middle");
    B.draftTick = setInterval(() => {
      const s = Math.round((Date.now() - t0) / 1000);
      timer.textContent = `${s} S · THE LOCAL MODEL IS PLANNING PARTS, CONNECTIONS AND STEPS`;
      bar.setAttribute("width", String(Math.min(296, 300 * (1 - Math.exp(-s / 45)))));
    }, 500);
    void msg;
  }
  $("#bpRetry").hidden = guide.state !== "failed";
  $("#bpCallout").hidden = true;
  overview(true);
}
async function show(id, opts = {}) {
  clearInterval(B.draftTick); clearTimeout(B.pollTimer);
  if (B.playing) stopPlay(true);
  const r = await window.titanApi("/api/builder/guide?id=" + encodeURIComponent(id));
  if (!r.ok) { $("#bpSub").textContent = "Couldn't load that blueprint."; return; }
  const guide = r.data;
  B.guide = guide; B.step = -1;
  $("#bpGuide").value = id;
  $("#bpDelete").hidden = guide.source !== "ai";
  $("#bpExport").hidden = !guide.printable;
  $("#bpSub").textContent = guide.state === "ready" ? `${guide.parts.length} PARTS · ${(guide.steps || []).length} STEPS` : guide.state === "drafting" ? "DRAFTING…" : "DRAFT FAILED";
  $("#bpTabProject").hidden = !B.project;
  if (guide.state !== "ready") {
    showDrafting(guide); $("#bpSteps").textContent = ""; $("#bpParts").textContent = "";
    if (guide.state === "drafting") B.pollTimer = setTimeout(() => poll(id), 2000);
    return;
  }
  $("#bpRetry").hidden = true;
  drawSheet(guide); overview(true);
  renderSteps(); renderParts(); renderProject();
  if (opts.autoplay) setTimeout(() => { if (B.open && B.guide === guide && !B.playing) play(); }, calm ? 200 : 900 + guide.parts.length * 90);
}
async function poll(id) {
  if (!B.open || B.guide?.id !== id) return;
  const r = await window.titanApi("/api/builder/guide?id=" + encodeURIComponent(id));
  if (r.ok && r.data.state !== "drafting") {
    await loadList(id);
    await show(id, { autoplay: r.data.state === "ready" });
    return;
  }
  B.pollTimer = setTimeout(() => poll(id), 2000);
}
async function open(id) {
  $("#builder").hidden = false; B.open = true;
  document.body.classList.add("builder-open");
  if (!(await loadList(id))) { $("#bpSub").textContent = "Sign in to open the builder."; return; }
  const first = id || $("#bpGuide").value;
  if (first) await show(first, { autoplay: false });
}
function close() {
  if (B.playing) stopPlay(true);
  clearInterval(B.draftTick); clearTimeout(B.pollTimer);
  B.open = false; $("#builder").hidden = true; document.body.classList.remove("builder-open");
}
async function ask(question) {
  question = (question || "").trim();
  if (!question) return;
  $("#bpAskInput").value = "";
  const r = await window.titanApi("/api/builder/make", { q: question });
  if (!r.ok) { $("#bpSub").textContent = r.data.error || "Couldn't start that blueprint."; return; }
  await loadList(r.data.id); await show(r.data.id);
}
async function remove() {
  if (!B.guide || B.guide.source !== "ai") return;
  if (!confirm(`Delete the blueprint "${B.guide.title}"?`)) return;
  await window.titanApi("/api/builder/delete", { id: B.guide.id });
  await loadList(); const first = $("#bpGuide").value; if (first) show(first);
}

// ---------------------------------------------------------------------------
// 9. STL EXPORT for printable parts (loads three.js only when asked)
// ---------------------------------------------------------------------------
async function exportStl() {
  const g = B.guide; if (!g || !g.printable) return;
  const THREE = await import("three");
  const { STLExporter } = await import("/vendor/STLExporter.js");
  const DEG = Math.PI / 180, solid = new THREE.Group();
  for (const p of g.parts) {
    const s = p.shape || {}; let geo;
    if (s.type === "tube") {
      const a = s.r_in, b = s.r_out, h = s.h / 2;
      geo = new THREE.LatheGeometry([[a, -h], [b, -h], [b, h], [a, h], [a, -h]].map(([x, y]) => new THREE.Vector2(x, y)), 72);
    } else if (s.type === "plate") {
      const w = s.w / 2, h = s.h / 2, r = Math.min(s.r || 0, w, h), sh = new THREE.Shape();
      sh.moveTo(-w + r, -h); sh.lineTo(w - r, -h); sh.quadraticCurveTo(w, -h, w, -h + r); sh.lineTo(w, h - r);
      sh.quadraticCurveTo(w, h, w - r, h); sh.lineTo(-w + r, h); sh.quadraticCurveTo(-w, h, -w, h - r);
      sh.lineTo(-w, -h + r); sh.quadraticCurveTo(-w, -h, -w + r, -h);
      for (const [x, y, rad] of s.holes || []) { const hp = new THREE.Path(); hp.absarc(x, y, rad, 0, Math.PI * 2, true); sh.holes.push(hp); }
      geo = new THREE.ExtrudeGeometry(sh, { depth: s.t, bevelEnabled: false, curveSegments: 32 }); geo.translate(0, 0, -s.t / 2);
    } else if (s.type === "box") geo = new THREE.BoxGeometry(s.w, s.h, s.d);
    else if (s.type === "cylinder") geo = new THREE.CylinderGeometry(s.rTop ?? s.r, s.r, s.h, 48);
    else continue;
    const m = new THREE.Mesh(geo);
    m.position.set(...(p.pos || [0, 0, 0])); m.rotation.set(...(p.rot || [0, 0, 0]).map((v) => v * DEG));
    solid.add(m);
  }
  solid.updateMatrixWorld(true);
  const data = new STLExporter().parse(solid, { binary: true });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([data], { type: "model/stl" }));
  a.download = (g.title || "titan-part").replace(/[^a-z0-9]+/gi, "-").toLowerCase() + ".stl";
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ---------------------------------------------------------------------------
// 10. WIRING
// ---------------------------------------------------------------------------
$("#bpClose").addEventListener("click", close);
$("#bpGuide").addEventListener("change", (e) => show(e.target.value));
$("#bpPlay").addEventListener("click", play);
$("#bpPrev").addEventListener("click", () => B.guide?.steps?.length && setStep(Math.max(0, B.step - 1)));
$("#bpNext").addEventListener("click", () => B.guide?.steps?.length && setStep(Math.min(B.guide.steps.length - 1, B.step + 1)));
$("#bpFit").addEventListener("click", () => setStep(-1));
$("#bpExport").addEventListener("click", exportStl);
$("#bpDelete").addEventListener("click", remove);
$("#bpRetry").addEventListener("click", () => B.guide && ask(B.guide.question || B.guide.title));
$("#bpAsk").addEventListener("submit", (e) => { e.preventDefault(); ask($("#bpAskInput").value); });
// the builder's MIC drives Titan's own mic; what you say goes through send(),
// which steers the blueprint ("next", "back") or asks Titan anything else
$("#bpMic").addEventListener("click", () => $("#mic")?.click());
const mainMic = $("#mic");
if (mainMic) new MutationObserver(() => $("#bpMic").classList.toggle("rec", mainMic.classList.contains("rec")))
  .observe(mainMic, { attributes: true, attributeFilter: ["class"] });
for (const b of document.querySelectorAll(".bp-tabs .pill")) {
  b.addEventListener("click", () => {
    for (const x of document.querySelectorAll(".bp-tabs .pill")) x.classList.toggle("active", x === b);
    for (const id of ["bpSteps", "bpParts", "bpProject"]) $("#" + id).hidden = id !== "bp" + b.dataset.tab;
  });
}
window.addEventListener("keydown", (e) => {
  if (!B.open || e.target.closest("input,select,textarea")) return;
  if (e.key === "Escape") close();
  if (e.key === "ArrowRight") $("#bpNext").click();
  if (e.key === "ArrowLeft") $("#bpPrev").click();
  if (e.key === " ") { e.preventDefault(); play(); }
});
initPanZoom();

// spoken shortcuts while the builder is open ("next", "back", "repeat", "stop")
function command(text) {
  if (!B.open || !B.guide || B.guide.state !== "ready") return false;
  const t = text.toLowerCase().replace(/[^a-z ]/g, "").trim();
  const steps = B.guide.steps || [];
  if (/^(next|next step|go on|carry on|continue)$/.test(t)) { setStep(Math.min(steps.length - 1, B.step + 1)); return true; }
  if (/^(back|previous|previous step|go back|last step)$/.test(t)) { setStep(Math.max(0, B.step - 1)); return true; }
  if (/^(repeat|repeat that|again|say that again)$/.test(t)) { if (B.step >= 0) setStep(B.step); return true; }
  if (/^(stop|pause|wait)$/.test(t)) { if (B.playing) stopPlay(true); else window.titanSpeech?.stop(); return true; }
  if (/^(play|start|walk me through it|go)$/.test(t)) { if (!B.playing) play(); return true; }
  if (/^(overview|show everything|zoom out)$/.test(t)) { setStep(-1); return true; }
  return false;
}
window.TitanBuilder = { open, close, command, isOpen: () => B.open };
