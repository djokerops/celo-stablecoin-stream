"use strict";

/* config */

const LOCAL_HOSTS = ["localhost", "127.0.0.1", ""];
const API_BASE = LOCAL_HOSTS.includes(location.hostname)
  ? "http://localhost:8000"
  : "https://api.celoflow.stream";
const MINUTES  = 60;
const REFRESH  = 30000;

/* Per-section time range, in hours. Each of these four sections carries its own
   1H/24H selector, so they are tracked independently and every loader reads its
   own key at call time — a refresh tick never resets what the user picked. */
/* Defaults. The KPI row and the receivers table open on 1H, not 24H, because
   a 24h window scans ~7.8M rows on the VM (18-26s) against ~500K for an hour
   (1-3s) - and those two are what the reader looks at first, so they are the
   ones that must not keep the page waiting. The remaining three keep 24H:
   a share-of-volume breakdown is more meaningful over a full day, and they
   sit below the fold where their latency is less costly. Every selector is
   still one click from the other range. */
const RANGES = { top: 1, symbol: 24, peg: 24, txtype: 24, recv: 1 };

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const SANS = '"Archivo", system-ui, sans-serif';
const MONO = '"IBM Plex Mono", ui-monospace, monospace';

/* ═══ theme 
   */
const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function theme() {
  return {
    surface: cssVar("--surface"),
    grid:    cssVar("--grid"),
    axis:    cssVar("--axis"),
    ink:     cssVar("--ink"),
    ink2:    cssVar("--ink-2"),
    ink3:    cssVar("--ink-3"),
    total:   cssVar("--series-total"),
    count:   cssVar("--series-count"),
    ttBg:    cssVar("--tt-bg"),
    hairline: cssVar("--hairline"),
  };
}

/* --palette is a quoted comma list in CSS; strip the quotes and split. */
function palette() {
  return cssVar("--palette").replace(/^["']|["']$/g, "").split(",").map(s => s.trim());
}

/* Fixed slot assignment. Busiest symbols first so the most-seen series get the
   leading, best-separated colours. Every token the producer streams is here.
   Colour follows the SYMBOL, never its rank — filtering or a symbol going quiet
   never repaints the survivors — and the slot index is identical in both
   themes, so a coin keeps its identity when the theme flips. */
const SYMBOL_ORDER = [
  "USDT","USDC","USDm","BRLA","USDGLO","cNGN","GHSm","COPm","EURm","KESm",
  "XOFm","BRLm","PHPm","wBRL","wARS","NGNm","GBPm","JPYm","ZARm","AUDm",
  "CADm","CHFm","VCHF","VGBP","USDM","wMXN","wCOP","wPEN","wCLP"
];
const SLOT = new Map(SYMBOL_ORDER.map((s, i) => [s, i]));
/* An unknown symbol gets neutral grey, never a generated or recycled hue —
   a 30th colour would be indistinguishable from one already in play. */
const colorFor = (sym) => SLOT.has(sym) ? palette()[SLOT.get(sym)] : cssVar("--unknown");
const rankOf   = (sym) => SLOT.has(sym) ? SLOT.get(sym) : 999;

/* Transaction types get the same treatment as symbols: a fixed slot per
   category, so a type keeps its colour across refreshes, across the two panels
   that draw it, and across a theme flip. Slots run 0..n from the top of the
   palette, which is the order the adjacency validation was run in — the stack
   below is pinned to this order, so its on-screen neighbours are exactly the
   validated pairs.

   Ordered by expected share, busiest first. The producer classifies by
   counterparty label and falls back to "p2p"; mint and burn are listed because
   classify() can emit them, though both endpoints filter zero/dead addresses
   out before grouping, so in practice they never arrive.

   The two CEX entries are one address list read in both directions: out of a
   hot wallet is a withdrawal, into one is a sweep or rebalance. Ramps are not
   split that way — on- and off-ramp both report as "Ramp". */
const TX_TYPES = [
  ["p2p",            "P2P"],
  ["ramping",        "Ramp"],
  ["dex_swap",       "DEX Swap"],
  ["cex_withdrawal", "CEX Withdrawal"],
  ["cex_rebalance",  "CEX Rebalance"],
  ["lending",        "Lending"],
  ["bridging",       "Bridging"],
  ["mint",           "Mint"],
  ["burn",           "Burn"],
];
/* Rows written before the producer started labelling carry tx_type = "". They
   are real volume and can't be dropped, so they get an explicit name and the
   same neutral grey an unrecognised symbol gets — never a palette hue, which
   would read as a category of its own. */
const TX_UNLABELED = "Unlabeled";
const TX_LABEL = new Map(TX_TYPES);
const TX_SLOT  = new Map(TX_TYPES.map(([, label], i) => [label, i]));

/* Which run of the palette the types draw from. Symbols start at 0; these start
   at 7, and the offset is measured, not taste.

   The palette guarantees a floor only for ADJACENT slots, but a pie ranks by
   value, so ANY two types can end up touching — all 36 pairs have to hold up,
   not just the nine consecutive ones. Scoring every 9-slot window on its worst
   pair in the worse theme, under CIEDE2000 (CIE76 is unusable here: it rates
   blues as far apart when they aren't, and picks a window of near-identical
   blues):

     slots  0-8   ΔE00  5.8   ← DEX Swap vs CEX Rebalance, both olive
     slots 14-22  ΔE00  6.6
     slots  7-15  ΔE00 10.4   ← chosen

   Consecutive on purpose: the stack is pinned to slot order, so its on-screen
   neighbours stay the pairs the palette validated. Rescore this if TX_TYPES
   ever grows past nine. */
const TX_SLOT_OFFSET = 7;

const txLabel = (key) => TX_LABEL.get(key) || (key || TX_UNLABELED);
const txColor = (label) =>
  TX_SLOT.has(label) ? palette()[TX_SLOT.get(label) + TX_SLOT_OFFSET] : cssVar("--unknown");
const txRank  = (label) => TX_SLOT.has(label) ? TX_SLOT.get(label) : 999;

/* ═══ formatting ═══════════════════════════════════════════════════════════ */
const nf = new Intl.NumberFormat(undefined);
const fmtNum = (n) => nf.format(Math.round(Number(n) || 0));
function fmtUsd(n) {
  n = Number(n) || 0;
  const a = Math.abs(n);
  if (a >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
  return "$" + n.toFixed(a < 10 ? 2 : 0);
}
const fmtUsdFull = (n) => "$" + nf.format(Math.round(Number(n) || 0));
/* The API sends a naive UTC stamp ("2026-09-13T17:48:00" — no Z, no offset).
   These digits are read straight out of the string rather than through
   new Date(), deliberately: a Date parse would read a naive string as the
   VIEWER's local time, and the axis would then say something different in
   Lagos than in New York for the same bar. Everyone sees the same UTC wall
   clock, which is what keeps a shared chart comparable between people. */
const hhmm = (iso) => {
  const h = +iso.slice(11, 13);
  return `${h % 12 || 12}:${iso.slice(14, 16)} ${h < 12 ? "AM" : "PM"}`;
};
const shortAddr = (a) => a.slice(0, 10) + "…" + a.slice(-8);

async function getJSON(path) {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(path + " → HTTP " + r.status);
  return r.json();
}

/* ═══ pivot: long rows (minute, series, value) → aligned wide series ════════
   Zero-fills every minute a series is absent, so the stack has no holes and
   the x-axis is shared across all four panels. keyFn maps a row to its series
   name, which is also what the colour and rank lookups key off — that lets the
   tx_type panels pivot on a different column and a different slot table
   without a second copy of this. */
function pivot(rows, valueKey, keyFn = (r) => r.symbol, rank = rankOf) {
  const minutes = [...new Set(rows.map(r => r.minute))].sort();
  const mIdx = new Map(minutes.map((m, i) => [m, i]));
  const bySeries = new Map();
  for (const r of rows) {
    const k = keyFn(r);
    if (!bySeries.has(k)) bySeries.set(k, new Array(minutes.length).fill(0));
    bySeries.get(k)[mIdx.get(r.minute)] = Number(r[valueKey]) || 0;
  }
  // stack bottom→top in fixed slot order (validated adjacency)
  const names = [...bySeries.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  return { minutes, names, data: names.map(n => bySeries.get(n)) };
}

/* ═══ shared chart chrome ══════════════════════════════════════════════════ */
const axisCommon = (t, fmt) => ({
  xAxis: {
    type: "category",
    axisLine:  { lineStyle: { color: t.axis } },
    axisTick:  { show: false },
    axisLabel: { color: t.ink3, fontFamily: MONO, fontSize: 10.5, margin: 10 },
    splitLine: { show: false },
  },
  yAxis: {
    type: "value",
    axisLine:  { show: false },
    axisTick:  { show: false },
    axisLabel: { color: t.ink3, fontFamily: MONO, fontSize: 10.5, formatter: fmt, margin: 12 },
    // solid hairlines, one step off the surface — never dashed
    splitLine: { lineStyle: { color: t.grid, width: 1, type: "solid" } },
  },
});

const tooltipBase = (t) => ({
  backgroundColor: t.ttBg,
  borderColor: t.hairline,
  borderWidth: 1,
  padding: [10, 12],
  extraCssText: "border-radius:8px; box-shadow:0 8px 28px rgba(0,0,0,.35);",
  textStyle: { color: t.ink, fontFamily: MONO, fontSize: 11.5 },
});

const swatch = (c) => `<span style="display:inline-block;width:9px;height:9px;border-radius:2.5px;background:${c};margin-right:7px;vertical-align:middle"></span>`;
const ttHead = (t, s) => `<div style="color:${t.ink3};font-size:10px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:7px">${s}</div>`;

/* Time headers carry the zone; plain ttHead stays generic. The axis is UTC for
   every visitor (see hhmm), and that is stated here — in the panel the reader
   opens deliberately — rather than on the axis, where it would cost horizontal
   room on all sixty ticks to say the same thing sixty times. */
const ttTime = (t, label) => ttHead(t, `${label} UTC`);

/* Shared by both stacked forms: series ranked by value at the hovered minute,
   capped, with the column total underneath. */
function stackedTooltip(t, fmtVal) {
  return (ps) => {
    const live = ps.filter(p => p.value > 0).sort((a, b) => b.value - a.value);
    const total = ps.reduce((acc, p) => acc + (p.value || 0), 0);
    if (!live.length) return ttTime(t, ps[0].axisValue) + `<span style="color:${t.ink3}">no payments</span>`;
    const rows = live.slice(0, 14).map(p =>
      `<div style="display:flex;gap:18px;justify-content:space-between;line-height:1.75">
         <span>${swatch(p.color)}<span style="color:${t.ink2}">${p.seriesName}</span></span>
         <span style="color:${t.ink};font-variant-numeric:tabular-nums">${fmtVal(p.value)}</span>
       </div>`).join("");
    const more = live.length > 14 ? `<div style="color:${t.ink3};margin-top:4px">+${live.length - 14} more</div>` : "";
    return ttTime(t, ps[0].axisValue) + rows + more +
      `<div style="margin-top:8px;padding-top:7px;border-top:1px solid ${t.hairline};
            display:flex;gap:18px;justify-content:space-between">
         <span style="color:${t.ink3}">Total</span>
         <span style="color:${t.ink};font-variant-numeric:tabular-nums">${fmtVal(total)}</span>
       </div>`;
  };
}

/* ═══ stacked area (USD volume per minute, per transaction type) */
function stackedAreaOption(piv, fmtAxis, fmtVal, colorOf) {
  const t = theme();
  const { minutes, names, data } = piv;

  const series = names.map((name, i) => {
    const c = colorOf(name);
    return {
      name, type: "line", stack: "total",
      smooth: .2, smoothMonotone: "x",
      symbol: "circle", symbolSize: 7, showSymbol: false,
      emphasis: { focus: "series" },
      // 1px of surface between bands — the same separator the bars use, drawn
      // as the band's own top edge instead of a border box.
      lineStyle: { width: 1, color: t.surface },
      itemStyle: { color: c },
      areaStyle: { color: c, opacity: 1 },
      data: data[i],
    };
  });

  return {
    animation: !REDUCED,
    animationDuration: 420,
    grid: { left: 4, right: 24, top: 14, bottom: 2, containLabel: true },
    tooltip: {
      ...tooltipBase(t), trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } },
      formatter: stackedTooltip(t, fmtVal),
    },
    legend: { show: false },           // custom HTML legend drives selection
    ...axisCommon(t, fmtAxis),
    xAxis: { ...axisCommon(t, fmtAxis).xAxis, data: minutes.map(hhmm), boundaryGap: false },
    series,
  };
}

/* ═══ stacked bar (payment count / USD volume, per stablecoin) ══════════════ */
function stackedBarOption(piv, fmtAxis, fmtVal) {
  const t = theme();
  const { minutes, names, data } = piv;

  /* Round only the cap of each column, not every segment: rounding an interior
     segment would read as a gap in the stack that isn't in the data. */
  const topAt = minutes.map((_, i) => {
    for (let s = names.length - 1; s >= 0; s--) if (data[s][i] > 0) return s;
    return -1;
  });

  const series = names.map((name, s) => ({
    name, type: "bar", stack: "total",
    barMaxWidth: 22,
    emphasis: { focus: "series" },
    // 1px surface-coloured border per segment → a 2px surface gap between
    // neighbours. White doing the separating, not a stroke around the mark.
    itemStyle: { color: colorFor(name), borderColor: t.surface, borderWidth: 1 },
    data: data[s].map((v, i) => (
      topAt[i] === s ? { value: v, itemStyle: { borderRadius: [3, 3, 0, 0] } } : v
    )),
  }));

  return {
    animation: !REDUCED,
    animationDuration: 420,
    // right margin ~= half a time label, so the final tick isn't clipped by the
    // plot edge on narrow screens (containLabel doesn't cover the last category)
    grid: { left: 4, right: 24, top: 14, bottom: 2, containLabel: true },
    tooltip: {
      ...tooltipBase(t), trigger: "axis",
      axisPointer: { type: "shadow", shadowStyle: { color: t.hairline } },
      formatter: stackedTooltip(t, fmtVal),
    },
    legend: { show: false },           // custom HTML legend drives selection
    ...axisCommon(t, fmtAxis),
    xAxis: { ...axisCommon(t, fmtAxis).xAxis, data: minutes.map(hhmm) },
    series,
  };
}

/* ═══ single-series area line (totals — not segmented) ══════════════════════ */
function areaLineOption(labels, values, color, fmtAxis, fmtVal, seriesName) {
  const t = theme();
  return {
    animation: !REDUCED,
    animationDuration: 420,
    grid: { left: 4, right: 24, top: 14, bottom: 2, containLabel: true },
    tooltip: {
      ...tooltipBase(t), trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } },
      formatter(ps) {
        const p = ps[0];
        return ttTime(t, p.axisValue) +
          `<div style="display:flex;gap:20px;justify-content:space-between">
             <span>${swatch(color)}<span style="color:${t.ink2}">${seriesName}</span></span>
             <span style="color:${t.ink};font-variant-numeric:tabular-nums">${fmtVal(p.value)}</span>
           </div>`;
      },
    },
    ...axisCommon(t, fmtAxis),
    xAxis: { ...axisCommon(t, fmtAxis).xAxis, data: labels, boundaryGap: false },
    series: [{
      name: seriesName, type: "line", data: values,
      smooth: .3, symbol: "circle", symbolSize: 8, showSymbol: false,
      lineStyle: { width: 2, color },
      itemStyle: { color, borderColor: t.surface, borderWidth: 2 },  // 2px surface ring
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: color + "2e" },   // ~18% at the top
          { offset: 1, color: color + "00" },
        ]),
      },
    }],
  };
}

/* ═══ custom legend — click toggles, hover isolates ════════════════════════ */
function renderLegend(host, chart, names, colorOf = colorFor) {
  host.innerHTML = "";
  for (const name of names) {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("aria-pressed", "true");
    b.innerHTML = `<span class="sw" style="background:${colorOf(name)}"></span>${name}`;
    b.addEventListener("click", () => {
      const on = b.getAttribute("aria-pressed") === "true";
      b.setAttribute("aria-pressed", String(!on));
      chart.dispatchAction({ type: on ? "legendUnSelect" : "legendSelect", name });
    });
    b.addEventListener("mouseenter", () => chart.dispatchAction({ type: "highlight", seriesName: name }));
    b.addEventListener("mouseleave", () => chart.dispatchAction({ type: "downplay", seriesName: name }));
    host.appendChild(b);
  }
}

/* ═══ table views — every value readable without hovering ══════════════════ */
function renderPivotTable(host, piv, fmtVal, firstCol, colorOf = colorFor) {
  const { minutes, names, data } = piv;
  const head = `<tr><th>${firstCol}</th>${names.map(n =>
    `<th><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${colorOf(n)};margin-right:5px"></span>${n}</th>`
  ).join("")}<th>Total</th></tr>`;
  const body = minutes.map((m, i) => {
    let tot = 0;
    const cells = names.map((_, s) => {
      const v = data[s][i]; tot += v;
      return `<td class="${v ? "" : "zero"}">${v ? fmtVal(v) : "·"}</td>`;
    }).join("");
    return `<tr><td>${hhmm(m)}</td>${cells}<td class="v">${fmtVal(tot)}</td></tr>`;
  }).join("");
  host.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderSeriesTable(host, labels, values, fmtVal, firstCol, valCol) {
  const body = labels.map((l, i) =>
    `<tr><td>${l}</td><td class="v">${fmtVal(values[i])}</td></tr>`).join("");
  host.innerHTML = `<table><thead><tr><th>${firstCol}</th><th>${valCol}</th></tr></thead><tbody>${body}</tbody></table>`;
}

/* ═══ chart registry ═══════════════════════════════════════════════════════ */
const charts = {};
function chartAt(id) {
  if (!charts[id]) {
    charts[id] = echarts.init(document.getElementById(id), null, { renderer: "canvas" });
    window.addEventListener("resize", () => charts[id].resize());
  }
  return charts[id];
}

const busy = (id, on) => document.getElementById(id).classList.toggle("loading", on);

/* Below this width the pie's legend moves under the chart and the bar labels
   step down a size. Kept in JS because ECharts lays out on a canvas and can't
   read the CSS media query. */
const NARROW_AT = 620;
const isNarrow = () => window.innerWidth < NARROW_AT;

/* ═══ data cache ═══════════════════════════════════════════════════════════
   Raw API rows are kept so a theme flip can re-render every panel in the new
   colours without refetching — no network round-trip, no visible reload. */
const CACHE = {};

/* ═══ render (pure — reads CACHE + current theme) ══════════════════════════ */
function renderSummary() {
  const s = CACHE.summary; if (!s) return;
  document.querySelectorAll("#p-top .rng").forEach(el => el.textContent = `· ${RANGES.top}h`);
  document.getElementById("s-volume").textContent    = fmtUsd(s.total_usd_volume || 0);
  document.getElementById("s-senders").textContent   = fmtNum(s.unique_senders || 0);
  document.getElementById("s-transfers").textContent = fmtNum(s.transfer_count || 0);
  document.getElementById("s-coins").textContent     = fmtNum(s.active_coins || 0);
}

function renderSenders() {
  const rows = CACHE.senders; if (!rows) return;
  const labels = rows.map(r => hhmm(r.minute));
  const vals   = rows.map(r => Number(r.senders) || 0);
  chartAt("c-senders").setOption(
    areaLineOption(labels, vals, theme().total, fmtNum, fmtNum, "Unique senders"), true);
  renderSeriesTable(document.getElementById("t-senders"), labels, vals, fmtNum, "Minute", "Senders");
}

function renderPayments() {
  const rows = CACHE.payments; if (!rows) return;
  const labels = rows.map(r => hhmm(r.minute));
  const vals   = rows.map(r => Number(r.payments) || 0);
  chartAt("c-payments").setOption(
    areaLineOption(labels, vals, theme().count, fmtNum, fmtNum, "Payments"), true);
  renderSeriesTable(document.getElementById("t-payments"), labels, vals, fmtNum, "Minute", "Payments");
}

function renderPaySym() {
  const rows = CACHE.paysym; if (!rows) return;
  const piv = pivot(rows, "payments");
  const c = chartAt("c-paysym");
  c.setOption(stackedBarOption(piv, fmtNum, fmtNum), true);
  renderLegend(document.getElementById("l-paysym"), c, piv.names);
  renderPivotTable(document.getElementById("t-paysym"), piv, fmtNum, "Minute");
}

function renderVolSym() {
  const rows = CACHE.volsym; if (!rows) return;
  const piv = pivot(rows, "usd_volume");
  const c = chartAt("c-volsym");
  c.setOption(stackedBarOption(piv, fmtUsd, fmtUsdFull), true);
  renderLegend(document.getElementById("l-volsym"), c, piv.names);
  renderPivotTable(document.getElementById("t-volsym"), piv, fmtUsd, "Minute");
}

/* 24h volume by stablecoin — ranked horizontal bars, each in its own fixed hue */
function renderBySymbol() {
  if (!CACHE.bysymbol) return;
  const t = theme(), narrow = isNarrow();
  const rows = CACHE.bysymbol.slice().sort((a, b) => a.usd_volume - b.usd_volume); // y-axis runs bottom-up
  chartAt("c-symbol").setOption({
    animation: !REDUCED,
    grid: { left: 4, right: narrow ? 44 : 56, top: 8, bottom: 2, containLabel: true },
    tooltip: {
      ...tooltipBase(t), trigger: "item",
      formatter: (p) => `${swatch(colorFor(p.name))}<span style="color:${t.ink2}">${p.name}</span>
        <span style="color:${t.ink};margin-left:14px">${fmtUsdFull(p.value)}</span>`,
    },
    xAxis: {
      type: "value", axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: t.ink3, fontFamily: MONO, fontSize: narrow ? 9.5 : 10.5,
                   formatter: fmtUsd, hideOverlap: true },
      splitLine: { lineStyle: { color: t.grid, type: "solid" } },
    },
    yAxis: {
      type: "category", data: rows.map(r => r.symbol),
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: t.ink2, fontFamily: MONO, fontSize: narrow ? 10 : 11 },
      splitLine: { show: false },
    },
    series: [{
      type: "bar", barMaxWidth: 16,
      data: rows.map(r => ({ value: r.usd_volume, itemStyle: { color: colorFor(r.symbol) } })),
      itemStyle: { borderRadius: [0, 4, 4, 0] },   // rounded data-end, square at baseline
      // one label per bar is the point of a ranked bar chart: it IS the value axis
      label: { show: true, position: "right", color: t.ink2, fontFamily: MONO,
               fontSize: narrow ? 9.5 : 10.5, formatter: (p) => fmtUsd(p.value) },
    }],
  }, true);
}

/* ═══ share pie (volume split — peg currency, transaction type) ════════════
   items: [{ name, value }] in the order the API ranked them. colorOf receives
   (name, index) so a caller can colour by rank or by a fixed slot table. */
function sharePieOption(items, colorOf) {
  const t = theme(), narrow = isNarrow();
  const total = items.reduce((acc, r) => acc + Number(r.value || 0), 0) || 1;
  const legendBase = {
    type: "scroll", itemWidth: 9, itemHeight: 9, icon: "roundRect",
    textStyle: { color: t.ink2, fontFamily: MONO, fontSize: 11 },
    pageTextStyle: { color: t.ink3 }, pageIconColor: t.ink3, pageIconInactiveColor: t.axis,
  };
  return {
    animation: !REDUCED,
    tooltip: {
      ...tooltipBase(t), trigger: "item",
      formatter: (p) => `${swatch(p.color)}<span style="color:${t.ink2}">${p.name}</span>
        <span style="color:${t.ink};margin-left:14px">${fmtUsdFull(p.value)}</span>
        <span style="color:${t.ink3};margin-left:8px">${(p.value / total * 100).toFixed(1)}%</span>`,
    },
    /* On a narrow screen a right-hand legend squeezes the pie to nothing, so it
       moves under the chart and runs horizontally instead. */
    legend: narrow
      ? { ...legendBase, orient: "horizontal", bottom: 0, left: "center", itemGap: 12 }
      : { ...legendBase, orient: "vertical", right: 4, top: "middle", itemGap: 9 },
    series: [{
      type: "pie",
      radius: narrow ? "62%" : "74%",
      center: narrow ? ["50%", "44%"] : ["38%", "50%"],
      avoidLabelOverlap: true, label: { show: false }, labelLine: { show: false },
      // 2px surface gap doing the separating between slices. No borderRadius on a
      // solid pie — rounding notches the slices where they meet at the centre.
      itemStyle: { borderColor: t.surface, borderWidth: 2 },
      emphasis: { scale: true, scaleSize: 4 },
      data: items.map((r, i) => ({
        name: r.name, value: r.value, itemStyle: { color: colorOf(r.name, i) },
      })),
    }],
  };
}

function renderByPeg() {
  if (!CACHE.bypeg) return;
  const pal = palette();
  const items = CACHE.bypeg.map(r => ({ name: r.peg, value: r.usd_volume }));
  chartAt("c-peg").setOption(sharePieOption(items, (_name, i) => pal[i % pal.length]), true);
}

/* Volume per minute by transaction type — the shape of the mix over the hour. */
function renderVolType() {
  const rows = CACHE.voltype; if (!rows) return;
  const piv = pivot(rows, "usd_volume", (r) => txLabel(r.tx_type), txRank);
  const c = chartAt("c-voltype");
  c.setOption(stackedAreaOption(piv, fmtUsd, fmtUsdFull, txColor), true);
  renderLegend(document.getElementById("l-voltype"), c, piv.names, txColor);
  renderPivotTable(document.getElementById("t-voltype"), piv, fmtUsd, "Minute", txColor);
}

/* The same split as a share of the window — colour is slot-fixed here, not
   rank-driven, so a type holds its hue when the 1H/24H ranking reshuffles. */
function renderByTxType() {
  if (!CACHE.bytxtype) return;
  const items = CACHE.bytxtype.map(r => ({ name: txLabel(r.tx_type), value: r.usd_volume }));
  chartAt("c-txtype").setOption(sharePieOption(items, txColor), true);
}

function renderReceivers() {
  const rows = CACHE.receivers; if (!rows) return;
  document.querySelector("#receivers tbody").innerHTML = rows.length ? rows.map(r =>
    `<tr><td class="addr" title="${r.receiver}">${shortAddr(r.receiver)}</td>` +
    `<td class="v">${fmtUsdFull(r.received_usd)}</td>` +
    `<td>${fmtNum(r.transfers)}</td></tr>`).join("")
    : `<tr><td colspan="3" class="empty">No payments in this window</td></tr>`;
}

/* ═══ load (fetch → cache → render) ════════════════════════════════════════ */
async function loadSummary() {
  busy("p-top", true);
  CACHE.summary = await getJSON(`/api/summary?hours=${RANGES.top}`);
  renderSummary();
  busy("p-top", false);
}

/* Unique senders per minute. One global series: an address that sends several
   different stablecoins in a minute is ONE sender, deduplicated server-side by
   uniqExact. Not segmented, so no legend — the title says what is plotted. */
async function loadSenders() {
  busy("p-senders", true);
  CACHE.senders = await getJSON(`/api/senders-per-minute?minutes=${MINUTES}`);
  renderSenders();
  busy("p-senders", false);
}

async function loadPayments() {
  busy("p-payments", true);
  CACHE.payments = await getJSON(`/api/payments-per-minute?minutes=${MINUTES}`);
  renderPayments();
  busy("p-payments", false);
}

async function loadPaySym() {
  busy("p-paysym", true);
  CACHE.paysym = await getJSON(`/api/payments-per-minute-by-symbol?minutes=${MINUTES}`);
  renderPaySym();
  busy("p-paysym", false);
}

async function loadVolSym() {
  busy("p-volsym", true);
  CACHE.volsym = await getJSON(`/api/volume-by-symbol-per-minute?minutes=${MINUTES}`);
  renderVolSym();
  busy("p-volsym", false);
}

async function loadBySymbol() {
  busy("p-symbol", true);
  CACHE.bysymbol = await getJSON(`/api/volume-by-symbol?hours=${RANGES.symbol}&limit=12`);
  renderBySymbol();
  busy("p-symbol", false);
}

async function loadByPeg() {
  busy("p-peg", true);
  CACHE.bypeg = await getJSON(`/api/volume-by-peg?hours=${RANGES.peg}`);
  renderByPeg();
  busy("p-peg", false);
}

async function loadVolType() {
  busy("p-voltype", true);
  CACHE.voltype = await getJSON(`/api/volume-by-tx-type-per-minute?minutes=${MINUTES}`);
  renderVolType();
  busy("p-voltype", false);
}

async function loadByTxType() {
  busy("p-txtype", true);
  CACHE.bytxtype = await getJSON(`/api/volume-by-tx-type?hours=${RANGES.txtype}`);
  renderByTxType();
  busy("p-txtype", false);
}

async function loadReceivers() {
  busy("p-recv", true);
  CACHE.receivers = await getJSON(`/api/top-receivers?hours=${RANGES.recv}&limit=15`);
  renderReceivers();
  busy("p-recv", false);
}

const RENDERERS = [renderSummary, renderSenders, renderPayments, renderPaySym,
                   renderVolSym, renderBySymbol, renderByPeg, renderByTxType,
                   renderVolType, renderReceivers];

/* ═══ theme toggle ═════════════════════════════════════════════════════════ */
const themeGroup = document.getElementById("theme-toggle");

function currentTheme() {
  return document.documentElement.dataset.theme ||
    (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
}
function markThemeButtons() {
  themeGroup.querySelectorAll("button").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.theme === currentTheme())));
}
function applyTheme(name) {
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem("theme", name); } catch (e) { /* private mode */ }
  markThemeButtons();
  // custom properties resolve on the next frame; re-render after they land
  requestAnimationFrame(() => RENDERERS.forEach(fn => { try { fn(); } catch (e) { console.error(e); } }));
}
themeGroup.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-theme]");
  if (b && b.dataset.theme !== currentTheme()) applyTheme(b.dataset.theme);
});
/* Follow the OS while the user hasn't expressed a preference of their own. */
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (!document.documentElement.dataset.theme) {
    markThemeButtons();
    requestAnimationFrame(() => RENDERERS.forEach(fn => { try { fn(); } catch (e) {} }));
  }
});
markThemeButtons();

/* ═══ chart / table toggles ════════════════════════════════════════════════ */
document.querySelectorAll(".toggle.view").forEach(group => {
  group.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-view]");
    if (!btn) return;
    const panel = btn.closest(".panel");
    panel.classList.toggle("as-table", btn.dataset.view === "table");
    group.querySelectorAll("button").forEach(b =>
      b.setAttribute("aria-pressed", String(b === btn)));
    if (btn.dataset.view === "chart") {
      const c = panel.querySelector(".chart");
      if (c && charts[c.id]) charts[c.id].resize();
    }
  });
});

/* ═══ 1H / 24H range selectors ═════════════════════════════════════════════ */
const LOADERS = { top: loadSummary, symbol: loadBySymbol, peg: loadByPeg,
                  txtype: loadByTxType, recv: loadReceivers };
document.querySelectorAll(".toggle.range").forEach(group => {
  group.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-hours]");
    if (!btn) return;
    const key = group.dataset.target;
    const hours = Number(btn.dataset.hours);
    if (RANGES[key] === hours) return;
    RANGES[key] = hours;
    group.querySelectorAll("button").forEach(b =>
      b.setAttribute("aria-pressed", String(b === btn)));
    LOADERS[key]().catch(err => console.error(`${key} reload failed:`, err));
  });
});

/* Re-render the width-sensitive charts only when the breakpoint is actually
   crossed — resize fires continuously and these two do a full setOption. */
let wasNarrow = isNarrow(), resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (isNarrow() === wasNarrow) return;
    wasNarrow = isNarrow();
    renderByPeg();
    renderByTxType();
    renderBySymbol();
  }, 180);
});

/* ═══ refresh loop ═════════════════════════════════════════════════════════ */

async function refreshAll() {
  const jobs = [loadSummary, loadSenders, loadPayments, loadPaySym,
                loadVolSym, loadBySymbol, loadByPeg, loadByTxType,
                loadVolType, loadReceivers];
  const results = await Promise.allSettled(jobs.map(fn => fn()));
  const failed = results.filter(r => r.status === "rejected");

  /* Failures stay in the console, not on the page. A fetch that misses is
     self-correcting — the next tick is 30s out and the panels keep showing the
     last good data — so a red banner in front of every visitor overstates a
     blip the board already recovers from on its own. The #err element and its
     styles are still in place if this ever needs turning back on. */
  if (failed.length) {
    console.error(`refresh: ${failed.length}/${jobs.length} panels failed`,
                  failed.map(f => f.reason));
  }

  /* The stamp is the only remaining freshness signal */
  if (failed.length < jobs.length) {
    document.getElementById("stamp").textContent =
      "Updated " + new Date().toLocaleTimeString(undefined,
        { hour12: true, timeZone: "UTC" }) + " UTC";
  }
}

refreshAll();
setInterval(refreshAll, REFRESH);
