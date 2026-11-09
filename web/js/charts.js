// Graphiques SVG autonomes (aucune librairie, aucun canvas).

import { escapeHtml, fmtNumber } from "./utils.js";

const W = 760;
const M = { top: 16, right: 14, bottom: 30, left: 42 };

/**
 * Courbes multi-séries.
 * series: [{ key, label, color, values: (number|null)[], dashed?, area? }]
 */
export function lineChart({
  series = [],
  labels = [],
  height = 240,
  unit = "°C",
  yMin = null,
  yMax = null,
  showPoints = false,
} = {}) {
  const visible = series.filter((item) => Array.isArray(item.values) && item.values.length);
  const all = visible.flatMap((item) => item.values).filter((value) => Number.isFinite(value));

  if (!all.length) {
    return `<div class="empty" style="padding:28px 12px">
      <p>Aucune mesure sur cette période.</p>
    </div>`;
  }

  const H = height;
  const innerW = W - M.left - M.right;
  const innerH = H - M.top - M.bottom;

  let min = yMin === null ? Math.min(...all) : yMin;
  let max = yMax === null ? Math.max(...all) : yMax;
  if (max - min < 2) {
    const center = (max + min) / 2;
    min = center - 1;
    max = center + 1;
  }
  const padding = (max - min) * 0.12;
  min -= padding;
  max += padding;

  const count = Math.max(labels.length, ...visible.map((item) => item.values.length), 2);
  const x = (index) => M.left + (innerW * index) / Math.max(1, count - 1);
  const y = (value) => M.top + innerH - ((value - min) / (max - min)) * innerH;

  const gridCount = 4;
  let grid = "";
  for (let i = 0; i <= gridCount; i += 1) {
    const value = min + ((max - min) * i) / gridCount;
    const py = y(value);
    grid += `<line x1="${M.left}" y1="${py.toFixed(1)}" x2="${W - M.right}" y2="${py.toFixed(1)}" stroke="var(--line)" stroke-width="1" stroke-dasharray="${i === 0 ? "0" : "3 5"}" opacity="0.8"/>`;
    grid += `<text x="${M.left - 8}" y="${(py + 3.5).toFixed(1)}" text-anchor="end" font-size="10.5" fill="var(--faint)">${fmtNumber(value, 0)}${i === gridCount ? unit : ""}</text>`;
  }

  const step = Math.max(1, Math.ceil(count / 8));
  let xLabels = "";
  for (let i = 0; i < count; i += step) {
    const label = labels[i];
    if (label === undefined) continue;
    xLabels += `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="10.5" fill="var(--faint)">${escapeHtml(label)}</text>`;
  }

  let paths = "";
  let points = "";

  for (const item of visible) {
    const color = item.color || "var(--primary)";
    const segments = [];
    let current = [];

    item.values.forEach((value, index) => {
      if (Number.isFinite(value)) {
        current.push({ x: x(index), y: y(value) });
      } else if (current.length) {
        segments.push(current);
        current = [];
      }
    });
    if (current.length) segments.push(current);

    for (const segment of segments) {
      if (segment.length === 1) {
        paths += `<circle cx="${segment[0].x.toFixed(1)}" cy="${segment[0].y.toFixed(1)}" r="2.6" fill="${color}"/>`;
        continue;
      }

      if (item.area !== false && item.key === visible[0].key) {
        const first = segment[0].x.toFixed(1);
        const last = segment[segment.length - 1].x.toFixed(1);
        const base = y(Math.max(min, Math.min(max, min)));
        paths += `<path d="M ${first} ${base} L ${segment.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" L ")} L ${last} ${base} Z" fill="${color}" opacity="0.09"/>`;
      }

      let d;
      if (item.step) {
        // Style escalier : la consigne tient d'un point au suivant.
        d = `M ${segment[0].x.toFixed(1)} ${segment[0].y.toFixed(1)}`;
        for (let i = 1; i < segment.length; i += 1) {
          d += ` H ${segment[i].x.toFixed(1)} V ${segment[i].y.toFixed(1)}`;
        }
      } else {
        d = `M ${segment.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" L ")}`;
      }
      paths += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" ${item.dashed ? 'stroke-dasharray="5 5"' : ""}/>`;
    }

    if (showPoints) {
      item.values.forEach((value, index) => {
        if (!Number.isFinite(value)) return;
        points += `<circle cx="${x(index).toFixed(1)}" cy="${y(value).toFixed(1)}" r="2.4" fill="${color}"><title>${escapeHtml(item.label)} : ${fmtNumber(value, 1)} ${unit}</title></circle>`;
      });
    }
  }

  return `<div class="chart-wrap">
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Graphique des températures">
      ${grid}
      ${paths}
      ${points}
      ${xLabels}
    </svg>
  </div>`;
}

export function chartLegend(series = []) {
  return `<div class="chart-legend">${series
    .map(
      (item) =>
        `<span class="lg" style="--c:${item.color || "var(--primary)"}">${escapeHtml(item.label)}</span>`
    )
    .join("")}</div>`;
}

/** Mini barres pour la répartition des modes sur une période. */
export function modeBars(rows = []) {
  if (!rows.length) return "";
  const total = rows.reduce((sum, row) => sum + row.count, 0) || 1;
  return `<div class="stack" style="gap:8px">${rows
    .map(
      (row) => `<div class="row-between" style="gap:10px">
        <span class="tiny" style="min-width:96px">${escapeHtml(row.label)}</span>
        <span style="flex:1;height:8px;border-radius:999px;background:var(--surface-3);overflow:hidden">
          <span style="display:block;height:100%;width:${((row.count / total) * 100).toFixed(1)}%;background:${row.color}"></span>
        </span>
        <span class="tiny mono" style="min-width:44px;text-align:right">${Math.round((row.count / total) * 100)} %</span>
      </div>`
    )
    .join("")}</div>`;
}
