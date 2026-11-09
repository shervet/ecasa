// Statistiques : températures intérieures, consignes et météo extérieure.

import { get, query } from "../api.js";
import { chartLegend, lineChart } from "../charts.js";
import { icon } from "../icons.js";
import { devicesInRoom, store } from "../state.js";
import { toastError } from "../toast.js";
import {
  addDays,
  escapeHtml,
  fmtDate,
  fmtNumber,
  fmtTemp,
  isoDay,
  parseDate,
  readPref,
  sortByPosition,
  todayIso,
  writePref,
} from "../utils.js";

const RANGES = [
  ["day", "Jour"],
  ["week", "Semaine"],
  ["month", "Mois"],
];

let root = null;
let range = readPref("stats.range", "week");
let anchor = readPref("stats.anchor", todayIso());
let roomId = readPref("stats.room", null);
let data = null;
let loading = false;
let requestId = 0;

export default {
  title: "Statistiques",

  async mount(view) {
    root = view;
    if (!RANGES.some(([key]) => key === range)) range = "week";
    if (!parseDate(anchor)) anchor = todayIso();

    const rooms = roomOptions();
    if (!rooms.some((room) => String(room.id) === String(roomId))) {
      roomId = rooms[0]?.id ?? null;
    }

    view.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div>
            <h1 class="page-title">Statistiques</h1>
            <p class="page-sub" id="statsSub">Historique agrégé heure par heure.</p>
          </div>
          <div class="page-actions">
            <label class="field" style="min-width:200px">
              <span class="sr-only">Pièce</span>
              <select class="select" id="statsRoom"></select>
            </label>
          </div>
        </div>

        <div class="card card-pad">
          <div class="row-between wrap" style="gap:10px">
            <div class="segmented" id="statsRange">
              ${RANGES.map(([key, label]) => `<button type="button" data-range="${key}" class="${key === range ? "is-active" : ""}">${label}</button>`).join("")}
            </div>
            <div class="row" style="gap:6px">
              <button class="icon-btn" type="button" id="statsPrev" aria-label="Période précédente">${icon("chevron-left", 20)}</button>
              <button class="btn btn-sm" type="button" id="statsToday">Aujourd'hui</button>
              <button class="icon-btn" type="button" id="statsNext" aria-label="Période suivante">${icon("chevron-right", 20)}</button>
            </div>
          </div>
        </div>

        <div id="statsContent">
          <div class="boot" style="min-height:240px"><span class="spinner"></span><p>Chargement des mesures…</p></div>
        </div>
      </div>
    `;

    paintRoomSelect();

    view.querySelector("#statsRoom").addEventListener("change", (event) => {
      roomId = event.target.value;
      writePref("stats.room", roomId);
      load();
    });

    view.querySelectorAll("[data-range]").forEach((button) => {
      button.addEventListener("click", () => {
        range = button.dataset.range;
        writePref("stats.range", range);
        view.querySelectorAll("[data-range]").forEach((item) => {
          item.classList.toggle("is-active", item.dataset.range === range);
        });
        load();
      });
    });

    view.querySelector("#statsPrev").addEventListener("click", () => shift(-1));
    view.querySelector("#statsNext").addEventListener("click", () => shift(1));
    view.querySelector("#statsToday").addEventListener("click", () => {
      anchor = todayIso();
      writePref("stats.anchor", anchor);
      load();
    });

    await load();
  },

  unmount() {
    root = null;
  },
};

function roomOptions() {
  const state = store.state;
  if (!state) return [];
  return sortByPosition(state.rooms || [])
    .map((room) => ({ ...room, devices: devicesInRoom(state, room.id) }))
    .filter((room) => room.devices.length);
}

function paintRoomSelect() {
  const select = root?.querySelector("#statsRoom");
  if (!select) return;
  const rooms = roomOptions();
  select.innerHTML = rooms.length
    ? rooms.map((room) => `<option value="${room.id}"${String(room.id) === String(roomId) ? " selected" : ""}>${escapeHtml(room.name)}</option>`).join("")
    : `<option value="">Aucune pièce équipée</option>`;
  select.disabled = !rooms.length;
}

function shift(direction) {
  if (range === "day") anchor = isoDay(addDays(anchor, direction));
  else if (range === "week") anchor = isoDay(addDays(anchor, direction * 7));
  else {
    const date = parseDate(anchor) || new Date();
    date.setMonth(date.getMonth() + direction);
    anchor = isoDay(date);
  }
  if (anchor > todayIso()) anchor = todayIso();
  writePref("stats.anchor", anchor);
  load();
}

async function load() {
  if (!root) return;
  const host = root.querySelector("#statsContent");
  if (!host) return;

  const rooms = roomOptions();
  if (!rooms.length) {
    host.innerHTML = `<div class="empty card">
      <span class="empty-icon">${icon("chart", 24)}</span>
      <strong>Aucune donnée à afficher</strong>
      <p>Ajoute un appareil à une pièce et laisse MeCasa relever les températures.</p>
    </div>`;
    return;
  }

  if (!roomId) roomId = rooms[0].id;
  const id = ++requestId;
  loading = true;
  host.innerHTML = `<div class="boot" style="min-height:240px"><span class="spinner"></span><p>Chargement des mesures…</p></div>`;

  try {
    const payload = await get(`/api/stats${query({ range, date: anchor, room_id: roomId })}`);
    if (id !== requestId) return;
    data = payload;
    paint();
  } catch (err) {
    if (id !== requestId) return;
    host.innerHTML = `<div class="card card-pad">
      <div class="hint is-bad">${escapeHtml(err.message)}</div>
      <button class="btn btn-sm mt-2" type="button" id="statsRetry">${icon("refresh", 15)} Réessayer</button>
    </div>`;
    root.querySelector("#statsRetry")?.addEventListener("click", load);
    toastError(err);
  } finally {
    loading = false;
  }
}

function paint() {
  const host = root?.querySelector("#statsContent");
  if (!host || !data) return;

  const summary = data.summary || {};
  const buckets = data.buckets || [];
  const sub = root.querySelector("#statsSub");
  if (sub) {
    sub.textContent = `${data.room_name} · ${periodLabel(data)} · ${summary.samples || 0} mesure${summary.samples > 1 ? "s" : ""}`;
  }

  const series = [
    { key: "ambient", label: "Ambiance", color: "var(--warm)", values: buckets.map((b) => b.ambient) },
    { key: "setpoint", label: "Consigne", color: "var(--primary)", values: buckets.map((b) => b.setpoint), dashed: true, step: true },
    { key: "outside", label: "Extérieur", color: "var(--cool)", values: buckets.map((b) => b.outside), area: false },
  ];

  const weather = data.weather || {};
  const gap = summary.gap_avg;

  host.innerHTML = `
    <div class="kpi-grid">
      ${kpi("thermometer", "Ambiance moyenne", `${fmtTemp(summary.ambient_avg)} °C`, `${fmtTemp(summary.ambient_min)} → ${fmtTemp(summary.ambient_max)} °C`)}
      ${kpi("flame", "Consigne moyenne", `${fmtTemp(summary.setpoint_avg)} °C`, "réglage appliqué")}
      ${kpi("cloud", "Extérieur moyen", `${fmtTemp(summary.outside_avg)} °C`, `${fmtTemp(summary.outside_min)} → ${fmtTemp(summary.outside_max)} °C`)}
      ${kpi(
        gap !== null && gap !== undefined && Number(gap) > 0 ? "trending-up" : "trending-down",
        "Écart moyen",
        `${gap === null || gap === undefined ? "—" : `${Number(gap) > 0 ? "+" : ""}${fmtTemp(gap)} °C`}`,
        "ambiance − consigne"
      )}
    </div>

    <div class="card mt-2">
      <div class="card-head">
        <div>
          <div class="card-title">${icon("activity", 18)} Températures</div>
          <div class="card-sub">${escapeHtml(periodLabel(data))}</div>
        </div>
        <span class="pill ${weather.ok ? "pill-ok" : "pill-ghost"}">${weather.ok ? "Météo incluse" : "Sans météo"}</span>
      </div>
      <div class="card-body">
        ${lineChart({
          series,
          labels: buckets.map((bucket) => bucket.short || bucket.label),
          height: 260,
          unit: "°C",
        })}
        ${chartLegend(series)}
        ${!weather.ok && weather.error ? `<p class="hint mt-2">${escapeHtml(weather.error)}</p>` : ""}
      </div>
    </div>

    <div class="card mt-2">
      <div class="card-head">
        <div class="card-title">${icon("list", 18)} Détail</div>
        <span class="tiny muted">${summary.buckets_with_data || 0}/${buckets.length} périodes renseignées</span>
      </div>
      <div style="overflow-x:auto">
        <table class="stats-table">
          <thead>
            <tr><th>Période</th><th>Ambiance</th><th>Consigne</th><th>Extérieur</th><th>Mesures</th></tr>
          </thead>
          <tbody>
            ${buckets
              .map(
                (bucket) => `<tr>
                  <td>${escapeHtml(bucket.label)}</td>
                  <td class="${bucket.ambient !== null ? "is-strong" : ""}">${fmtTemp(bucket.ambient)} °C</td>
                  <td>${fmtTemp(bucket.setpoint)} °C</td>
                  <td>${fmtTemp(bucket.outside)} °C</td>
                  <td>${bucket.count || "—"}</td>
                </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>
    </div>

    <p class="hint mt-2">
      Les mesures sont agrégées par heure en mémoire puis écrites par lots sur le disque.
      ${summary.first_sample ? `Historique du ${fmtDate(summary.first_sample)} au ${fmtDate(summary.last_sample)}.` : ""}
    </p>
  `;

  attachChartTooltip();}

function kpi(iconName, label, value, sub) {
  return `<div class="kpi">
    <div class="k-label">${icon(iconName, 14)} ${escapeHtml(label)}</div>
    <div class="k-value">${escapeHtml(value)}</div>
    <div class="k-sub">${escapeHtml(sub)}</div>
  </div>`;
}

function attachChartTooltip() {
  const host = root?.querySelector("#statsContent");
  const wrap = host?.querySelector(".chart-wrap");
  const svg = wrap?.querySelector("svg");
  if (!wrap || !svg || !data?.buckets?.length) return;

  wrap.querySelector(".chart-tooltip")?.remove();

  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.style.cssText =
    "position:absolute;display:none;pointer-events:none;z-index:5;" +
    "background:var(--surface);border:1px solid var(--line);border-radius:10px;" +
    "padding:8px 10px;font-size:0.8rem;box-shadow:0 4px 14px rgba(0,0,0,.18);" +
    "max-width:220px;";
  wrap.style.position = "relative";
  wrap.appendChild(tooltip);

  const buckets = data.buckets;
  const count = buckets.length;
  const W = 760, M_LEFT = 42, M_RIGHT = 14;
  const innerW = W - M_LEFT - M_RIGHT;

  function nearestIndex(clientX) {
    const rect = svg.getBoundingClientRect();
    const ratio = (clientX - rect.left) / rect.width;
    const svgX = ratio * W;
    const idx = Math.round(((svgX - M_LEFT) / innerW) * (count - 1));
    return Math.max(0, Math.min(count - 1, idx));
  }

  function show(clientX) {
    const index = nearestIndex(clientX);
    const bucket = buckets[index];
    if (!bucket) return;
    const lines = [`<strong>${escapeHtml(bucket.label || bucket.short || "")}</strong>`];
    if (bucket.ambient !== null && bucket.ambient !== undefined) lines.push(`Ambiance : ${fmtTemp(bucket.ambient)} °C`);
    if (bucket.setpoint !== null && bucket.setpoint !== undefined) lines.push(`Consigne : ${fmtTemp(bucket.setpoint)} °C`);
    if (bucket.outside !== null && bucket.outside !== undefined) lines.push(`Extérieur : ${fmtTemp(bucket.outside)} °C`);
    if (bucket.count) lines.push(`${bucket.count} mesure${bucket.count > 1 ? "s" : ""}`);
    tooltip.innerHTML = lines.join("<br>");
    tooltip.style.display = "block";
    const rect = wrap.getBoundingClientRect();
    const x = clientX - rect.left;
    tooltip.style.left = `${Math.min(x + 12, rect.width - 230)}px`;
    tooltip.style.top = `12px`;

    // Repère vertical sur la position survolée/cliquée.
    let marker = svg.querySelector(".chart-marker");
    if (!marker) {
      marker = document.createElementNS("http://www.w3.org/2000/svg", "line");
      marker.setAttribute("class", "chart-marker");
      marker.setAttribute("stroke", "var(--faint)");
      marker.setAttribute("stroke-width", "1.2");
      marker.setAttribute("stroke-dasharray", "4 4");
      marker.setAttribute("y1", "16");
      marker.setAttribute("y2", String(svg.viewBox.baseVal.height - 30 || 230));
      svg.appendChild(marker);
    }
    const svgX = M_LEFT + (innerW * index) / Math.max(1, count - 1);
    marker.setAttribute("x1", svgX.toFixed(1));
    marker.setAttribute("x2", svgX.toFixed(1));
    marker.style.display = "";
  }

  function hide() {
    tooltip.style.display = "none";
    const marker = svg.querySelector(".chart-marker");
    if (marker) marker.style.display = "none";
  }

  let pinned = false;
  svg.addEventListener("mousemove", (event) => {
    if (pinned) return;
    show(event.clientX);
  });
  svg.addEventListener("mouseleave", () => {
    if (!pinned) hide();
  });
  svg.addEventListener("click", (event) => {
    if (pinned) {
      pinned = false;
      tooltip.style.display = "none";
    } else {
      pinned = true;
      show(event.clientX);
    }
  });
}

function periodLabel(payload) {
  if (!payload) return "—";
  const start = parseDate(payload.start);
  const end = parseDate(payload.end);
  if (!start || !end) return payload.range_label || "—";
  if (payload.range === "day") return fmtDate(start, { weekday: "long", day: "numeric", month: "long" });
  if (payload.range === "week") return `du ${fmtDate(start)} au ${fmtDate(end)}`;
  return `${start.toLocaleDateString("fr-FR", { month: "long", year: "numeric" })}`;
}
