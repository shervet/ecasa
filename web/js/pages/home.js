// Accueil : vue d'ensemble de la maison et réglage rapide.

import { put } from "../api.js";
import { icon, modeIcon, weatherIconName } from "../icons.js";
import { openDeviceSheet } from "../components/device-sheet.js";
import { openSheet } from "../sheet.js";
import { toastError } from "../toast.js";
import { setPendingPlanningRoom } from "./planning.js";
import { refreshState, roomSummary, store, subscribe } from "../state.js";

let weather = null;

function presenceInfo() {
  const state = store.state;
  if (state?.presence && typeof state.presence === "object") return state.presence;
  const absent =
    String(state?.settings?.presence_mode || "present").toLowerCase() === "absent";
  return { active: absent, reason: absent ? "manual" : "present" };
}

function isAbsent() {
  return presenceInfo().active === true;
}

function isVacation() {
  const presence = presenceInfo();
  return presence.active === true && presence.reason === "vacation";
}

/** Trio Présent/Absent/Vacances : un seul bouton actif. */
function syncPresence() {
  const absent = isAbsent();
  const vacation = isVacation();
  for (const [id, active] of [
    ["homePresent", !absent],
    ["homeAbsent", absent && !vacation],
    ["homeVacation", vacation],
  ]) {
    const button = document.getElementById(id);
    if (!button) continue;
    button.classList.toggle("btn-primary", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

/** Ids de pièces pilotées par Pronote (relus à chaque rendu : la config peut changer). */
function pronoteRoomIds() {
  const ids = store.config?.pronote_room_ids;
  return Array.isArray(ids)
    ? ids.map((value) => Number(value)).filter((value) => Number.isFinite(value))
    : [];
}

/** Nom d'un modèle, fourni par /api/state (pas de requête dédiée à chaque poll). */
function modelName(modelId) {
  return store.state?.model_names?.[String(modelId)] || "Modèle";
}

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
  fmtDate,
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
    const absent = isAbsent();
    view.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div>
            <h1 class="page-title" id="homeGreeting">${greeting()}</h1>
            <p class="page-sub" id="homeDate">${escapeHtml(fmtDateLong(new Date()))}</p>
          </div>
          <div class="page-actions" role="group" aria-label="Présence">
            <button class="btn${absent ? "" : " btn-primary"}" type="button" id="homePresent" aria-pressed="${absent ? "false" : "true"}">Présent</button>
            <button class="btn${absent && !isVacation() ? " btn-primary" : ""}" type="button" id="homeAbsent" aria-pressed="${absent && !isVacation() ? "true" : "false"}">Absent</button>
            <button class="btn${isVacation() ? " btn-primary" : ""}" type="button" id="homeVacation" aria-pressed="${isVacation() ? "true" : "false"}">Vacances</button>
          </div>
        </div>

        <section class="hero" id="homeHero"></section>
        <div class="strip" id="homeStrip"></div>
        <section class="room-grid" id="homeRooms"></section>
        <section id="homeCpu"></section>
        <section id="homeDisk"></section>
        <section id="homeOther"></section>
      </div>
    `;

    const setPresence = async (event) => {
      const button = event.currentTarget;
      const mode = button.id === "homeAbsent" ? "absent" : "present";
      if (button.id === "homeVacation") {
        openVacationSheet();
        return;
      }
      if (isAbsent() === (mode === "absent") && !isVacation()) return;
      button.disabled = true;
      try {
        await put("/api/settings", {
          presence_mode: mode,
          absence_start: "",
          absence_until: "",
        });
        await refreshState({ silent: true });
        paint();
      } catch (err) {
        toastError(err);
      } finally {
        button.disabled = false;
      }
    };
    view.querySelector("#homePresent")?.addEventListener("click", setPresence);
    view.querySelector("#homeAbsent")?.addEventListener("click", setPresence);
    view.querySelector("#homeVacation")?.addEventListener("click", setPresence);

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

function todayISO() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function openVacationSheet() {
  const presence = presenceInfo();
  const start = presence.start || todayISO();
  const until = presence.until || "";
  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <p class="hint">La maison passe en hors gel pendant toute la période. Le planning Pronote n'est pas modifié : il reprend automatiquement au retour.</p>
    <label class="field">
      <span class="label">Début des vacances</span>
      <input class="input" type="date" data-vacation-start value="${escapeHtml(start)}">
    </label>
    <label class="field">
      <span class="label">Fin des vacances <span class="opt">incluse</span></span>
      <input class="input" type="date" data-vacation-until value="${escapeHtml(until)}">
    </label>
    <p class="hint" id="vacationError" hidden style="color:var(--bad)"></p>
  `;
  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%;flex-wrap:wrap";
  footer.innerHTML = `
    <button class="btn" type="button" data-vacation-clear>Annuler les vacances</button>
    <button class="btn btn-primary" type="button" data-vacation-save>Programmer</button>
  `;

  const sheet = openSheet({
    title: "Mode vacances",
    sub: "Absence programmée, avec retour automatique",
    body,
    footer,
  });
  if (!sheet) return;

  const errorNode = body.querySelector("#vacationError");
  const showError = (message) => {
    if (!errorNode) return;
    errorNode.textContent = message;
    errorNode.hidden = !message;
  };

  const save = async (event) => {
    const startValue = String(body.querySelector("[data-vacation-start]")?.value || "");
    const untilValue = String(body.querySelector("[data-vacation-until]")?.value || "");
    if (!startValue) {
      showError("Choisis une date de début.");
      return;
    }
    if (untilValue && untilValue < startValue) {
      showError("La fin doit être après le début.");
      return;
    }
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await put("/api/settings", {
        presence_mode: "present",
        absence_start: startValue,
        absence_until: untilValue,
      });
      await refreshState({ silent: true });
      paint();
      sheet.close();
    } catch (err) {
      toastError(err);
    } finally {
      button.disabled = false;
    }
  };

  const clear = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await put("/api/settings", {
        presence_mode: "present",
        absence_start: "",
        absence_until: "",
      });
      await refreshState({ silent: true });
      paint();
      sheet.close();
    } catch (err) {
      toastError(err);
    } finally {
      button.disabled = false;
    }
  };

  footer.querySelector("[data-vacation-save]")?.addEventListener("click", save);
  footer.querySelector("[data-vacation-clear]")?.addEventListener("click", clear);
}

function paint() {
  syncPresence();
  paintHero();
  paintStrip();
  paintRooms();
  paintCpu();
  paintDisk();
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
        ${state.presence?.active
          ? `<span class="pill pill-warn">${state.presence.reason === "vacation"
              ? `Vacances${state.presence.until ? ` jusqu'au ${fmtDate(state.presence.until)}` : ""}`
              : "Absent"}</span>`
          : ""}
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
  const pronoteIds = pronoteRoomIds();

  for (const room of rooms) {
    const summary = roomSummary(state, room);
    if (!summary) continue;

    const mode = summary.mode || "off";
    const next = summary.next;
    const override = summary.override;
    const humidity =
      Number.isFinite(summary.humidity) && summary.humidity > 0
        ? Math.round(summary.humidity)
        : null;
    const label = modeLabel(mode, store.meta);

    // Indicateurs de liaison : modèle de planning et/ou Pronote.
    const badges = [];
    if (pronoteIds.includes(Number(room.id))) {
      badges.push(
        `<span class="pill pill-accent" title="Planning lié à Pronote">${icon("school", 13)} Pronote</span>`
      );
    }
    const link = state.model_links?.[String(room.id)];
    if (link) {
      const name = modelName(link.model_id);
      badges.push(
        `<span class="pill pill-model" title="Planning lié au modèle « ${escapeHtml(name)} »">${icon("link", 13)} ${escapeHtml(name)}</span>`
      );
    }

    // Snapshot enrichi : heating_curve peut être absent (ancien snapshot / pièce non équipée).
    const hc = room?.heating_curve;
    const advanceMinutes =
      hc?.enabled === true && Number(hc?.next_advance?.minutes) > 0
        ? Math.round(Number(hc.next_advance.minutes))
        : 0;
    const advanceBadge = advanceMinutes > 0
      ? `<span class="pill" title="Avance appliquée à la prochaine montée">⏱ ${advanceMinutes} min</span>`
      : "";

    cards.push(`
      <article class="room-card" data-mode="${escapeHtml(mode)}">
        <div class="room-head">
          <div>
            <div class="room-name">${escapeHtml(room.name)}</div>
            <div class="room-devices">${summary.devices.length} appareil${summary.devices.length > 1 ? "s" : ""} · ${escapeHtml(summary.primary?.name || "")}</div>
          </div>
          <div class="row wrap" style="gap:6px;justify-content:flex-end">
            ${badges.join("")}
            <span class="pill" style="background:color-mix(in srgb, ${modeColor(mode)} 14%, transparent);color:${modeColor(mode)}">${modeIcon(mode, 13)} ${escapeHtml(label)}</span>
          </div>
        </div>

        <div class="room-main">
          <div class="room-temp" data-cal-room="${room.id}" role="button" tabindex="0" title="Voir le planning de la semaine">
            <span class="value">${fmtTemp(summary.ambient)}</span><span class="unit">°C</span>
          </div>
          <div class="room-target" data-goto-room="${room.id}" role="button" tabindex="0" title="Ouvrir le planning de cette pièce">
            <span class="room-target-label">Consigne</span>
            <span class="room-target-value">
              <strong>${fmtTemp(summary.target)} °C</strong>
              ${humidity !== null ? `<span class="room-humidity" title="Humidité relative">${humidity} %</span>` : ""}
            </span>
            <span class="room-target-meta">
              ${advanceBadge}
              ${summary.until ? `jusqu'à ${fmtTime(summary.until)}` : next ? `${inWords(next.in_minutes)} → ${fmtTemp(next.temp)} °C` : "planning"}
            </span>
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

  host.querySelectorAll("[data-cal-room]").forEach((node) => {
    const open = () => openPlanningCalendar(Number(node.dataset.calRoom));
    node.addEventListener("click", open);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });
}

function slotAt(daySlots, hour) {
  let active = null;
  for (const slot of daySlots || []) {
    const [h] = String(slot.time).split(":").map(Number);
    if (Number.isFinite(h) && h <= hour) active = slot;
    else break;
  }
  return active;
}

function calendarCellClass(slot) {
  if (!slot || !slot.mode || slot.mode === "off") return "pl-off";
  const temp = Number(slot.temp);
  if (!Number.isFinite(temp)) return "pl-off";
  if (temp < 18) return "pl-cold";
  if (temp <= 20) return "pl-mild";
  return "pl-hot";
}

/** Fenêtre « calendrier » : jours en colonnes, heures en lignes, températures en cases. */
function openPlanningCalendar(roomId) {
  const state = store.state;
  const room = (state?.rooms || []).find((item) => Number(item.id) === Number(roomId));
  if (!room) return;
  const summary = roomSummary(state, room);
  // Même appareil que la page Planning (actionneur, puis thermostat, puis 1er).
  const programs = summary?.primary?.programs || {};
  let week = state?.week === "B" ? "B" : "A";

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <div class="segmented" data-cal-week>
      <button type="button" data-cal-w="A" class="${week === "A" ? "is-active" : ""}">Semaine A</button>
      <button type="button" data-cal-w="B" class="${week === "B" ? "is-active" : ""}">Semaine B</button>
    </div>
    <div class="planning-cal-wrap" data-cal-wrap></div>
    <p class="hint">Cases colorées par température : <span class="pl-dot pl-cold"></span> &lt; 18 °C,
      <span class="pl-dot pl-mild"></span> 18–20 °C, <span class="pl-dot pl-hot"></span> &gt; 20 °C,
      <span class="pl-dot pl-off"></span> arrêt.</p>
  `;

  const sheet = openSheet({
    title: `Planning · ${room.name}`,
    sub: "Semaine type : jours en colonnes, heures en lignes",
    body,
  });

  const paint = () => {
    const days = programs[week] || [];
    const wrap = body.querySelector("[data-cal-wrap]");
    if (!wrap) return;
    let html = '<table class="planning-cal"><thead><tr><th></th>';
    for (let d = 0; d < 7; d += 1) {
      html += `<th>${weekdayShort(d)}</th>`;
    }
    html += "</tr></thead><tbody>";
    // Heures « résolues » : une heure sans planning garde le dernier réglage connu
    // (report en avant, y compris sur minuit grâce au report du jour précédent).
    // Initialisation : on prend le dernier réglage connu de la fin de semaine (jour 6)
    // qui est le jour précédant chronologiquement lundi 00h dans un motif hebdomadaire répété.
    let carry = null;
    const day6Slots = days[6] || [];
    for (let hour = 23; hour >= 0; hour -= 1) {
      const actual = slotAt(day6Slots, hour);
      if (actual) {
        carry = actual;
        break;
      }
    }
    const resolved = [];
    for (let d = 0; d < 7; d += 1) {
      const hours = [];
      for (let hour = 0; hour < 24; hour += 1) {
        const actual = slotAt(days[d], hour);
        if (actual) carry = actual;
        hours.push({ slot: actual || carry || null, inherited: !actual && Boolean(carry) });
      }
      resolved.push(hours);
    }

    // Regroupe les heures consécutives ayant la même température de display.
// Laisser le mode changer au sein d'un groupe (règle B).
    const columns = [];
    for (let d = 0; d < 7; d += 1) {
      const groups = [];
      for (let hour = 0; hour < 24; hour += 1) {
        const { slot, inherited } = resolved[d][hour];
        const mode = slot && slot.mode ? slot.mode : "off";
        const temp = slot ? Number(slot.temp) : null;
        
        const group = groups[groups.length - 1];
        // Règle B: même température → même groupe, même si le mode change
        if (group && group.temps[group.temps.length - 1] === temp) {
          // Même température que le dernier horaire ajouté → agrandir le groupe
          group.end = hour;
          group.slots.push(slot);
          group.temps.push(temp);
          group.inherited.push(inherited);
        } else {
          // Nouveau groupe : déterminer la classe de couleur
          let cls;
          if (mode === "off") {
            cls = "pl-off";
          } else if (!Number.isFinite(temp)) {
            cls = "pl-off";
          } else if (temp < 18) {
            cls = "pl-cold";
          } else if (temp <= 20) {
            cls = "pl-mild";
          } else {
            cls = "pl-hot";
          }
          groups.push({
            start: hour,
            end: hour,
            cls,
            mode, // mode de la première heure du groupe
            slots: [slot],
            temps: [temp],
            inherited: [inherited],
          });
        }
      }
      columns.push(groups);
    }

    // Vérifier si des températures sontKnown pour chaque journée
    const dayKnownTemp = new Array(7).fill(false);
    for (let d = 0; d < 7; d += 1) {
      for (const group of columns[d]) {
        for (const t of group.temps) {
          if (Number.isFinite(t)) {
            dayKnownTemp[d] = true;
            break;
          }
        }
        if (dayKnownTemp[d]) break;
      }
    }

    for (let hour = 0; hour < 24; hour += 1) {
      html += `<tr><th>${String(hour).padStart(2, "0")}h</th>`;
      for (let d = 0; d < 7; d += 1) {
        const dayGroups = columns[d];
        const group = dayGroups.find((item) => item.start === hour);
        if (!group) continue;

        // Chaque heure appartient à une boîte (rule C)
        // Construire le texte
        let text;
        if (dayKnownTemp[d]) {
          // Montrer la température si connue pour la journée
          if (Number.isFinite(group.temps[group.temps.length - 1])) {
            text = `${fmtTemp(group.temps[group.temps.length - 1], 0)}°`;
          } else {
            text = "—";
          }
        } else {
          text = "—";
        }
        
        // Build tooltip with per-hour details (rule E)
        const stamps = [];
        for (let h = group.start; h <= group.end; h += 1) {
          const slot = group.slots[h - group.start];
          const kept = group.inherited[h - group.start];
          const s = `${weekdayShort(d)} ${String(h).padStart(2, "0")}h`;
          if (slot) {
            if (kept) {
              stamps.push(`${s} maintien (fin de semaine précédente) · ${fmtTemp(slot.temp)} °C`);
            } else {
              stamps.push(`${s} ${modeLabel(slot.mode, store.meta)} · ${fmtTemp(slot.temp)} °C`);
            }
          } else {
            stamps.push(`${s} arrêt`);
          }
        }
        const title = escapeHtml(stamps.join("\n"));
        
        const span = group.end - group.start + 1;
        const rowspan = span > 1 ? ` rowspan="${span}"` : "";
        html += `<td${rowspan}><span class="pl-cell ${group.cls}" title="${title}">${text}</span></td>`;
      }
      html += "</tr>";
    }
    html += "</tbody></table>";
    wrap.innerHTML = html;
    body.querySelectorAll("[data-cal-w]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.calW === week);
    });
  };

  body.querySelectorAll("[data-cal-w]").forEach((btn) => {
    btn.addEventListener("click", () => {
      week = btn.dataset.calW === "B" ? "B" : "A";
      paint();
    });
  });
  paint();
  return sheet;
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
        ${sparkline(loads, "%", "var(--primary)")}
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
        ${sparkline(temps, "°C", "var(--warn)")}
        <p class="tiny muted" style="margin-top:4px">60 dernières minutes · ${temps.length} mesure${temps.length > 1 ? "s" : ""}</p>
      </div>`);
  }
  host.innerHTML = cards.length ? `<div class="cpu-grid">${cards.join("")}</div>` : "";
}

function sparkline(values, unit = "", color = "currentColor") {
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
  const fmtAxis = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    const digits = abs >= 100 ? 0 : (abs >= 10 ? 0 : 1);
    return `${n.toFixed(digits)} ${unit}`.trim();
  };
  const mid = (min + max) / 2;
  const svg = `<svg viewBox="0 0 ${W} ${H}" class="cpu-spark" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" />
    <circle cx="${x(last.i).toFixed(1)}" cy="${y(last.v).toFixed(1)}" r="2.4" fill="currentColor" />
  </svg>`;
  return `<div class="cpu-chart" style="color:${color}">
    <div class="cpu-yaxis">
      <span>${escapeHtml(fmtAxis(max))}</span>
      <span>${escapeHtml(fmtAxis(mid))}</span>
      <span>${escapeHtml(fmtAxis(min))}</span>
    </div>
    <div class="cpu-spark-wrap">${svg}</div>
  </div>`;
}

function paintDisk() {
  const host = document.getElementById("homeDisk");
  if (!host) return;
  const prefs = store.prefs || {};
  if (prefs.showDiskUsage === false) {
    host.innerHTML = "";
    return;
  }
  const disk = store.state?.disk || {};
  const total = Number(disk.total || 0);
  const system = Number(disk.system || 0);
  const apps = Number(disk.apps || 0);
  const free = Number(disk.free || 0);
  if (!total) {
    host.innerHTML = "";
    return;
  }
  const slices = [
    { label: "Système", value: system, color: "var(--bad)" },
    { label: "Applications / utilisateurs", value: apps, color: "var(--primary)" },
    { label: "Libre", value: free, color: "var(--ok)" },
  ];
  const pct = (value) => {
    if (!total) return "0 %";
    const p = (Math.max(0, Number(value || 0)) / total) * 100;
    return `${p >= 10 ? p.toFixed(0) : p.toFixed(1)} %`;
  };
  const legend = slices
    .map(
      (slice) => `<div class="disk-legend-row">
        <span class="disk-dot" style="background:${slice.color}"></span>
        <span class="disk-label">${escapeHtml(slice.label)}</span>
        <span class="disk-value">${escapeHtml(fmtBytes(slice.value))} (${escapeHtml(pct(slice.value))})</span>
      </div>`
    )
    .join("");
  const usedPct = pct(Number(disk.used || (system + apps)));
  host.innerHTML = `<div class="card card-pad">
    <div class="row-between" style="margin-bottom:8px">
      <span class="card-title">${icon("hard-drive", 16)} Disque principal</span>
      <span class="tiny muted">${escapeHtml(usedPct)} utilisé · ${escapeHtml(fmtBytes(total))} au total</span>
    </div>
    <div class="disk-pie-wrap">
      ${pieChart(slices, total)}
      <div class="disk-legend">${legend}</div>
    </div>
  </div>`;
}

function pieChart(slices, total) {
  const values = slices.map((slice) => Math.max(0, Number(slice.value || 0)));
  const sum = values.reduce((a, b) => a + b, 0);
  if (!sum) {
    return `<div class="empty" style="padding:10px 8px"><p>Aucune donnée.</p></div>`;
  }
  let acc = 0;
  const stops = slices
    .map((slice, index) => {
      const start = acc;
      acc += (values[index] / sum) * 360;
      return `${slice.color} ${start.toFixed(1)}deg ${acc.toFixed(1)}deg`;
    })
    .join(", ");
  return `<div class="disk-pie" style="background: conic-gradient(${stops})">
    <div class="disk-pie-hole"></div>
  </div>`;
}

function fmtBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let i = 0;
  let value = bytes;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
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
