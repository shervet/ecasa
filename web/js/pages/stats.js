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
let energyInitial = true;
let energyRequestId = 0;
let energyPayload = null;
let temperatureTooltipAbort = null;

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
      // Garde le dernier jour du mois choisi : la fenêtre « 7 jours » est
      // ainsi utile dès l'ouverture, sans rester bloquée sur le 1er.
      energyDay = defaultEnergyDay(energyMonth);
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
    temperatureTooltipAbort?.abort();
    temperatureTooltipAbort = null;
    energyPayload = null;
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

  document.querySelectorAll(".chart-tooltip").forEach((t) => {
    if (t.dataset.owner === "temp") t.remove();
  });
  temperatureTooltipAbort?.abort();
  const controller = new AbortController();
  temperatureTooltipAbort = controller;

  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.dataset.owner = "temp";
  tooltip.style.position = "fixed";
  document.body.appendChild(tooltip);

  const buckets = data.buckets;
  const count = buckets.length;
  const W = 760, M_LEFT = 42, M_RIGHT = 14;
  const innerW = W - M_LEFT - M_RIGHT;
  let pinned = false;

  function nearestIndex(clientX) {
    const rect = svg.getBoundingClientRect();
    const ratio = (clientX - rect.left) / rect.width;
    const svgX = ratio * W;
    const idx = Math.round(((svgX - M_LEFT) / innerW) * (count - 1));
    return Math.max(0, Math.min(count - 1, idx));
  }

  function place(clientX, clientY) {
    // Mesure fiable : la bulle doit être visible avant offsetWidth/Height.
    tooltip.style.display = "block";
    const width = tooltip.offsetWidth || 0;
    const height = tooltip.offsetHeight || 0;
    const pad = 10;
    let left = clientX - width / 2;
    left = Math.max(pad, Math.min(left, Math.max(pad, window.innerWidth - width - pad)));
    // L'information apparaît juste au-dessus du doigt (ou en dessous si le
    // haut de l'écran est atteint), exactement là où l'on a cliqué.
    let top = clientY - height - 16;
    if (top < pad) top = clientY + 18;
    top = Math.max(pad, Math.min(top, Math.max(pad, window.innerHeight - height - pad)));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function show(event) {
    const index = nearestIndex(event.clientX);
    const bucket = buckets[index];
    if (!bucket) return;
    const lines = [`<strong>${escapeHtml(bucket.label || bucket.short || "")}</strong>`];
    if (bucket.ambient !== null && bucket.ambient !== undefined) lines.push(`Ambiance : ${fmtTemp(bucket.ambient)} °C`);
    if (bucket.setpoint !== null && bucket.setpoint !== undefined) lines.push(`Consigne : ${fmtTemp(bucket.setpoint)} °C`);
    if (bucket.outside !== null && bucket.outside !== undefined) lines.push(`Extérieur : ${fmtTemp(bucket.outside)} °C`);
    if (bucket.count) lines.push(`${bucket.count} mesure${bucket.count > 1 ? "s" : ""}`);
    tooltip.innerHTML = lines.join("<br>");
    place(event.clientX, event.clientY);

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
    pinned = false;
    tooltip.style.display = "none";
    const marker = svg.querySelector(".chart-marker");
    if (marker) marker.style.display = "none";
  }

  svg.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || pinned) return;
    show(event);
  });
  svg.addEventListener("pointerleave", (event) => {
    if (event.pointerType !== "mouse" || pinned) return;
    hide();
  });
  svg.addEventListener("pointerdown", (event) => {
    if (pinned) {
      hide();
    } else {
      pinned = true;
      show(event);
    }
  });

  // Un appui hors du graphique referme la bulle épinglée ; le défilement
  // de la page aussi, pour ne pas laisser une info figée à l'écran.
  document.addEventListener(
    "pointerdown",
    (event) => {
      if (!wrap.contains(event.target)) hide();
    },
    { capture: true, signal: controller.signal }
  );
  window.addEventListener("scroll", hide, {
    passive: true,
    capture: true,
    signal: controller.signal,
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

async function loadEnergy(refresh = false) {
  if (!root) return;
  const host = root.querySelector("#energyContent");
  if (!host) return;

  const id = ++energyRequestId;
  host.innerHTML = `<div class="card card-pad">
    <div class="boot" style="min-height:160px"><span class="spinner"></span><p>Chargement de la consommation…</p></div>
  </div>`;

  const isNav = energyView !== "year";
  try {
    const payload = await get(
      `/api/energy${query({
        view: energyView,
        // Les vues jour, semaine et mois sont ancrées sur un mois + un
        // jour : c'est ce couple qui pilote la navigation par flèches /
        // glisser (en vue mois, seuls le mois et l'année de la date
        // comptent ; en vue semaine, le mois et le jour du mois).
        month: isNav ? energyMonth : undefined,
        day: isNav && !energyInitial ? energyDay : undefined,
        days: energyView === "day" ? energyDays : undefined,
        // Synchro manuelle : force l'appel à Enedis au lieu du cache de 3 h.
        refresh: refresh ? 1 : undefined,
      })}`
    );
    if (id !== energyRequestId) return;
    energyPayload = payload;
    energyInitial = false;
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

  energyPayload = payload;
  const ok = Boolean(payload?.ok);
  const summary = payload?.summary || {};
  const isNav = energyView !== "year";
  const [prevLabel, nextLabel] = {
    day: ["Jours précédents", "Jours suivants"],
    week: ["Semaines précédentes", "Semaines suivantes"],
    month: ["Mois précédents", "Mois suivants"],
  }[energyView] || ["Précédent", "Suivant"];

  // Le mois est placé entre les deux flèches : on change de bloc (7 jours,
  // 7 semaines ou 7 mois) sans quitter le sélecteur, et le libellé de
  // période reste lisible juste en dessous.
  const nav = isNav
    ? `<div class="energy-nav">
        <button class="icon-btn" type="button" id="energyPrev" aria-label="${prevLabel}"${navDisabled(payload, -1) ? " disabled" : ""}>${icon("chevron-left", 18)}</button>
        <label class="energy-month">
          <span class="sr-only">Mois</span>
          <select class="select" id="energyMonth">
            ${MONTHS.map(
              (label, index) =>
                `<option value="${index + 1}"${index + 1 === energyMonth ? " selected" : ""}>${label}</option>`
            ).join("")}
          </select>
        </label>
        <button class="icon-btn" type="button" id="energyNext" aria-label="${nextLabel}"${navDisabled(payload, 1) ? " disabled" : ""}>${icon("chevron-right", 18)}</button>
      </div>
      <div class="energy-period tiny muted">${escapeHtml(periodNavLabel(payload))}</div>`
    : "";

  const controls = `<div class="row-between wrap" style="gap:10px">
    <div class="segmented" id="energyView">
      ${ENERGY_VIEWS.map(
        ([key, label]) =>
          `<button type="button" data-energy-view="${key}" class="${key === energyView ? "is-active" : ""}">${label}</button>`
      ).join("")}
    </div>
    ${
      isNav
        ? `<span class="tiny muted energy-swipe-hint">${icon("calendar-days", 14)} Glisser sur le graphique</span>`
        : ""
    }
  </div>
  ${nav}`;

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
      ${barChart({
        labels,
        series,
        height: 280,
        unit: "kWh",
        ariaLabel: "Consommation électrique par année",
      })}
      ${chartLegend(series)}
      ${hasByYear ? energyTable(payload) : energyKpiGrid(summary)}
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
      <div class="row" style="gap:8px;align-items:center">
        <span class="pill ${ok ? "pill-ok" : "pill-ghost"}">${ok ? "kWh" : "hors ligne"}</span>
        <button class="icon-btn" type="button" id="energyRefresh" aria-label="Synchroniser maintenant" title="Synchroniser maintenant">${icon("refresh", 16)}</button>
      </div>
    </div>
    <div class="card-body">
      ${controls}
      <div style="margin-top:12px">${body}</div>
    </div>
  </div>`;

  host.querySelectorAll("[data-energy-view]").forEach((button) => {
    button.addEventListener("click", () => {
      energyView = button.dataset.energyView;
      energyInitial = true;
      writePref("stats.energyView", energyView);
      // La vue mois déplace energyDay dans le temps (année incluse) : en
      // revenant aux vues jour / semaine, on repart sur l'année courante.
      const date = parseDate(energyDay);
      if (date && date.getFullYear() !== new Date().getFullYear()) {
        energyDay = defaultEnergyDay(energyMonth);
        writePref("stats.energyDay", energyDay);
      }
      loadEnergy();
    });
  });

  host.querySelector("#energyMonth")?.addEventListener("change", (event) => {
    energyMonth = Number(event.target.value) || new Date().getMonth() + 1;
    energyInitial = true;
    // Ouvre la fin du mois choisi (les 7 derniers jours disponibles), plutôt
    // que le 1er qui ne montrait qu'une journée.
    energyDay = defaultEnergyDay(energyMonth);
    writePref("stats.energyMonth", energyMonth);
    writePref("stats.energyDay", energyDay);
    loadEnergy();
  });

  host.querySelector("#energyPrev")?.addEventListener("click", () => shiftEnergy(-1));
  host.querySelector("#energyNext")?.addEventListener("click", () => shiftEnergy(1));
  // La synchro automatique n'a lieu qu'à 3 h : ce bouton permet de forcer
  // une actualisation manuelle à tout moment.
  host.querySelector("#energyRefresh")?.addEventListener("click", () => loadEnergy(true));

  const barWrap = host.querySelector(".chart-wrap");
  if (barWrap) {
    // Clic sur une barre : tableau des années pour la période cliquée.
    attachBarTooltip(barWrap, { unit: "kWh", table: payload?.tooltip });
    // En vue jour / semaine / mois, un glisser horizontal sur la courbe
    // change de bloc — même geste qu'un défilement en restant appuyé.
    if (isNav) attachEnergyDrag(barWrap);
  }
}

/* ------------------------------------------------ navigation consommation */

function defaultEnergyDay(month) {
  const now = new Date();
  if (Number(month) === now.getMonth() + 1) return isoDay(now);
  const last = new Date(now.getFullYear(), Number(month), 0).getDate();
  return isoDay(new Date(now.getFullYear(), Number(month) - 1, last));
}

function shiftEnergy(direction) {
  if (energyView === "week") shiftEnergyWeek(direction);
  else if (energyView === "month") shiftEnergyMonth(direction);
  else if (energyView === "day") shiftEnergyDay(direction);
}

function shiftEnergyWeek(direction) {
  // On change de semaine de référence (±1 semaine) : le bloc de 7
  // semaines affiché la suit.
  const window_ = energyPayload?.window;
  const base = parseDate(window_?.ref_iso || window_?.start_iso);
  if (!base || !window_) return;

  let target = addDays(base, direction * 7);
  const first = parseDate(window_.first_iso);
  const limit = parseDate(window_.limit_iso);
  if (first && target < first) target = first;
  if (limit && target > limit) target = limit;

  const iso = isoDay(target);
  if (iso === window_.ref_iso) return;
  energyDay = iso;
  energyMonth = target.getMonth() + 1;
  writePref("stats.energyDay", energyDay);
  writePref("stats.energyMonth", energyMonth);
  loadEnergy();
}

function shiftEnergyMonth(direction) {
  // On change de mois de référence (±1 mois) : le bloc de 7 mois la suit.
  // energyDay porte l'année (la navigation peut franchir le 1er janvier).
  const window_ = energyPayload?.window;
  const current = parseDate(energyDay) || new Date();
  const target = new Date(
    current.getFullYear(),
    current.getMonth() + direction,
    1
  );
  const key = `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, "0")}`;
  // Bornes de navigation calculées par le serveur (YYYY-MM).
  if (window_?.first && key < window_.first) return;
  if (window_?.limit && key > window_.limit) return;
  energyMonth = target.getMonth() + 1;
  energyDay = isoDay(target);
  writePref("stats.energyMonth", energyMonth);
  writePref("stats.energyDay", energyDay);
  loadEnergy();
}

function shiftEnergyDay(delta) {
  // Le serveur raisonne en numéro de jour dans le mois sélectionné, mais la
  // navigation doit pouvoir traverser la frontière du mois : quand le calcul
  // dépasse la fin (ou le début) du mois courant, on change de mois (et
  // d'année si besoin) au lieu de bloquer sur le dernier (ou le premier) jour.
  const current = parseDate(energyDay) || new Date();
  const target = new Date(
    current.getFullYear(),
    current.getMonth(),
    current.getDate() + delta
  );
  energyMonth = target.getMonth() + 1;
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  const day = Math.min(Math.max(target.getDate(), 1), lastDay);
  energyDay = isoDay(new Date(target.getFullYear(), target.getMonth(), day));
  writePref("stats.energyMonth", energyMonth);
  writePref("stats.energyDay", energyDay);
  loadEnergy();
}

function navDisabled(payload, direction) {
  const window_ = payload?.window;
  if (!window_) return true;
  if (payload.view === "week" || payload.view === "month") {
    // Navigation libre : on peut toujours se déplacer ; le serveur borne
    // la cible via first/limit si elle dépasse la plage disponible.
    return false;
  }
  if (payload.view === "day") {
    // Navigation libre : on peut toujours se déplacer, y compris au-delà
    // des frontières de mois (le changement de mois se fait dans
    // shiftEnergyDay). Le serveur renvoie une erreur si le mois cible n'a
    // aucune donnée, mais la navigation reste possible.
    return false;
  }
  return false;
}

function periodNavLabel(payload) {
  const window_ = payload?.window;
  if (!window_) return "";
  if (payload.view === "week") return window_.label || "";
  if (payload.view === "month") return window_.label || "";
  if (payload.view === "day") return dayWindowLabel(window_, energyDay);
  return "";
}

function attachEnergyDrag(wrap) {
  wrap.classList.add("is-draggable");
  // Seuil volontairement court : sur un téléphone, un glisser de 3 cm doit
  // suffire. Le geste reste horizontal uniquement pour laisser le
  // défilement vertical de la page (touch-action: pan-y).
  const threshold = 30;
  let startX = 0;
  let startY = 0;
  let lastDx = 0;
  let tracking = false;
  let moved = false;

  const finish = () => {
    tracking = false;
    wrap.classList.remove("is-dragging");
  };

  wrap.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    tracking = true;
    moved = false;
    lastDx = 0;
    startX = event.clientX;
    startY = event.clientY;
    wrap.classList.add("is-dragging");
    try {
      wrap.setPointerCapture(event.pointerId);
    } catch {
      /* capture non disponible (ancien WebView) : on continue sans */
    }
  });

  wrap.addEventListener("pointermove", (event) => {
    if (!tracking) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    lastDx = dx;
    if (!moved) {
      // On ne prend la main que sur un geste franchement horizontal :
      // le défilement vertical de la page reste possible.
      if (Math.abs(dx) < 8 || Math.abs(dx) <= Math.abs(dy)) return;
      moved = true;
    }
    if (Math.abs(dx) >= threshold) {
      finish();
      shiftEnergy(dx < 0 ? 1 : -1);
    }
  });

  wrap.addEventListener("pointerup", () => {
    // Repli : un glisser un peu court mais net est quand même pris en compte.
    if (tracking && moved && Math.abs(lastDx) >= 20) {
      finish();
      shiftEnergy(lastDx < 0 ? 1 : -1);
      return;
    }
    finish();
  });
  wrap.addEventListener("pointercancel", finish);
  wrap.addEventListener("pointerleave", () => {
    if (tracking && !moved) finish();
  });
  // Un glisser ne doit pas déclencher le clic de barre (info) à la fin.
  wrap.addEventListener(
    "click",
    (event) => {
      if (!moved) return;
      event.preventDefault();
      event.stopPropagation();
      moved = false;
    },
    true
  );
}

function energyTable(payload) {
  const summary = payload.summary || {};
  const rows = summary.by_year || [];
  if (!rows.length) return "";

  const view = payload.view;
  const window_ = payload.window || null;
  const firstHeader = {
    week: "Semaines",
    day: "Jour",
    month: "Mois",
    year: "Année",
  }[view] || "Période";
  const valueHeader =
    view === "week" || view === "month"
      ? "Total période"
      : view === "day"
        ? "Conso jour"
        : "Consommation";
  const partial = summary.partial
    ? `<p class="hint mt-2">Totaux à date : l’année en cours n’est pas terminée, la comparaison porte sur la même période.</p>`
    : "";

  return `<div class="energy-table-wrap">
    <table class="stats-table energy-table">
      <thead>
        <tr><th>${firstHeader}</th><th>${valueHeader}</th><th>Écart N-1</th></tr>
      </thead>
      <tbody>
        ${rows
          .map((row) => {
            const delta = row.delta_pct;
            return `<tr>
              <td class="energy-period-cell">${periodRowLabel(view, row.year, window_)}</td>
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

function periodRowLabel(view, year, window_) {
  if (view === "week" && window_) {
    return `<span class="period-name">${escapeHtml(window_.label || `Semaine ${window_.iso_week || ""}`)}</span><span class="period-year">${year}</span>`;
  }
  if (view === "month" && window_) {
    return `<span class="period-name">${escapeHtml(window_.range_label || window_.label || "")}</span><span class="period-year">${year}</span>`;
  }
  if (view === "day" && window_) {
    return `<span class="period-name">${escapeHtml(dayWindowLabel(window_, energyDay, year))}</span><span class="period-year">${year}</span>`;
  }
  return `<span class="period-name">${year}</span>`;
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

function dayWindowLabel(window_, energyDayIso, year = null) {
  if (!window_) return "";
  const date = parseDate(energyDayIso) || new Date();
  const displayYear = year ?? date.getFullYear();
  const month = (window_.month || energyMonth || 1) - 1;
  const start = new Date(displayYear, month, window_.start);
  const end = new Date(displayYear, month, window_.end);
  if (window_.start === window_.end) {
    return fmtDate(start, { day: "numeric", month: "long" });
  }
  return `${fmtDate(start)} → ${fmtDate(end)}`;
}

function energySubtitle(payload) {
  if (!payload) return "";
  const view = payload.view_label || "Mois";
  if (payload.view === "day") {
    return `${view} · ${payload.month_label || ""} · comparaison par année`;
  }
  if (payload.view === "week") {
    const label = payload.window?.label || view;
    return `${label} · comparaison par année`;
  }
  if (payload.view === "year") return `${view} · total à date · comparaison par année`;
  return `${view} · comparaison par année`;
}
