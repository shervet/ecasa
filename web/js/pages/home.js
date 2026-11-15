// Accueil : vue d'ensemble de la maison et réglage rapide.

import { icon, modeIcon, weatherIconName } from "../icons.js";
import { openDeviceSheet } from "../components/device-sheet.js";
import { setPendingPlanningRoom } from "./planning.js";
import { applyNow, refreshState, roomSummary, store, subscribe } from "../state.js";

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
        <section id="homeCpu"></section>
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

    const unsub = subscribe((_, reason) => {
      if (String(reason || "").startsWith("pref:")) paint();
    });
    view.__homeUnsub = unsub;
  },

  unmount() {
    const view = document.getElementById("view");
    if (view?.__homeUnsub) {
      try { view.__homeUnsub(); } catch { /* ignore */ }
      view.__homeUnsub = null;
    }
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
  paintCpu();
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

  const title = average !== null ? `La maison est à ${fmtTemp(average)} °C` : "Bienvenue dans eCasa";
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
        ${weather ? `<button class="pill pill-ghost" type="button" id="weatherToggle" style="cursor:pointer">${icon("cloud", 13)} Extérieur ${fmtTemp(weather.temp)} °C</button>` : ""}
      </div>
    </div>
      <div class="hero-stats">
      <div class="hero-stat"><div class="v">${ambients.length}/${summaries.length}</div><div class="l">Pièces suivies</div></div>
      <div class="hero-stat"><div class="v">${fmtTemp(targetAverage)} °C</div><div class="l">Consigne moyenne</div></div>
      <div class="hero-stat"><div class="v">${(state.devices || []).length}</div><div class="l">Appareils</div></div>
      <div class="hero-stat"><div class="v">${state.status?.last_poll ? fmtTime(state.status.last_poll) : "—"}</div><div class="l">Dernière lecture</div></div>
    </div>
  `;

  // Boîte météo en position fixed (par-dessus, non rognée par le hero).
  document.querySelectorAll("#weatherBoxFloating").forEach((el) => el.remove());
  if (weather) {
    const box = document.createElement("div");
    box.id = "weatherBoxFloating";
    box.className = "weather-card";
    box.style.cssText = "position:fixed;z-index:1000;display:none;min-width:280px;max-width:360px;padding:14px 16px;background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow-lg);";
    box.innerHTML = `
      <div class="weather-head">
        <div>
          <div class="card-title">${icon("cloud-sun", 17)} Météo de la semaine</div>
          <p class="hint mt-1">${escapeHtml(weather.city || "Prévisions locales")} · ${escapeHtml(weather.daily_source || weather.source || "")}</p>
        </div>
        <span class="pill pill-ghost">${icon(weatherIconName(weather.code), 14)} ${fmtTemp(weather.temp)} °C</span>
      </div>
      ${Array.isArray(weather.daily) && weather.daily.length
        ? `<div class="weather-days" style="margin-top:8px">
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
    `;
    document.body.appendChild(box);

    const toggle = document.getElementById("weatherToggle");
    if (toggle) {
      toggle.onclick = (e) => {
        const open = box.style.display !== "none";
        if (open) {
          box.style.display = "none";
        } else {
          const rect = toggle.getBoundingClientRect();
          box.style.display = "block";
          const bw = box.offsetWidth || 280;
          let left = rect.right - bw;
          left = Math.max(8, Math.min(left, window.innerWidth - bw - 8));
          let top = rect.bottom + 6;
          if (top + box.offsetHeight > window.innerHeight - 8) {
            top = rect.top - box.offsetHeight - 6;
          }
          box.style.left = left + "px";
          box.style.top = top + "px";
        }
      };
    }
    // Close on outside click (avoid duplicate listeners)
    if (!window.__weatherClickBound) {
      window.__weatherClickBound = true;
      document.addEventListener("click", (e) => {
        const b = document.getElementById("weatherBoxFloating");
        const t = document.getElementById("weatherToggle");
        if (b && b.style.display !== "none" && !b.contains(e.target) && e.target !== t) {
          b.style.display = "none";
        }
      });
    }
  }
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
  host.innerHTML = `${icon("info", 17)}<span>eCasa applique le planning automatiquement. Dernière synchronisation ${state.status?.last_poll ? fmtTime(state.status.last_poll) : "à venir"}.</span>`;
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
          <div class="room-target" data-goto-room="${room.id}" role="button" tabindex="0" title="Ouvrir le planning de cette pièce">
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

  host.querySelectorAll("[data-goto-room]").forEach((target) => {
    const go = () => {
      const roomId = Number(target.dataset.gotoRoom);
      setPendingPlanningRoom(roomId);
      location.hash = `#/planning/room:${roomId}`;
    };
    target.addEventListener("click", go);
    target.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        go();
      }
    });
  });
}

function paintCpu() {
  const host = document.getElementById("homeCpu");
  if (!host) return;
  const prefs = store.prefs || {};
  const showLoad = prefs.showCpuLoad !== false;
  const showTemp = prefs.showCpuTemp !== false;
  if (!showLoad && !showTemp) {
    host.innerHTML = "";
    return;
  }
  const cpu = store.state?.cpu || {};
  const loads = Array.isArray(cpu.load) ? cpu.load : [];
  const temps = Array.isArray(cpu.temp) ? cpu.temp : [];
  const lastLoad = [...loads].reverse().find((value) => Number.isFinite(value));
  const lastTemp = [...temps].reverse().find((value) => Number.isFinite(value));

  const cards = [];
  if (showLoad) {
    cards.push(`
      <div class="card card-pad cpu-card">
        <div class="row-between" style="margin-bottom:6px">
          <span class="card-title">${icon("gauge", 16)} Charge CPU</span>
          <strong>${lastLoad !== undefined ? `${Number(lastLoad).toFixed(1)} %` : "—"}</strong>
        </div>
        <div class="cpu-spark-wrap" style="color:var(--primary)">${sparkline(loads)}</div>
        <p class="tiny muted" style="margin-top:4px">60 dernières minutes · ${loads.length} mesure${loads.length > 1 ? "s" : ""}</p>
      </div>`);
  }
  if (showTemp) {
    cards.push(`
      <div class="card card-pad cpu-card">
        <div class="row-between" style="margin-bottom:6px">
          <span class="card-title">${icon("thermometer", 16)} Température CPU</span>
          <strong>${lastTemp !== undefined ? `${Number(lastTemp).toFixed(1)} °C` : "—"}</strong>
        </div>
        <div class="cpu-spark-wrap" style="color:var(--warn)">${sparkline(temps)}</div>
        <p class="tiny muted" style="margin-top:4px">60 dernières minutes · ${temps.length} mesure${temps.length > 1 ? "s" : ""}</p>
      </div>`);
  }
  host.innerHTML = cards.length ? `<div class="cpu-grid">${cards.join("")}</div>` : "";
}

function sparkline(values) {
  const list = Array.isArray(values) ? values : [];
  const points = [];
  for (let i = 0; i < list.length; i++) {
    if (Number.isFinite(list[i])) points.push({ i, v: Number(list[i]) });
  }
  if (!points.length) {
    return `<div class="empty" style="padding:10px 8px"><p>Aucune mesure pour le moment.</p></div>`;
  }
  let min = Math.min(...points.map((p) => p.v));
  let max = Math.max(...points.map((p) => p.v));
  if (max - min < 0.5) {
    const center = (max + min) / 2;
    min = center - 0.5;
    max = center + 0.5;
  }
  const pad = (max - min) * 0.1;
  min -= pad;
  max += pad;
  const W = 220;
  const H = 52;
  const M = 4;
  const x = (i) => M + ((W - 2 * M) * i) / Math.max(1, list.length - 1);
  const y = (v) => H - M - ((H - 2 * M) * (v - min)) / (max - min);
  const d = points
    .map((p, idx) => `${idx === 0 ? "M" : "L"} ${x(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`)
    .join(" ");
  const last = points[points.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" class="cpu-spark" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" />
    <circle cx="${x(last.i).toFixed(1)}" cy="${y(last.v).toFixed(1)}" r="2.4" fill="currentColor" />
  </svg>`;
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
              <div class="li-title${Number(device.enabled) === 0 ? " is-off" : ""}">${escapeHtml(device.name)}</div>
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
