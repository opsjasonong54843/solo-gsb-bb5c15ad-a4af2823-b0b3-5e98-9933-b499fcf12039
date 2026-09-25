"use strict";

const $ = (sel) => document.querySelector(sel);

const els = {
  expected: $("#expectedLength"),
  fragments: $("#fragments"),
  tpl: $("#fragTpl"),
  btnAdd: $("#btnAdd"),
  btnDemo: $("#btnDemo"),
  btnClear: $("#btnClear"),
  btnRun: $("#btnRun"),
  btnDownload: $("#btnDownload"),
  banner: $("#banner"),
  results: $("#results"),
  map: $("#map"),
  mapTip: $("#mapTip"),
  stFragments: $("#stFragments"),
  stCovered: $("#stCovered"),
  stClean: $("#stClean"),
  stConflict: $("#stConflict"),
  stGap: $("#stGap"),
  stPercent: $("#stPercent"),
  listCovered: $("#listCovered"),
  listConflict: $("#listConflict"),
  listGap: $("#listGap"),
  listOverflow: $("#listOverflow"),
};

const STORE_KEY = "pcap-reasm-v1";

/* ---------------- 分片输入行 ---------------- */

function renumber() {
  els.fragments.querySelectorAll(".frag-idx").forEach((el, i) => {
    el.textContent = "#" + (i + 1);
  });
}

function addFragment({ label = "", offset = "", hex = "" } = {}) {
  const node = els.tpl.content.firstElementChild.cloneNode(true);
  node.querySelector(".frag-label").value = label;
  node.querySelector(".frag-offset").value = offset;
  node.querySelector(".frag-hex").value = hex;
  node.querySelector(".frag-del").addEventListener("click", () => {
    node.remove();
    renumber();
    saveState();
  });
  node.querySelectorAll("input, textarea").forEach((inp) =>
    inp.addEventListener("input", saveState)
  );
  els.fragments.appendChild(node);
  renumber();
  return node;
}

function collectPayload() {
  const fragments = [];
  els.fragments.querySelectorAll(".fragment").forEach((node) => {
    fragments.push({
      label: node.querySelector(".frag-label").value.trim(),
      offset: node.querySelector(".frag-offset").value,
      hex: node.querySelector(".frag-hex").value,
    });
  });
  return {
    expected_length: els.expected.value,
    fragments,
  };
}

function saveState() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(collectPayload()));
  } catch (_) { /* 忽略存储失败 */ }
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (!saved) return false;
    els.expected.value = saved.expected_length ?? "";
    (saved.fragments || []).forEach((f) => addFragment(f));
    return els.fragments.children.length > 0;
  } catch (_) {
    return false;
  }
}

function clearAll() {
  els.fragments.innerHTML = "";
  els.expected.value = 0;
  els.results.classList.add("hidden");
  els.banner.classList.add("hidden");
  saveState();
}

/* ---------------- 示例 ---------------- */

const DEMO = {
  expected_length: 16,
  fragments: [
    { label: "A", offset: 0, hex: "00 01 02 03 04" },
    { label: "B", offset: 4, hex: "04 05 06 07" },        // 与 A 在偏移4同值,去重
    { label: "C", offset: 8, hex: "aa 09 0a 0b" },        // 偏移8与 D 异值冲突
    { label: "D", offset: 8, hex: "08 09 0a 0b" },        // 偏移9-11同值
    { label: "E", offset: 14, hex: "0e 0f 10 11" },       // 尾部超出预期长度 -> 溢出
  ],
};

function loadDemo() {
  els.fragments.innerHTML = "";
  els.expected.value = DEMO.expected_length;
  DEMO.fragments.forEach((f) => addFragment(f));
  saveState();
}

/* ---------------- 接口调用 ---------------- */

function validateBeforeSend(raw) {
  const expected = Number(raw.expected_length);
  if (!Number.isInteger(expected) || expected < 0) {
    throw new Error("请填写非负整数的预期字节数");
  }
  if (raw.fragments.length === 0) return;
  raw.fragments.forEach((f, i) => {
    const off = Number(f.offset);
    if (f.offset === "" || !Number.isInteger(off) || off < 0) {
      throw new Error(`第 ${i + 1} 个分片缺少有效的起始偏移`);
    }
    if (!f.hex.trim()) throw new Error(`第 ${i + 1} 个分片缺少十六进制内容`);
  });
}

async function runReassemble() {
  let raw = collectPayload();
  try {
    validateBeforeSend(raw);
  } catch (e) {
    showBanner("err", e.message);
    return;
  }
  // 转成服务端需要的数值类型
  const payload = {
    expected_length: Number(raw.expected_length),
    fragments: raw.fragments.map((f) => ({
      label: f.label,
      offset: Number(f.offset),
      hex: f.hex,
    })),
  };

  els.btnRun.disabled = true;
  try {
    const resp = await fetch("/api/reassemble", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const detail = await readError(resp);
      showBanner("err", `重组失败:${detail}`);
      return;
    }
    const result = await resp.json();
    lastPayload = payload;
    render(result);
  } catch (e) {
    showBanner("err", `请求失败:${e.message}`);
  } finally {
    els.btnRun.disabled = false;
  }
}

async function readError(resp) {
  try {
    const j = await resp.json();
    return j.detail || resp.statusText;
  } catch (_) {
    return resp.statusText;
  }
}

let lastPayload = null;

async function download() {
  if (!lastPayload) return;
  els.btnDownload.disabled = true;
  try {
    const resp = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastPayload),
    });
    if (!resp.ok) {
      showBanner("err", `下载被拒绝:${await readError(resp)}`);
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "reassembled.bin";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    showBanner("err", `下载失败:${e.message}`);
  } finally {
    els.btnDownload.disabled = false;
  }
}

/* ---------------- 结果渲染 ---------------- */

function showBanner(kind, msg) {
  els.banner.className = "banner banner-" + kind;
  els.banner.textContent = msg;
}

const nf = new Intl.NumberFormat();

function rangeText(r) {
  return `[${nf.format(r.start)}, ${nf.format(r.end)})`;
}

function renderRanges(container, ranges, kind, emptyText) {
  container.innerHTML = "";
  if (!ranges.length) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = emptyText;
    container.appendChild(d);
    return;
  }
  for (const r of ranges) {
    const item = document.createElement("div");
    item.className = "range-item ri-" + kind;

    const head = document.createElement("div");
    head.className = "range-head";
    const off = document.createElement("span");
    off.className = "range-off";
    off.textContent = rangeText(r);
    const len = document.createElement("span");
    len.className = "range-len";
    len.textContent = nf.format(r.length) + " 字节";
    head.append(off, len);
    item.appendChild(head);

    if (r.hex_preview) {
      const h = document.createElement("div");
      h.className = "range-hex";
      h.textContent = r.hex_preview + (r.length > 32 ? " …" : "");
      item.appendChild(h);
    }
    if (r.conflict_bytes) {
      const c = document.createElement("div");
      c.className = "range-sample";
      c.textContent = `其中 ${nf.format(r.conflict_bytes)} 字节为冲突字节`;
      item.appendChild(c);
    }
    if (r.sources && r.sources.length) {
      const s = document.createElement("div");
      s.className = "range-src";
      s.textContent = "涉及分片:" + r.sources.join(", ");
      item.appendChild(s);
    }
    if (r.samples && r.samples.length) {
      r.samples.forEach((line) => {
        const sm = document.createElement("div");
        sm.className = "range-sample";
        sm.textContent = line;
        item.appendChild(sm);
      });
    }
    container.appendChild(item);
  }
}

/* ---- 覆盖图 ---- */

let mapModel = null;

function subtractRange(range, cut) {
  // 从半开区间 range 中挖去 cut,返回剩余区间列表
  const out = [];
  if (cut.end <= range.start || cut.start >= range.end) return [range];
  if (cut.start > range.start) out.push({ start: range.start, end: cut.start });
  if (cut.end < range.end) out.push({ start: cut.end, end: range.end });
  return out;
}

function buildMapModel(result) {
  const exp = result.expected_length;
  const segments = [];
  if (exp > 0) {
    const conflicts = result.ranges.conflict;
    // 从每个已覆盖区间中挖去冲突字节,剩下的才是正常覆盖
    let cleanRanges = result.ranges.covered.map((r) => ({ ...r }));
    for (const c of conflicts) {
      cleanRanges = cleanRanges.flatMap((r) => subtractRange(r, c));
    }
    for (const r of cleanRanges) segments.push({ ...r, type: "clean" });
    for (const r of conflicts) segments.push({ ...r, type: "conflict" });
    for (const r of result.ranges.gap) segments.push({ ...r, type: "gap" });
    segments.sort((a, b) => a.start - b.start);
  }
  return {
    expected: exp,
    segments,
    overflow: result.ranges.overflow.map((r) => ({ ...r, type: "overflow" })),
    overflowMax: result.ranges.overflow.reduce((m, r) => Math.max(m, r.end), exp),
  };
}

const COLORS = {
  clean: "#22c55e",
  conflict: "#ef4444",
  gap: "#f59e0b",
  overflow: "#a855f7",
};
const TYPE_NAME = {
  clean: "正常覆盖",
  conflict: "冲突",
  gap: "缺口",
  overflow: "溢出",
};

function drawMap(result) {
  const canvas = els.map;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 900;
  const model0 = buildMapModel(result);
  mapModel = model0;
  const hasOverflow = model0.overflow.length > 0;
  const cssH = hasOverflow ? 84 : 52;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  canvas.style.height = cssH + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const model = model0;

  const padX = 8;
  const trackH = 22;
  const track1Y = 10;
  const track2Y = hasOverflow ? track1Y + trackH + 14 : 0;

  ctx.font = "11px monospace";
  ctx.textBaseline = "middle";

  // 轨道1:[0, expected)
  const w = cssW - padX * 2;
  ctx.fillStyle = "#0b0f18";
  ctx.fillRect(padX, track1Y, w, trackH);

  if (model.expected === 0) {
    ctx.fillStyle = "#8a97ab";
    ctx.fillText("预期长度为 0", padX + 8, track1Y + trackH / 2);
  } else {
    for (const s of model.segments) {
      const x = padX + (s.start / model.expected) * w;
      const x2 = padX + (s.end / model.expected) * w;
      ctx.fillStyle = COLORS[s.type];
      ctx.fillRect(Math.floor(x), track1Y, Math.max(1, Math.ceil(x2 - x)), trackH);
    }
    ctx.strokeStyle = "#2a3548";
    ctx.strokeRect(padX + 0.5, track1Y + 0.5, w - 1, trackH);
    ctx.fillStyle = "#8a97ab";
    ctx.fillText("0", padX, track1Y + trackH + 9);
    const label = nf.format(model.expected);
    const tw = ctx.measureText(label).width;
    ctx.fillText(label, padX + w - tw, track1Y + trackH + 9);
  }

  // 轨道2:溢出部分(坐标系延伸到实际出现过的最大偏移)
  if (hasOverflow) {
    ctx.fillStyle = "#0b0f18";
    ctx.fillRect(padX, track2Y, w, trackH);
    const span = model.overflowMax - model.expected;
    for (const s of model.overflow) {
      const x = padX + ((s.start - model.expected) / span) * w;
      const x2 = padX + ((s.end - model.expected) / span) * w;
      ctx.fillStyle = COLORS.overflow;
      ctx.fillRect(Math.floor(x), track2Y, Math.max(1, Math.ceil(x2 - x)), trackH);
    }
    ctx.strokeStyle = "#2a3548";
    ctx.strokeRect(padX + 0.5, track2Y + 0.5, w - 1, trackH);
    ctx.fillStyle = "#8a97ab";
    const l1 = nf.format(model.expected);
    ctx.fillText(l1, padX, track2Y + trackH + 9);
    const l2 = nf.format(model.overflowMax);
    const tw2 = ctx.measureText(l2).width;
    ctx.fillText(l2, padX + w - tw2, track2Y + trackH + 9);
  }
}

els.map.addEventListener("mousemove", (ev) => {
  if (!mapModel) return;
  const rect = els.map.getBoundingClientRect();
  const padX = 8;
  const w = rect.width - padX * 2;
  const x = ev.clientX - rect.left - padX;
  const trackH = 22;
  const inTrack1 = ev.clientY - rect.top >= 10 && ev.clientY - rect.top <= 10 + trackH;
  const track2Y = 10 + trackH + 14;
  const inTrack2 =
    mapModel.overflow.length > 0 &&
    ev.clientY - rect.top >= track2Y &&
    ev.clientY - rect.top <= track2Y + trackH;

  let hit = null;
  if (inTrack1 && mapModel.expected > 0) {
    const off = Math.floor((x / w) * mapModel.expected);
    hit = mapModel.segments.find((s) => off >= s.start && off < s.end);
  } else if (inTrack2) {
    const span = mapModel.overflowMax - mapModel.expected;
    const off = mapModel.expected + Math.floor((x / w) * span);
    hit = mapModel.overflow.find((s) => off >= s.start && off < s.end);
  }

  if (hit) {
    els.mapTip.textContent =
      `${TYPE_NAME[hit.type]} [${nf.format(hit.start)}, ${nf.format(hit.end)}) · ` +
      `${nf.format(hit.length)} 字节`;
    els.mapTip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 330) + "px";
    els.mapTip.style.top = ev.clientY + 14 + "px";
    els.mapTip.classList.remove("hidden");
  } else {
    els.mapTip.classList.add("hidden");
  }
});
els.map.addEventListener("mouseleave", () => els.mapTip.classList.add("hidden"));

let lastResult = null;

function render(result) {
  els.results.classList.remove("hidden");
  lastResult = result;
  const s = result.stats;

  if (result.can_download) {
    showBanner("ok", `✓ 范围完整且无冲突,已还原 ${nf.format(result.expected_length)} 字节,可以下载。`);
  } else {
    const problems = [];
    if (s.gap_bytes) problems.push(`${nf.format(s.gap_bytes)} 字节缺口`);
    if (s.conflict_bytes) problems.push(`${nf.format(s.conflict_bytes)} 字节冲突`);
    if (result.expected_length === 0) problems.push("预期长度为 0");
    showBanner("warn", "暂不可下载:" + problems.join("、"));
  }

  els.stFragments.textContent = nf.format(s.fragment_count);
  els.stCovered.textContent = nf.format(s.covered_bytes);
  els.stClean.textContent = nf.format(s.clean_bytes);
  els.stConflict.textContent = nf.format(s.conflict_bytes);
  els.stGap.textContent = nf.format(s.gap_bytes);
  els.stPercent.textContent = s.coverage_percent + "%";
  els.btnDownload.disabled = !result.can_download;

  renderRanges(els.listCovered, result.ranges.covered, "clean", "没有任何已覆盖区间");
  renderRanges(els.listConflict, result.ranges.conflict, "conflict", "没有重叠冲突");
  renderRanges(els.listGap, result.ranges.gap, "gap", "没有缺口,范围完整");
  renderRanges(els.listOverflow, result.ranges.overflow, "overflow", "没有溢出分片");

  if (result.ranges.truncated) {
    const note = document.createElement("div");
    note.className = "trunc-note";
    note.textContent = `区间过多,列表仅展示前 ${result.ranges.max_ranges} 条,覆盖图仍包含全部范围。`;
    // 附加到溢出卡片之后(简单提示一次),先清除上一轮遗留
    const old = els.listOverflow.parentElement.querySelector(".trunc-note");
    if (old) old.remove();
    els.listOverflow.parentElement.appendChild(note);
  }

  drawMap(result);
}

/* ---------------- 绑定与启动 ---------------- */

els.btnAdd.addEventListener("click", () => addFragment());
els.btnDemo.addEventListener("click", loadDemo);
els.btnClear.addEventListener("click", clearAll);
els.btnRun.addEventListener("click", runReassemble);
els.btnDownload.addEventListener("click", download);
els.expected.addEventListener("input", saveState);
let resizeTimer = null;
window.addEventListener("resize", () => {
  if (!lastResult) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => drawMap(lastResult), 150);
});

if (!loadState()) {
  loadDemo();
}
