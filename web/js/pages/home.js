// Accueil : vue d'ensemble de la maison et réglage rapide.

import { icon, modeIcon, weatherIconName } from "../icons.js";
import { openDeviceSheet } from "../components/device-sheet.js";
import { applyNow, refreshState, roomSummary, store } from "../state.js";

let weather = null;

async function loadWeather() {
  try {
    const data = await fetch("/api/weather").then((r) => r.json());
    const hasTemp = data?.temp !== null && data?.temp !== undefined;
    const hasDaily = Array.isArray(data?.daily) && data.daily.length > 0;
    weather = data?.ok && (hasTemp || hasDaily) ? data : null;
  } catch {
    weather = null;
  }
}

import {
  escapeHtml,
  fmtDateLong,
  fmtTemp,
  fmtTime,
  greeting,
  inWords,
  modeColor,
  modeLabel,
  parseDate,
  sortByPosition,
  weekdayShort,
} from "../utils.js";

export default {
  title: "Accueil",

  async mount(view) {
    view.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div>
            <h1 class="page-title" id="homeGreeting">${greeting()}</h1>
            <p class="page-sub" id="homeDate">${escapeHtml(fmtDateLong(new Date()))}</p>
          </div>
          <div class="page-actions">
            <button class="btn" type="button" id="homeRefresh">${icon("refresh", 17)} Actualiser</button>
            <button class="btn btn-primary" type="button" id="homeApply">${icon("zap", 17)} Appliquer</button>
          </div>
        </div>

        <section class="hero" id="homeHero"></section>
        <div class="strip" id="homeStrip"></div>
        <section class="room-grid" id="homeRooms"></section>
        <section id="homeOther"></section>
      </div>
    `;

    view.querySelector("#homeRefresh")?.addEventListener("click", async () => {
      await refreshState({ silent: false });
      paint();
    });

    view.querySelector("#homeApply")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await applyNow();
      } catch {
        /* toast déjà affiché */
      } finally {
        button.disabled = false;
      }
    });

    paint();
    loadWeather().then(() => paint());
  },

  onState() {
    // Ne pas écraser le DOM pendant une interaction (tap/bouton) en cours :
    // le prochain poll rattrapera la mise à jour.
    const active = document.activeElement;
    const view = document.getElementById("view");
    if (active && view && view.contains(active)) return;
    paint();
  },
};

function paint() {
  paintHero();
  paintStrip();
  paintRooms();
  paintOther();
}

function paintHero() {
  const host = document.getElementById("homeHero");
  if (!host) return;
  const state = store.state;

  if (!state) {
    host.innerHTML = `<div class="skeleton" style="height:190px"></div>`;
    return;
  }

  const rooms = sortByPosition(state.rooms || []).filter((room) =>
    (state.devices || []).some((device) => Number(device.room_id) === Number(room.id))
  );
  const summaries = rooms.map((room) => ({ room, summary: roomSummary(state, room) })).filter((item) => item.summary);

  const ambients = summaries
    .map((item) => item.summary.ambient)
    .filter((value) => Number.isFinite(value));
  const targets = summaries
    .map((item) => item.summary.target)
    .filter((value) => Number.isFinite(value));
  const average = ambients.length ? ambients.reduce((a, b) => a + b, 0) / ambients.length : null;
  const targetAverage = targets.length ? targets.reduce((a, b) => a + b, 0) / targets.length : null;

  const nextChanges = (state.devices || [])
    .filter((device) => device.next && Number.isFinite(Number(device.next.in_minutes)))
    .sort((a, b) => Number(a.next.in_minutes) - Number(b.next.in_minutes));
  const next = nextChanges[0];

  const title = average !== null ? `La maison est à ${fmtTemp(average)} °C` : "Bienvenue dans MeCasa";
  const subtitle = next
    ? `Prochain changement ${inWords(next.next.in_minutes)} pour ${next.name} : ${next.next.describe || "—"}.`
    : "Aucun changement prévu dans les prochaines heures. Le planning veille.";

  host.innerHTML = `
    <div class="hero-top">
      <div>
        <span class="hero-kicker">${icon("house", 14)} Semaine ${state.week} · ISO ${state.iso_week}</span>
        <h2 class="hero-title">${escapeHtml(title)}</h2>
        <p class="hero-text">${escapeHtml(subtitle)}</p>
      </div>
      <div class="row wrap" style="gap:6px">
        ${state.status?.mock ? '<span class="pill pill-warn">Simulation</span>' : '<span class="pill pill-ok">Réel</span>'}
        <span class="pill pill-ghost">${state.status?.running ? "Moteur actif" : "Moteur arrêté"}</span>
        ${weather ? `<details class="weather-details" style="display:inline-block;position:relative">
  <summary class="pill pill-ghost" style="cursor:pointer;list-style:none">${icon("cloud", 13)} Extérieur ${fmtTemp(weather.temp)} °C</summary>
  <div id="weatherBox" class="weather-card" style="position:absolute;right:0;top:100%;z-index:50;min-width:280px;margin-top:6px">
    <div class="weather-head">
      <div>
        <div class="card-title">${icon("cloud-sun", 17)} Météo de la semaine</div>
        <p class="hint mt-1">${escapeHtml(weather.city || "Prévisions locales")} · ${escapeHtml(weather.daily_source || weather.source || "")}</p>
      </div>
      <span class="pill pill-ghost">${icon(weatherIconName(weather.code), 14)} ${fmtTemp(weather.temp)} °C</span>
    </div>
    ${Array.isArray(weather.daily) && weather.daily.length
      ? `<div class="weather-days">
          ${weather.daily.map((day) => {
            const date = parseDate(day.date);
            const index = date ? (date.getDay() + 6) % 7 : 0;
            const today = date && date.toDateString() === new Date().toDateString();
            return `<div class="weather-day${today ? " is-today" : ""}">
              <span class="weather-dow">${weekdayShort(index)}</span>
              <span class="weather-icon">${icon(weatherIconName(day.code), 19)}</span>
              <span class="weather-max">${fmtTemp(day.tmax, 0)}°</span>
              <span class="weather-min">${fmtTemp(day.tmin, 0)}°</span>
            </div>`;
          }).join("")}
        </div>`
      : ""}
  </div>
</details>` : ""}
      </div>
    </div>
      <div class="hero-stats">
      <div class="hero-stat"><div class="v">${ambients.length}/${summaries.length}</div><div class="l">Pièces suivies</div></div>
      <div class="hero-stat"><div class="v">${fmtTemp(targetAverage)} °C</div><div class="l">Consigne moyenne</div></div>
      <div class="hero-stat"><div class="v">${(state.devices || []).length}</div><div class="l">Appareils</div></div>
      <div class="hero-stat"><div class="v">${state.status?.last_poll ? fmtTime(state.status.last_poll) : "—"}</div><div class="l">Dernière lecture</div></div>
    </div>
  `;
}

function paintStrip() {
  const host = document.getElementById("homeStrip");
  if (!host) return;
  const state = store.state;
  if (!state) {
    host.innerHTML = "";
    return;
  }

  const action = state.status?.last_action;
  const error = state.status?.last_error;
  if (error) {
    host.innerHTML = `${icon("alert", 17)}<span><strong>Dernière erreur :</strong> ${escapeHtml(error)}</span>`;
    return;
  }
  if (action) {
    host.innerHTML = `${icon("check", 17)}<span>${escapeHtml(action)}</span>`;
    return;
  }
  host.innerHTML = `${icon("info", 17)}<span>MeCasa applique le planning automatiquement. Dernière synchronisation ${state.status?.last_poll ? fmtTime(state.status.last_poll) : "à venir"}.</span>`;
}

function paintRooms() {
  const host = document.getElementById("homeRooms");
  if (!host) return;
  const state = store.state;
  if (!state) {
    host.innerHTML = Array.from({ length: 3 }, () => '<div class="skeleton" style="height:220px"></div>').join("");
    return;
  }

  const rooms = sortByPosition(state.rooms || []);
  const cards = [];

  for (const room of rooms) {
    const summary = roomSummary(state, room);
    if (!summary) continue;

    const mode = summary.mode || "off";
    const next = summary.next;
    const override = summary.override;
    const label = modeLabel(mode, store.meta);

    cards.push(`
      <article class="room-card" data-mode="${escapeHtml(mode)}">
        <div class="room-head">
          <div>
            <div class="room-name">${escapeHtml(room.name)}</div>
            <div class="room-devices">${summary.devices.length} appareil${summary.devices.length > 1 ? "s" : ""} · ${escapeHtml(summary.primary?.name || "")}</div>
          </div>
          <span class="pill" style="background:color-mix(in srgb, ${modeColor(mode)} 14%, transparent);color:${modeColor(mode)}">${modeIcon(mode, 13)} ${escapeHtml(label)}</span>
        </div>

        <div class="room-main">
          <div class="room-temp">
            <span class="value">${fmtTemp(summary.ambient)}</span><span class="unit">°C</span>
          </div>
          <div class="room-target">
            Consigne
            <strong>${fmtTemp(summary.target)} °C</strong>
            ${summary.until ? `jusqu'à ${fmtTime(summary.until)}` : next ? `${inWords(next.in_minutes)} → ${fmtTemp(next.temp)} °C` : "planning"}
          </div>
        </div>

        <div class="room-foot">
          <div class="row" style="gap:6px;min-width:0">
            ${override ? `<span class="override-flag">${icon("clock", 13)} Forçage</span>` : `<span class="tiny muted truncate">${escapeHtml(summary.source || "planning")}</span>`}
          </div>
          <div class="room-actions">
            <button class="btn btn-sm" type="button" data-device="${summary.primary?.device_id}">
              ${icon("sliders", 15)} Régler
            </button>
          </div>
        </div>
      </article>
    `);
  }

  host.innerHTML = cards.length
    ? cards.join("")
    : `<div class="empty" style="grid-column:1/-1">
        <span class="empty-icon">${icon("house", 24)}</span>
        <strong>Aucune pièce équipée</strong>
        <p>Ajoute un appareil depuis Réglages → Pièces &amp; appareils, puis lance une synchronisation.</p>
      </div>`;

  host.querySelectorAll("[data-device]").forEach((button) => {
    button.addEventListener("click", () => openDeviceSheet(button.dataset.device));
  });
}

function paintOther() {
  const host = document.getElementById("homeOther");
  if (!host) return;
  const state = store.state;
  if (!state) {
    host.innerHTML = "";
    return;
  }

  const roomIds = new Set((state.rooms || []).map((room) => Number(room.id)));
  const others = (state.devices || []).filter((device) => !roomIds.has(Number(device.room_id)));
  if (!others.length) {
    host.innerHTML = "";
    return;
  }

  host.innerHTML = `<div class="card">
    <div class="card-head">
      <div class="card-title">${icon("thermometer", 18)} Appareils sans pièce</div>
      <span class="tiny muted">${others.length}</span>
    </div>
    <div class="list">
      ${others
        .map(
          (device) => `<div class="list-row">
            <div class="li-main">
              <div class="li-title">${escapeHtml(device.name)}</div>
              <div class="li-sub">${fmtTemp(device.current?.ambient)} °C · ${escapeHtml(modeLabel(device.desired?.mode || device.current?.mode, store.meta))}</div>
            </div>
            <button class="btn btn-sm" type="button" data-device="${device.device_id}">${icon("sliders", 15)} Régler</button>
          </div>`
        )
        .join("")}
    </div>
  </div>`;

  host.querySelectorAll("[data-device]").forEach((button) => {
    button.addEventListener("click", () => openDeviceSheet(button.dataset.device));
  });
}
