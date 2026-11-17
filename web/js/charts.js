// Graphiques SVG autonomes (aucune librairie, aucun canvas).

import { escapeHtml, fmtNumber } from "./utils.js";

const W = 760;

// Détection tactile / largeurs étroites : utilisée pour agrandir les axes
const TOUCH = typeof matchMedia === "function" && matchMedia("(max-width: 900px), (pointer: coarse)").matches;
const K = TOUCH ? 2.9 : 1;
const primaryFont = Math.round(10.5 * K * 10) / 10;
const secondaryFont = Math.round(9.5 * K * 10) / 10;
const M = {
  top: TOUCH ? Math.round(primaryFont + 14) : 16,
  right: 14,
  bottom: TOUCH ? 70 : 30,
  left: TOUCH ? 76 : 42,
};

function niceMax(value, divisions = 4) {
  if (!(value > 0)) return 1;
  const rough = value / divisions;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10]
    .map((factor) => factor * power)
    .find((candidate) => candidate >= rough) || 10 * power;
  return step * divisions;
}

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
    const gridFont = primaryFont;
  grid += `<text x="${M.left - 8}" y="${(py + 3.5).toFixed(1)}" text-anchor="end" font-size="${primaryFont}" fill="var(--faint)">${fmtNumber(value, 0)}</text>`;
  }

// Étiquette d'unité autonome au-dessus de la graduation du haut
grid += `<text x="${M.left - 8}" y="${Math.round(primaryFont * 0.8) + 2}" text-anchor="end" font-size="${primaryFont}" fill="var(--faint)">${unit}</text>`;

  const step = Math.max(1, Math.ceil(count / 8));
  let xLabels = "";
  for (let i = 0; i < count; i += step) {
    const label = labels[i];
    if (label === undefined) continue;
    const parts = String(label).split("|");
    const textFont = primaryFont;
    const tspanFont = secondaryFont;
    if (parts.length > 1) {
      xLabels += `<text x="${x(i).toFixed(1)}" y="${H - (TOUCH ? primaryFont + secondaryFont + 5 : 20)}" text-anchor="middle" font-size="${textFont}" fill="var(--faint)">${escapeHtml(parts[0])}<tspan x="${x(i).toFixed(1)}" dy="${TOUCH ? Math.round(0.9 * secondaryFont) : 12}" font-size="${tspanFont}">${escapeHtml(parts[1])}</tspan></text>`;
    } else {
      xLabels += `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="${textFont}" fill="var(--faint)">${escapeHtml(label)}</text>`;
    }
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
        paths += `<circle cx="${segment[0].x.toFixed(1)}" cy="${segment[0].y.toFixed(1)}" r="2.6" fill="${color}" data-series="${escapeHtml(item.key)}" class="chart-series"/>`;
        continue;
      }

      if (item.area !== false && item.key === visible[0].key) {
        const first = segment[0].x.toFixed(1);
        const last = segment[segment.length - 1].x.toFixed(1);
        const base = y(Math.max(min, Math.min(max, min)));
        paths += `<path d="M ${first} ${base} L ${segment.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" L ")} L ${last} ${base} Z" fill="${color}" opacity="0.09" data-series="${escapeHtml(item.key)}" class="chart-series"/>`;
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
      paths += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" ${item.dashed ? 'stroke-dasharray="5 5"' : ""} data-series="${escapeHtml(item.key)}" class="chart-series"/>`;
    }

    if (showPoints) {
      item.values.forEach((value, index) => {
        if (!Number.isFinite(value)) return;
        points += `<circle cx="${x(index).toFixed(1)}" cy="${y(value).toFixed(1)}" r="2.4" fill="${color}" data-series="${escapeHtml(item.key)}" class="chart-series"><title>${escapeHtml(item.label)} : ${fmtNumber(value, 1)} ${unit}</title></circle>`;
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

/**
 * Barres groupées multi-séries (une série par année).
 * series: [{ key, label, color, values: (number|null)[] }]
 * outdoor (optionnel): { label, unit, values: (number|null)[] } — courbe température, axe droit.
 */
export function barChart({
  labels = [],
  series = [],
  height = 260,
  unit = "kWh",
  maxLabels = 12,
  ariaLabel = "Graphique en barres",
  outdoor = null,
} = {}) {
  const visible = series.filter(
    (item) => Array.isArray(item.values) && item.values.length
  );
  const all = visible.flatMap((item) => item.values).filter((value) => Number.isFinite(value));

  if (!all.length) {
    return `<div class="empty" style="padding:28px 12px">
      <p>Aucune donnée sur cette période.</p>
    </div>`;
  }

  const tempSeries = Array.isArray(outdoor)
    ? outdoor.filter((s) => Array.isArray(s?.values))
    : (outdoor && Array.isArray(outdoor.values) ? [outdoor] : []);
  const tempNums = tempSeries.flatMap((s) => s.values).filter((value) => Number.isFinite(value));
  const hasTemp = tempNums.length > 0;
  const right = hasTemp ? 42 : M.right;

  const H = height;
  const count = Math.max(labels.length, ...visible.map((item) => item.values.length), 1);
  const svgWidth = count > 20 ? Math.max(W, count * 22) : W;
  const innerW = svgWidth - M.left - right;
  const innerH = H - M.top - M.bottom;
  const max = niceMax(Math.max(...all));
  const groupW = innerW / count;
  const groupPad = Math.min(8, groupW * 0.16);
  const barGap = Math.min(3, groupW * 0.06);
  // Séries réellement présentes dans chaque groupe (une barre par série
  // non nulle) : permet de centrer les barres quand un groupe n'a qu'une
  // série (ex. vue Année : une barre par année), sans décaler les barres
  // vers la droite comme le faisait l'ancien offset global par série.
  const seriesByGroup = Array.from({ length: count }, () => []);
  visible.forEach((item, seriesIndex) => {
    item.values.forEach((value, index) => {
      if (Number.isFinite(value) && index >= 0 && index < count) {
        seriesByGroup[index].push(seriesIndex);
      }
    });
  });
  const maxBarsPerGroup = Math.max(1, ...seriesByGroup.map((group) => group.length));
  const barW = Math.max(
    1,
    (groupW - groupPad * 2 - barGap * (maxBarsPerGroup - 1)) / maxBarsPerGroup
  );
  const y = (value) => M.top + innerH - (value / max) * innerH;
  // Palette dédiée aux séries : N, N-1 et N-2 restent nettement distinctes
  // (vert, orange, violet, bleu…), en clair comme en sombre.
  const palette = [
    "var(--series-1)",
    "var(--series-2)",
    "var(--series-3)",
    "var(--series-4)",
    "var(--series-5)",
    "var(--series-6)",
  ];

  const gridCount = 4;
  let grid = "";
  const gridFont = primaryFont;
  for (let i = 0; i <= gridCount; i += 1) {
    const value = (max * i) / gridCount;
    const py = y(value);
    grid += `<line x1="${M.left}" y1="${py.toFixed(1)}" x2="${svgWidth - right}" y2="${py.toFixed(1)}" stroke="var(--line)" stroke-width="1" stroke-dasharray="${i === 0 ? "0" : "3 5"}" opacity="0.8"/>`;
    grid += `<text x="${M.left - 8}" y="${(py + 3.5).toFixed(1)}" text-anchor="end" font-size="${gridFont}" fill="var(--faint)">${fmtNumber(value, Number.isInteger(value) ? 0 : 1)}</text>`;
  }

// Étiquette d'unité autonome au-dessus de la graduation du haut
grid += `<text x="${M.left - 8}" y="${Math.round(primaryFont * 0.8) + 2}" text-anchor="end" font-size="${primaryFont}" fill="var(--faint)">${unit}</text>`;

  let bars = "";
  visible.forEach((item, seriesIndex) => {
    if (!item.color) item.color = palette[seriesIndex % palette.length];
    item.values.forEach((value, index) => {
      if (!Number.isFinite(value)) return;
      const barH = Math.max(0, innerH - (y(value) - M.top));
      const siblings = seriesByGroup[index] || [];
      const position = Math.max(0, siblings.indexOf(seriesIndex));
      const totalWidth = siblings.length * barW + (siblings.length - 1) * barGap;
      const groupStart = M.left + groupW * index + (groupW - totalWidth) / 2;
      const x = groupStart + position * (barW + barGap);
      const title = `${labels[index] ?? index} · ${item.label} : ${fmtNumber(value, 1)} ${unit}`;
      bars += `<rect class="chart-bar" x="${x.toFixed(1)}" y="${(M.top + innerH - barH).toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="2" fill="${item.color}" data-index="${index}" data-label="${escapeHtml(labels[index] ?? index)}" data-series="${escapeHtml(item.label)}" data-value="${Number(value)}" role="img" aria-label="${escapeHtml(title)}"><title>${escapeHtml(title)}</title></rect>`;
    });
  });

  let tempLayer = "";
  if (hasTemp) {
    let tMin = Math.min(...tempNums);
    let tMax = Math.max(...tempNums);
    if (tMax - tMin < 1) {
      const c = (tMax + tMin) / 2;
      tMin = c - 0.5;
      tMax = c + 0.5;
    }
    const tPad = (tMax - tMin) * 0.12;
    tMin -= tPad;
    tMax += tPad;
    const ty = (value) => M.top + innerH - ((value - tMin) / (tMax - tMin)) * innerH;
    const tUnit = (tempSeries[0] && tempSeries[0].unit) || "°C";
    for (let i = 0; i <= gridCount; i += 1) {
      const value = tMin + ((tMax - tMin) * i) / gridCount;
      const py = ty(value);
      tempLayer += `<text x="${svgWidth - right + 6}" y="${(py + 3.5).toFixed(1)}" text-anchor="start" font-size="${gridFont}" fill="var(--faint)">${fmtNumber(value, 0)}${tUnit}</text>`;
    }
    tempSeries.forEach((ts) => {
      const vals = Array.isArray(ts.values) ? ts.values : [];
      const color = ts.color || "var(--warn, #f59e0b)";
      const pts = [];
      for (let i = 0; i < count; i += 1) {
        const v = vals[i];
        if (!Number.isFinite(v)) continue;
        pts.push({ i, x: M.left + groupW * i + groupW / 2, y: ty(v), v });
      }
      if (pts.length >= 2) {
        const d = `M ${pts.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" L ")}`;
        tempLayer += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" opacity="0.95" data-series="${escapeHtml(ts.key || ts.label || "")}" class="chart-series"/>`;
      }
      for (const p of pts) {
        const title = `${labels[p.i] ?? ""} · ${ts.label || "Temp. ext."} : ${fmtNumber(p.v, 1)} ${tUnit}`;
        tempLayer += `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="2.4" fill="${color}" data-series="${escapeHtml(ts.key || ts.label || "")}" class="chart-series"><title>${escapeHtml(title)}</title></circle>`;
      }
    });
  }

  const step = Math.max(1, Math.ceil(count / maxLabels));
  let xLabels = "";
  const textFont = primaryFont;
  const tspanFont = secondaryFont;
  for (let i = 0; i < count; i += step) {
    const label = labels[i];
    if (label === undefined) continue;
    const x = M.left + groupW * i + groupW / 2;
    const parts = String(label).split("|");
    if (parts.length > 1) {
      xLabels += `<text x="${x.toFixed(1)}" y="${H - (TOUCH ? primaryFont + secondaryFont + 5 : 20)}" text-anchor="middle" font-size="${textFont}" fill="var(--faint)">${escapeHtml(parts[0])}<tspan x="${x.toFixed(1)}" dy="${TOUCH ? Math.round(0.9 * secondaryFont) : 12}" font-size="${tspanFont}">${escapeHtml(parts[1])}</tspan></text>`;
    } else {
      xLabels += `<text x="${x.toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="${textFont}" fill="var(--faint)">${escapeHtml(label)}</text>`;
    }
  }

  const scroll = svgWidth > W ? " is-scroll" : "";
  return `<div class="chart-wrap${scroll}">
    <svg viewBox="0 0 ${svgWidth} ${H}" role="img" aria-label="${escapeHtml(ariaLabel)}"${scroll ? ` style="width:${svgWidth}px;max-width:none"` : ""}>
      ${grid}
      ${bars}
      ${tempLayer}
      ${xLabels}
    </svg>
  </div>`;
}

export function chartLegend(series = []) {
  return `<div class="chart-legend">${series
    .map(
      (item) =>
        `<span class="lg" data-key="${escapeHtml(item.key || item.label)}" style="--c:${item.color || "var(--primary)"}">${escapeHtml(item.label)}</span>`
    )
    .join("")}</div>`;
}

const TOOLTIP_DISMISS = "chart-tooltip:dismiss";
let tooltipDismissBound = false;
// Chaque bulle connaît son graphique : indispensable puisqu'elle vit dans
// <body> et non dans le conteneur du graphique.
const tooltipOwners = new WeakMap();

/** Supprime les bulles dont le graphique a été remplacé par un rendu. */
function purgeTooltips() {
  document.querySelectorAll(".chart-tooltip").forEach((tip) => {
    // Seules les bulles créées par attachBarTooltip sont suivies dans la
    // WeakMap : les autres (tooltip température, etc.) ne doivent pas être
    // supprimées.
    if (!tooltipOwners.has(tip)) return;
    const owner = tooltipOwners.get(tip);
    if (owner && owner.isConnected) return;
    tooltipOwners.delete(tip);
    tip.remove();
  });
}

/** Referme les bulles quand on touche ailleurs ou qu'on fait défiler la page. */
function bindTooltipDismiss() {
  if (tooltipDismissBound) return;
  tooltipDismissBound = true;

  const dismissAll = () => {
    document.querySelectorAll(".chart-tooltip").forEach((tip) => {
      if (!tip.style.display || tip.style.display === "none") return;
      tip.dispatchEvent(new Event(TOOLTIP_DISMISS));
    });
  };

  // Un appui dans le graphique referme puis rouvre la bulle via l'écouteur
  // du graphique lui-même : le résultat reste la barre touchée.
  document.addEventListener("pointerdown", dismissAll, true);

  // La bulle est ancrée à une barre : on la referme dès que la page bouge.
  window.addEventListener("scroll", dismissAll, {
    passive: true,
    capture: true,
  });
}

/**
 * Tooltip interactif pour les barres d'un graphique en barres.
 * Affiche label, série et valeur au survol dans une fenêtre flottante
 * placée au-dessus de la barre cliquée.
 *
 * Au clic (ou appui), la bulle épingle un **tableau** de la période
 * cliquée — une ligne par année de la comparaison — quand le serveur
 * fournit ``table`` ({ labels, rows: [[{ label, value, days, color }]] })
 * et que plusieurs lignes sont utiles ; sinon elle garde l'affichage
 * simple.
 *
 * La bulle est ajoutée à <body> avec un positionnement fixe : aucun
 * conteneur qui défile, aucun `overflow` ni `backdrop-filter` d'un ancêtre
 * ne peut la rogner ou la cacher derrière la navigation.
 */
export function attachBarTooltip(wrap, { unit = "", table = null } = {}) {
  if (!wrap) return;
  bindTooltipDismiss();
  purgeTooltips();

  let tooltip = wrap.__chartTooltip;
  if (!tooltip || !tooltip.isConnected) {
    tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.style.position = "fixed";
    document.body.appendChild(tooltip);
    wrap.__chartTooltip = tooltip;
    tooltipOwners.set(tooltip, wrap);
  }

  let activeRect = null;
  let pinned = false;

  function place() {
    if (!activeRect || !tooltip.isConnected) return;
    // La bulle doit être mesurable avant d'être positionnée.
    tooltip.style.display = "block";

    const rect = activeRect.getBoundingClientRect();
    const width = tooltip.offsetWidth || 0;
    const height = tooltip.offsetHeight || 0;
    const pad = 8;

    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(pad, Math.min(left, Math.max(pad, window.innerWidth - width - pad)));

    // Juste au-dessus de la barre ; en dessous si le haut de l'écran manque.
    let top = rect.top - height - 10;
    if (top < pad) top = rect.bottom + 10;
    top = Math.max(pad, Math.min(top, Math.max(pad, window.innerHeight - height - pad)));

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  // Lignes du tableau de la période cliquée (une ligne par année), telles
  // que calculées par le serveur — aucun total n'est recalculé ici.
  function tableRows(rect) {
    if (!table || !Array.isArray(table.rows)) return null;
    const index = Number(rect.dataset.index);
    if (!Number.isInteger(index)) return null;
    const rows = table.rows[index];
    return Array.isArray(rows) ? rows : null;
  }

  function tooltipTable(rows) {
    const body = rows
      .map((row) => {
        const value =
          row.value === null || row.value === undefined
            ? "—"
            : `${fmtNumber(Number(row.value), 1)} ${unit}`.trim();
        const variation =
          row.variation === null || row.variation === undefined
            ? "—"
            : `${Number(row.variation) > 0 ? "+" : ""}${fmtNumber(Number(row.variation), 1)} %`;
        return `<tr>
          <td><span class="chart-tooltip-dot" style="background:${row.color || "var(--primary)"}"></span>${escapeHtml(row.label)}</td>
          <td class="num">${escapeHtml(value)}</td>
          <td class="num">${escapeHtml(variation)}</td>
        </tr>`;
      })
      .join("");
    return `<table class="chart-tooltip-table">
      <thead><tr><th>Année</th><th class="num">Conso</th><th class="num">Variation</th></tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }

  function show(rect, withTable = false) {
    activeRect = rect;
    const label = (rect.dataset.label ?? "").split("|")[0];
    const series = rect.dataset.series ?? "";
    const raw = rect.dataset.value;
    const valueText =
      raw === "" || raw === undefined
        ? "—"
        : `${fmtNumber(Number(raw), 1)} ${unit}`.trim();
    const rows = withTable ? tableRows(rect) : null;
    if (rows && rows.length > 1) {
      tooltip.innerHTML =
        (label ? `<strong>${escapeHtml(label)}</strong>` : "") +
        tooltipTable(rows);
    } else {
      // Une seule série (ou pas de tableau) : affichage simple.
      tooltip.innerHTML = [
        label ? `<strong>${escapeHtml(label)}</strong>` : "",
        series ? escapeHtml(series) : "",
        escapeHtml(valueText),
      ]
        .filter(Boolean)
        .join("<br>");
    }
    place();
  }

  function hide() {
    activeRect = null;
    pinned = false;
    tooltip.style.display = "none";
  }

  // event.target peut être un noeud SVG : closest() n'existe que sur Element.
  function barFrom(event) {
    const target = event.target;
    if (!target || typeof target.closest !== "function") return null;
    return target.closest("rect[data-value]");
  }

  tooltip.addEventListener(TOOLTIP_DISMISS, hide);

  // Sur ordinateur, la bulle suit la barre survolée ; sur mobile, un appui
  // l'épingle au-dessus de la barre touchée.
  wrap.addEventListener("pointerover", (event) => {
    if (event.pointerType !== "mouse" || pinned) return;
    const rect = barFrom(event);
    if (rect) show(rect);
  });
  wrap.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || pinned) return;
    const rect = barFrom(event);
    if (rect) show(rect);
  });
  wrap.addEventListener("pointerout", (event) => {
    if (event.pointerType !== "mouse" || pinned) return;
    if (!wrap.contains(event.relatedTarget)) hide();
  });
  wrap.addEventListener("pointerdown", (event) => {
    const rect = barFrom(event);
    if (rect) {
      pinned = true;
      // Au clic : le tableau des années de la comparaison, s'il est utile.
      show(rect, true);
    } else {
      // Appui dans le vide : on referme la valeur affichée.
      hide();
    }
  });
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
