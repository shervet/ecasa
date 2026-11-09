// Statistiques : températures intérieures, consignes et météo extérieure.

import { get, query } from "../api.js";
import { attachBarTooltip, barChart, chartLegend, lineChart } from "../charts.js";
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

const ENERGY_VIEWS = [
  ["day", "Jour"],
  ["week", "Semaine"],
  ["month", "Mois"],
  ["year", "Année"],
];

const MONTHS = [
  "janvier",
  "février",
  "mars",
  "avril",
  "mai",
  "juin",
  "juillet",
  "août",
  "septembre",
  "octobre",
  "novembre",
  "décembre",
];

let root = null;
let range = readPref("stats.range", "week");
let anchor = readPref("stats.anchor", todayIso());
let roomId = readPref("stats.room", null);
let data = null;
let loading = false;
let requestId = 0;
let energyView = readPref("stats.energyView", "month");
let energyMonth = Number(readPref("stats.energyMonth", new Date().getMonth() + 1));
let energyDay = readPref("stats.energyDay", todayIso());
let energyDays = Number(readPref("stats.energyDays", 7));
let energyRequestId = 0;

export default {
  title: "Statistiques",

  async mount(view) {
    root = view;
    if (!RANGES.some(([key]) => key === range)) range = "week";
    if (!ENERGY_VIEWS.some(([key]) => key === energyView)) energyView = "month";
    if (!Number.isInteger(energyMonth) || energyMonth < 1 || energyMonth > 12) {
      energyMonth = new Date().getMonth() + 1;
    }
    if (!Number.isInteger(energyDays) || energyDays < 1) energyDays = 7;
    if (!parseDate(energyDay)) energyDay = todayIso();
    const energyDate = parseDate(energyDay);
    if (energyDate && energyDate.getMonth() + 1 !== energyMonth) {
      energyDay = isoDay(new Date(energyDate.getFullYear(), energyMonth - 1, 1));
    }
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

        <div id="energyContent" class="mt-2"></div>
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
    // La carte énergie charge en arrière-plan : un appel Enedis lent ne
    // retarde pas l'affichage des courbes de température.
    loadEnergy();
  },

  unmount() {
    energyRequestId += 1;
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

    <details class="card card-details mt-2"${readPref("stats.detailOpen", false) ? " open" : ""}>
      <summary class="card-head">
        <div class="card-title">${icon("list", 18)} Détail</div>
        <span class="tiny muted">${summary.buckets_with_data || 0}/${buckets.length} périodes renseignées</span>
        ${icon("chevron-down", 18, "details-chevron")}
      </summary>
      <div class="card-body" style="overflow-x:auto">
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
    </details>

    <p class="hint mt-2">
      Les mesures sont agrégées par heure en mémoire puis écrites par lots sur le disque.
      ${summary.first_sample ? `Historique du ${fmtDate(summary.first_sample)} au ${fmtDate(summary.last_sample)}.` : ""}
    </p>
  `;

  attachChartTooltip();

  const details = host.querySelector(".card-details");
  if (details) {
    details.addEventListener("toggle", () => {
      writePref("stats.detailOpen", details.open);
    });
  }
}

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

/* ------------------------------------------------------- consommation */

async function loadEnergy() {
  if (!root) return;
  const host = root.querySelector("#energyContent");
  if (!host) return;

  const id = ++energyRequestId;
  host.innerHTML = `<div class="card card-pad">
    <div class="boot" style="min-height:160px"><span class="spinner"></span><p>Chargement de la consommation…</p></div>
  </div>`;

  try {
    const payload = await get(
      `/api/energy${query({
        view: energyView,
        month: energyView === "day" ? energyMonth : undefined,
        day: energyView === "day" ? energyDay : undefined,
        days: energyView === "day" ? energyDays : undefined,
      })}`
    );
    if (id !== energyRequestId) return;
    paintEnergy(payload);
  } catch (err) {
    if (id !== energyRequestId) return;
    host.innerHTML = `<div class="card card-pad">
      <div class="hint is-bad">${escapeHtml(err.message)}</div>
      <button class="btn btn-sm mt-2" type="button" id="energyRetry">${icon("refresh", 15)} Réessayer</button>
    </div>`;
    root.querySelector("#energyRetry")?.addEventListener("click", loadEnergy);
    toastError(err);
  }
}

function paintEnergy(payload) {
  const host = root?.querySelector("#energyContent");
  if (!host) return;

  const ok = Boolean(payload?.ok);
  const summary = payload?.summary || {};
  const window_ = payload?.window || null;
  const isDay = energyView === "day";

  const dayNav = isDay
    ? `<div class="row" style="gap:4px;align-items:center">
        <button class="icon-btn" type="button" id="energyPrev" aria-label="7 jours précédents"${
          window_ && window_.start <= 1 ? " disabled" : ""
        }>${icon("chevron-left", 18)}</button>
        <span class="tiny" id="energyWindow">${dayWindowLabel(window_, energyDay)}</span>
        <button class="icon-btn" type="button" id="energyNext" aria-label="7 jours suivants"${
          window_ && window_.end >= window_.limit ? " disabled" : ""
        }>${icon("chevron-right", 18)}</button>
      </div>`
    : "";

  const controls = `<div class="row-between wrap" style="gap:10px">
    <div class="segmented" id="energyView">
      ${ENERGY_VIEWS.map(
        ([key, label]) =>
          `<button type="button" data-energy-view="${key}" class="${key === energyView ? "is-active" : ""}">${label}</button>`
      ).join("")}
    </div>
    ${
      isDay
        ? `<div class="row" style="gap:8px;align-items:center;flex-wrap:wrap">
            <label class="field" style="min-width:150px">
              <span class="sr-only">Mois</span>
              <select class="select" id="energyMonth">
                ${MONTHS.map(
                  (label, index) =>
                    `<option value="${index + 1}"${index + 1 === energyMonth ? " selected" : ""}>${label}</option>`
                ).join("")}
              </select>
            </label>
            ${dayNav}
          </div>`
        : ""
    }
  </div>`;

  let body = "";
  if (!ok) {
    const hint = payload?.error || "Consommation indisponible.";
    const action =
      payload?.enabled === false || payload?.configured === false
        ? `<a class="btn btn-sm mt-2" href="#/settings">Ouvrir les réglages</a>`
        : "";
    body = `<div class="empty" style="padding:24px 12px">
      <span class="empty-icon">${icon("zap", 22)}</span>
      <strong>Consommation électrique</strong>
      <p>${escapeHtml(hint)}</p>
      ${action}
    </div>`;
  } else {
    const series = payload.series || [];
    const labels = payload.labels || [];
    const hasByYear = Array.isArray(summary.by_year) && summary.by_year.length;
    body = `
      ${hasByYear ? byYearTable(summary) : energyKpiGrid(summary)}
      ${barChart({
        labels,
        series,
        height: 280,
        unit: "kWh",
        ariaLabel: "Consommation électrique par année",
      })}
      ${chartLegend(series)}
      <p class="hint mt-2">
        Source MyElectricalData (Enedis)${
          payload.last_date ? ` · dernière donnée ${fmtDate(payload.last_date)}` : ""
        } · ${payload.days || 0} jour(s) en mémoire.
      </p>
    `;
  }

  host.innerHTML = `<div class="card">
    <div class="card-head">
      <div>
        <div class="card-title">${icon("zap", 18)} Consommation électrique</div>
        <div class="card-sub">${escapeHtml(energySubtitle(payload))}</div>
      </div>
      <span class="pill ${ok ? "pill-ok" : "pill-ghost"}">${ok ? "kWh" : "hors ligne"}</span>
    </div>
    <div class="card-body">
      ${controls}
      <div style="margin-top:12px">${body}</div>
    </div>
  </div>`;

  host.querySelectorAll("[data-energy-view]").forEach((button) => {
    button.addEventListener("click", () => {
      energyView = button.dataset.energyView;
      writePref("stats.energyView", energyView);
      loadEnergy();
    });
  });

  host.querySelector("#energyMonth")?.addEventListener("change", (event) => {
    energyMonth = Number(event.target.value) || new Date().getMonth() + 1;
    const current = parseDate(energyDay) || new Date();
    energyDay = isoDay(new Date(current.getFullYear(), energyMonth - 1, 1));
    writePref("stats.energyMonth", energyMonth);
    writePref("stats.energyDay", energyDay);
    loadEnergy();
  });

  host.querySelector("#energyPrev")?.addEventListener("click", () => shiftEnergyDay(-energyDays));
  host.querySelector("#energyNext")?.addEventListener("click", () => shiftEnergyDay(energyDays));

  const barWrap = host.querySelector(".chart-wrap");
  attachBarTooltip(barWrap, { unit: "kWh" });
}

function byYearTable(summary) {
  const rows = summary.by_year;
  const partial = summary.partial
    ? `<p class="hint mt-2">Totaux à date : l’année en cours n’est pas terminée, la comparaison porte sur la même période.</p>`
    : "";
  return `<div style="overflow-x:auto">
    <table class="stats-table">
      <thead>
        <tr><th>Année</th><th>Consommation</th><th>Écart N-1</th></tr>
      </thead>
      <tbody>
        ${rows
          .map((row) => {
            const delta = row.delta_pct;
            return `<tr>
              <td>${escapeHtml(row.label ?? row.year ?? "—")}</td>
              <td>${row.kwh === null || row.kwh === undefined ? "—" : `${fmtNumber(row.kwh, 0)} kWh`}</td>
              <td>${delta === null || delta === undefined ? "—" : `${delta > 0 ? "+" : ""}${fmtNumber(delta, 1)} %`}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table>
    ${partial}
  </div>`;
}

function energyKpiGrid(summary) {
  const delta = summary.delta_pct;
  return `<div class="kpi-grid">
    ${kpi(
      "zap",
      summary.label || `Année ${summary.year ?? ""}`,
      summary.total_kwh === null || summary.total_kwh === undefined
        ? "—"
        : `${fmtNumber(summary.total_kwh, 1)} kWh`,
      `${summary.days || 0} jour(s) relevés`
    )}
    ${kpi(
      "history",
      summary.previous_label || `Année ${summary.previous_year ?? ""}`,
      summary.previous_kwh === null || summary.previous_kwh === undefined
        ? "—"
        : `${fmtNumber(summary.previous_kwh, 1)} kWh`,
      "même période"
    )}
    ${kpi(
      delta !== null && delta !== undefined && delta > 0 ? "trending-up" : "trending-down",
      "Écart",
      delta === null || delta === undefined ? "—" : `${delta > 0 ? "+" : ""}${fmtNumber(delta, 1)} %`,
      "année sur année"
    )}
  </div>`;
}

function dayWindowLabel(window_, energyDayIso) {
  if (!window_) return "";
  const date = parseDate(energyDayIso) || new Date();
  const year = date.getFullYear();
  const month = (energyMonth || 1) - 1;
  const start = new Date(year, month, window_.start);
  const end = new Date(year, month, window_.end);
  return `${fmtDate(start)} → ${fmtDate(end)}`;
}

function shiftEnergyDay(delta) {
  // Le serveur ne raisonne qu'en numéro de jour dans le mois sélectionné :
  // on borne donc le jour au mois courant avant de demander la fenêtre.
  const date = parseDate(energyDay) || new Date();
  const year = date.getFullYear();
  const month = (energyMonth || 1) - 1;
  const lastDay = new Date(year, month + 1, 0).getDate();
  const day = Math.min(Math.max(date.getDate() + delta, 1), lastDay);
  energyDay = isoDay(new Date(year, month, day));
  writePref("stats.energyDay", energyDay);
  loadEnergy();
}

function energySubtitle(payload) {
  if (!payload) return "";
  const view = payload.view_label || "Mois";
  if (payload.view === "day") {
    return `${view} · ${payload.month_label || ""} · comparaison par année`;
  }
  if (payload.view === "year") return `${view} · total annuel`;
  return `${view} · comparaison par année`;
}
