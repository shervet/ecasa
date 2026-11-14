// Planning hebdomadaire : agenda tactile, éditeur de créneaux, imports Pronote/Wiser.

import { get, post, put } from "../api.js";
import { icon, modeIcon } from "../icons.js";
import { openSheet, closeSheet } from "../sheet.js";
import { devicesInRoom, refreshConfig, refreshState, setPref, store } from "../state.js";
import { toast, toastError } from "../toast.js";
import {
  clamp,
  describeSlot,
  deviceTempRange,
  escapeHtml,
  fmtDate,
  fmtTemp,
  minutesToTime,
  modeColor,
  modeLabel,
  modesList,
  deviceForMode,
  targetModes,
  mondayOf,
  parseDate,
  sortByPosition,
  timeToMinutes,
  todayIndex,
  weekDates,
  weekdayLong,
  weekdayShort,
} from "../utils.js";

const HOUR_PX = 56;
const PRESETS = {
  Matin: [
    { time: "06:30", mode: "heat", temp: 20 },
    { time: "09:00", mode: "off", temp: 17 },
  ],
  Journée: [
    { time: "09:00", mode: "heat", temp: 20 },
    { time: "17:00", mode: "off", temp: 17 },
  ],
  Soir: [
    { time: "18:00", mode: "heat", temp: 20 },
    { time: "23:00", mode: "off", temp: 17 },
  ],
  Nuit: [
    { time: "23:00", mode: "off", temp: 17 },
    { time: "06:30", mode: "heat", temp: 19 },
  ],
  Absence: [
    { time: "08:00", mode: "off", temp: 16 },
    { time: "18:00", mode: "heat", temp: 20 },
  ],
  "Week-end": [
    { time: "09:00", mode: "heat", temp: 20 },
    { time: "23:30", mode: "off", temp: 17 },
  ],
};

// Modèles prédéfinis pour toute la semaine
const WEEK_PRESETS = {
  vacances: [
    [ // Lundi
      { time: "07:00", mode: "heat", temp: 19 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "16:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Mardi
      { time: "07:00", mode: "heat", temp: 19 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "16:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Mercredi
      { time: "07:00", mode: "heat", temp: 19 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "16:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Jeudi
      { time: "07:00", mode: "heat", temp: 19 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "16:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Vendredi
      { time: "07:00", mode: "heat", temp: 19 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "16:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Samedi
      { time: "08:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Dimanche
      { time: "08:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
  ],
  travail: [
    [ // Lundi
      { time: "06:30", mode: "heat", temp: 20 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "17:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Mardi
      { time: "06:30", mode: "heat", temp: 20 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "17:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Mercredi
      { time: "06:30", mode: "heat", temp: 20 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "17:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Jeudi
      { time: "06:30", mode: "heat", temp: 20 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "17:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Vendredi
      { time: "06:30", mode: "heat", temp: 20 },
      { time: "09:00", mode: "off", temp: 17 },
      { time: "17:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Samedi
      { time: "08:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Dimanche
      { time: "08:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
  ],
  weekend: [
    [ // Lundi - absence jour
      { time: "08:00", mode: "off", temp: 16 },
    ],
    [ // Mardi
      { time: "08:00", mode: "off", temp: 16 },
    ],
    [ // Mercredi
      { time: "08:00", mode: "off", temp: 16 },
    ],
    [ // Jeudi
      { time: "08:00", mode: "off", temp: 16 },
    ],
    [ // Vendredi
      { time: "08:00", mode: "off", temp: 16 },
    ],
    [ // Samedi
      { time: "08:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
    [ // Dimanche
      { time: "08:00", mode: "heat", temp: 20 },
      { time: "23:00", mode: "off", temp: 17 },
    ],
  ],
  absence: Array.from({ length: 7 }, () => [
    { time: "08:00", mode: "off", temp: 16 },
    { time: "18:00", mode: "heat", temp: 19 },
  ]),
};

let root = null;
let targets = [];
let target = null;
let models = {};
let week = "A";
let day = todayIndex();
let dirty = false;
let appliedModelByTarget = {};
let selectedModelByTarget = {};
let viewMode = "day"; // "day" | "week"
let modelEdit = null; // { index, name, room, model, original }
let pendingRoomId = null; // roomId (targetId) demandé via deep-link (device sheet → Modifier)
let pendingScrollId = null; // ancre de la pièce à faire défiler après le rendu

function pickTarget() {
  const wanted = takePendingPlanningRoom();
  if (wanted != null) {
    const found = targets.find(
      (t) =>
        (t.kind === "room" && Number(t.targetId) === Number(wanted)) ||
        (t.kind === "device" && Number(t.targetId) === Number(wanted))
    );
    if (found) {
      // Arrivée depuis l'accueil : on affiche la page Planning mais on
      // positionne directement sur la pièce demandée.
      pendingScrollId = found.id;
      return found;
    }
  }
  return targets[0] || null;
}

/** Amène la pièce demandée à l'écran (deep-link depuis l'accueil). */
function scrollToPendingTarget() {
  const id = pendingScrollId;
  if (!id) return;
  pendingScrollId = null;
  const anchor = root?.querySelector(`[data-room-anchor="${id}"]`);
  if (!anchor) return;
  requestAnimationFrame(() => {
    anchor.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

export function setPendingPlanningRoom(roomId) {
  pendingRoomId = roomId;
}

export function takePendingPlanningRoom() {
  const value = pendingRoomId;
  pendingRoomId = null;
  return value;
}

export default {
  title: "Planning",

  async mount(view) {
    root = view;
    dirty = false;
    day = Number.isInteger(store.prefs.day) ? store.prefs.day : todayIndex();

    view.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div>
            <h1 class="page-title">Planning</h1>
            <p class="page-sub" id="planSub">Semaines type par pièce, créneaux et imports.</p>
          </div>
          <div class="page-actions">
            <button class="btn" type="button" id="planImport">${icon("upload", 17)} Importer</button>
            <button class="btn btn-primary" type="button" id="planPronote">${icon("school", 17)} Pronote</button>
            <button class="btn btn-primary" type="button" id="planSave" disabled>${icon("save", 17)} Enregistrer</button>
          </div>
        </div>

        <div class="planning-nav" id="planNav"></div>

        <div class="planning-toolbar">
          <div class="row wrap" style="gap:10px;align-items:center">
            <span class="pill pill-ghost" id="planDirty">À jour</span>
            <button class="btn btn-sm" type="button" id="planCopy">${icon("copy", 15)} Copier vers l'autre semaine</button>
          </div>
        </div>

        <div class="stack" id="planRooms"></div>

        <div id="modelsAnchor" style="scroll-margin-top: calc(var(--topbar-h) + 52px)"></div>

        <div class="card card-pad">
          <div class="row-between wrap" style="gap:10px">
            <div>
              <div class="card-title">${icon("sparkles", 18)} Modèles de semaine</div>
              <p class="hint mt-1">Remplace toute la semaine d'une pièce par un modèle prédéfini.</p>
            </div>
            <div class="row wrap" style="gap:8px;align-items:center">
              <select class="select" id="planPresetRoom" style="width:auto">
                ${targets.map((t) => `<option value="${t.id}">${escapeHtml(t.label)}</option>`).join("")}
              </select>
              <select class="select" id="planWeekPreset" style="width:auto">
                <option value="vacances">Vacances</option>
                <option value="travail">Semaine travail</option>
                <option value="weekend">Week-end prolongé</option>
                <option value="absence">Absence</option>
              </select>
              <button class="btn btn-sm btn-primary" type="button" id="planApplyWeekPreset">${icon("check", 14)} Appliquer (efface la semaine)</button>
            </div>
          </div>
        </div>

        <div class="card card-pad">
          <div class="row-between wrap" style="gap:10px">
            <div>
              <div class="card-title">${icon("sliders", 18)} Modèles</div>
              <p class="hint mt-1">Crée, édite et applique un planning à plusieurs pièces depuis la page Modèles.</p>
            </div>
            <a class="btn" href="#/models">${icon("chevron-right", 15)} Ouvrir les modèles</a>
          </div>
        </div>
      </div>
    `;

    buildTargets();
    if (!targets.length) {
      view.querySelector("#planRooms").innerHTML = `<div class="empty">
        <span class="empty-icon">${icon("calendar", 24)}</span>
        <strong>Aucune zone à programmer</strong>
        <p>Ajoute d'abord un appareil dans Réglages → Pièces &amp; appareils.</p>
      </div>`;
      view.querySelector("#planSave").disabled = true;
      return;
    }

    // Par défaut : semaine courante.
    week = store.state?.week || "A";
    setPref("week", week);

    paintPlanNav();

    if (!targets.length) return;
    target = pickTarget();
    // Lien profond : #/planning/room:3 (ou device:12) ouvre la pièce demandée.
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    if (parts[0] === "planning" && parts[1]) {
      const wanted = decodeURIComponent(parts[1]);
      const found = targets.find(
        (t) => t.id === wanted || `${t.kind}:${t.targetId}` === wanted
      );
      if (found) {
        target = found;
        pendingScrollId = found.id;
      }
    }
    rebuildModels();
    paintWeekSwitch();
    paintRooms();
    scrollToPendingTarget();
    paintDirty();
    paintSub();

    const presetRoomSelect = view.querySelector("#planPresetRoom");
    const roomOptions = targets.map((t) => `<option value="${t.id}">${escapeHtml(t.label)}</option>`).join("");
    if (presetRoomSelect) presetRoomSelect.innerHTML = roomOptions;


    view.querySelector("#planSave").addEventListener("click", save);
    view.querySelector("#planCopy").addEventListener("click", copyToOtherWeek);
    view.querySelector("#planImport").addEventListener("click", openImportSheet);
    view.querySelector("#planPronote")?.addEventListener("click", () => {
      try {
        openPronoteSheet();
      } catch (err) {
        console.error(err);
        toastError(err);
      }
    });

    // Appliquer un modèle prédéfini pour toute la semaine
    view.querySelector("#planApplyWeekPreset")?.addEventListener("click", () => {
      const roomId = view.querySelector("#planPresetRoom")?.value;
      const presetKey = view.querySelector("#planWeekPreset")?.value;
      const t = targets.find((x) => x.id === roomId);
      if (!t) { toast("Choisis d'abord une pièce.", { type: "warn" }); return; }
      target = t;
      rebuildModels();
      applyWeekPreset(presetKey);
    });

    window.addEventListener("beforeunload", warnUnload);
  },

  unmount() {
    closeSheet();
    window.removeEventListener("beforeunload", warnUnload);
    root = null;
  },

  onState() {
    if (!root) return;
    paintLive();
  },
};

/* --------------------------------------------------------------- cibles */

function buildTargets() {
  const state = store.state;
  targets = [];
  if (!state) return;

  const rooms = sortByPosition(state.rooms || []);
  for (const room of rooms) {
    const devices = devicesInRoom(state, room.id);
    if (!devices.length) continue;
    const actuator = devices.find((d) => Number(d.device_id) === Number(room.actuator_device_id));
    const device = actuator || devices[0];
    const linkedRooms = Array.isArray(store.config?.pronote_room_ids)
      ? store.config.pronote_room_ids.map(Number)
      : (Number(store.config?.pronote_room_id || 0) ? [Number(store.config.pronote_room_id)] : []);
    targets.push({
      id: `room:${room.id}`,
      kind: "room",
      targetId: room.id,
      room,
      device,
      devices,
      label: room.name,
      sub: `${devices.length} appareil${devices.length > 1 ? "s" : ""}`,
      pronoteLinked: linkedRooms.includes(Number(room.id)),
    });
  }

  // Appareils sans pièce (cas orphelins).
  const knownRooms = new Set(rooms.map((room) => Number(room.id)));
  for (const device of state.devices || []) {
    if (knownRooms.has(Number(device.room_id))) continue;
    targets.push({
      id: `device:${device.device_id}`,
      kind: "device",
      targetId: device.device_id,
      room: null,
      device,
      devices: [device],
      label: device.name,
      sub: "Sans pièce",
      pronoteLinked: false,
    });
  }
}

function cloneDays(days) {
  const source = Array.isArray(days) ? days : [];
  return Array.from({ length: 7 }, (_, index) => {
    const list = Array.isArray(source[index]) ? source[index] : [];
    return list
      .map((slot) => ({
        time: slot.time || "00:00",
        mode: slot.mode || "off",
        temp: Number(slot.temp ?? 20),
      }))
      .sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
  });
}

function rebuildModels() {
  models = {};
  for (const t of targets) {
    const programs = t.device?.programs || {};
    models[t.id] = {
      A: cloneDays(programs.A),
      B: cloneDays(programs.B),
    };
  }
}

/* --------------------------------------------------------------- rendu */

function paintDeviceSelect() {
  const select = root?.querySelector("#planDevice");
  const field = root?.querySelector("#planDeviceField");
  if (!select) return;
  const devices = target?.devices || [];
  if (devices.length <= 1) {
    if (field) field.style.display = "none";
    return;
  }
  if (field) field.style.display = "";
  select.innerHTML = devices
    .map((device) =>
      `<option value="${device.device_id}"${Number(device.device_id) === Number(target?.device?.device_id) ? " selected" : ""}>${escapeHtml(device.name)}</option>`
    )
    .join("");
}

function paintWeekSwitch() {
  root?.querySelectorAll("[data-week]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.week === week);
  });
}

/**
 * Semaines gérées par Pronote (liste renvoyée par le backend via
 * `config.pronote_weeks`). Par défaut les deux semaines restent gérées.
 */
function pronoteLinkedWeeks() {
  const raw = store.config?.pronote_weeks;
  if (Array.isArray(raw) && raw.length) {
    const valid = raw.map((value) => String(value).toUpperCase()).filter((value) => value === "A" || value === "B");
    if (valid.length) return valid;
  }
  // Pas de réglage explicite : on déduit les semaines ayant au moins un jour
  // de cours sur une des pièces liées.
  const schoolDaysMap = store.config?.pronote_school_days || {};
  const found = new Set();
  for (const perWeek of Object.values(schoolDaysMap)) {
    for (const weekKey of ["A", "B"]) {
      if (Array.isArray(perWeek?.[weekKey]) && perWeek[weekKey].some(Boolean)) found.add(weekKey);
    }
  }
  return found.size ? [...found] : ["A", "B"];
}

/** Semaine affichée par défaut : préférence utilisateur, sinon semaine unique Pronote, sinon semaine courante. */
function defaultPlanningWeek() {
  if (store.prefs.week === "A" || store.prefs.week === "B") return store.prefs.week;
  const weeks = store.config?.pronote_weeks;
  if (Array.isArray(weeks) && weeks.length === 1) return weeks[0];
  return store.state?.week || "A";
}

/** Jours verrouillés (cours Pronote) d'une semaine d'une pièce : aucun si la semaine n'est pas liée. */
function lockedDaysFor(t, weekKey) {
  const locked = [false, false, false, false, false, false, false];
  if (!t?.pronoteLinked || !pronoteLinkedWeeks().includes(weekKey)) return locked;
  const schoolDays = store.config?.pronote_school_days?.[String(t.targetId)]?.[weekKey] || [];
  return locked.map((_, index) => !!schoolDays[index]);
}

/** Conserve les créneaux des jours verrouillés de `previous` dans `next`. */
function keepLockedDays(t, weekKey, previous, next) {
  const locked = lockedDaysFor(t, weekKey);
  return next.map((slots, index) => (locked[index] ? cloneDays([previous?.[index] ?? []])[0] : slots));
}

function paintPlanNav() {
  const nav = root?.querySelector("#planNav");
  if (!nav) return;
  const weekSwitch = `
    <div class="week-switch" id="planWeekSwitch" style="flex:none">
      <button type="button" data-week="A">Semaine A</button>
      <button type="button" data-week="B">Semaine B</button>
    </div>`;
  let html = weekSwitch;
  html += targets
    .map((t) => `<button type="button" class="plan-nav-btn" data-jump-room="${t.id}">${escapeHtml(t.label)}</button>`)
    .join("");
  html += `<button type="button" class="plan-nav-btn" data-jump-models>Modèles</button>`;
  nav.innerHTML = html;

  nav.querySelectorAll("[data-week]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.week;
      if (!next || next === week) return;
      if (dirty && !confirm("Des modifications ne sont pas enregistrées. Changer de semaine ?")) return;
      week = next;
      setPref("week", week);
      rebuildModels();
      dirty = false;
      paintWeekSwitch();
      paintRooms();
      paintDirty();
      paintSub();
    });
  });

  nav.querySelectorAll("[data-jump-room]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const el = root?.querySelector(`[data-room-anchor="${btn.dataset.jumpRoom}"]`);
      if (!el) return;
      const navEl = root?.querySelector("#planNav");
      const offset = (document.querySelector(".topbar")?.offsetHeight || 64) + (navEl?.offsetHeight || 0) + 8;
      const top = el.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({ top, behavior: "smooth" });
    });
  });
  nav.querySelectorAll("[data-jump-models]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const el = root?.querySelector("#modelsAnchor");
      if (!el) return;
      const navEl = root?.querySelector("#planNav");
      const offset = (document.querySelector(".topbar")?.offsetHeight || 64) + (navEl?.offsetHeight || 0) + 8;
      const top = el.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({ top, behavior: "smooth" });
    });
  });

  paintWeekSwitch();
}

function paintSub() {
  const node = root?.querySelector("#planSub");
  if (!node) return;
  node.textContent = `Semaine ${week} · ${targets.length} pièce${targets.length > 1 ? "s" : ""}`;
}

function paintRooms() {
  const host = root?.querySelector("#planRooms");
  if (!host) return;
  const linkedRooms = Array.isArray(store.config?.pronote_room_ids)
    ? store.config.pronote_room_ids.map(Number)
    : (Number(store.config?.pronote_room_id || 0) ? [Number(store.config.pronote_room_id)] : []);
  if (!targets.length) {
    host.innerHTML = '<div class="empty"><span class="empty-icon">' + icon("calendar", 24) + '</span><strong>Aucune pièce équipée</strong><p>Ajoute un appareil dans Réglages.</p></div>';
    return;
  }

  const dates = weekDates(mondayOf(new Date()));
  const nowIndex = todayIndex();
  const nowWeek = store.state?.week;

  let html = "";
  for (const t of targets) {
    const programs = t.device?.programs || {};
    const model = models[t.id];
    const days = cloneDays((model || programs)[week] ?? (model || programs).A);
    const appliedIdx = appliedModelByTarget[t.id];
    const selVal = selectedModelByTarget[t.id] ?? "";
    const hasDevices = (t.devices || []).length > 1;
    let deviceSelect = "";
    if (hasDevices) {
      deviceSelect = '<select class="select plan-device-select" data-target="' + t.id + '" style="width:auto">';
      for (const d of (t.devices || [])) {
        const sel = Number(d.device_id) === Number(t.device?.device_id) ? " selected" : "";
        deviceSelect += '<option value="' + d.device_id + '"' + sel + '>' + escapeHtml(d.name) + '</option>';
      }
      deviceSelect += "</select>";
    } else {
      deviceSelect = '<span class="tiny muted">' + escapeHtml(t.device?.name || "") + ' · Semaine ' + escapeHtml(week) + '</span>';
    }

    // model select
    let savedModels = [];
    try { savedModels = JSON.parse(localStorage.getItem("ecasa.weekModels") || "[]"); } catch { savedModels = []; }
    let modelSelect = '<select class="select plan-model-select" data-target="' + t.id + '" style="width:auto;min-width:150px"><option value="">— modèle —</option>';
    for (let i = 0; i < savedModels.length; i++) {
      const sel = String(i) === String(selVal) ? " selected" : "";
      modelSelect += '<option value="' + i + '"' + sel + '>' + escapeHtml(savedModels[i].name) + '</option>';
    }
    modelSelect += "</select>";
        const isApplied = selVal !== "" && appliedIdx !== undefined && Number(appliedIdx) === Number(selVal);
    const applyBtnClass = "btn btn-sm plan-apply-btn" + (isApplied ? " is-applied" : "");

    const isLinked = !!t.pronoteLinked;
    // Le verrouillage Pronote est par SEMAINE : une semaine non liée reste
    // entièrement modifiable, même si la pièce est liée sur l'autre semaine.
    const linkedWeeks = pronoteLinkedWeeks();
    const isLinkedWeek = isLinked && linkedWeeks.includes(week);
    const schoolDaysMap = store.config?.pronote_school_days || {};
    const schoolDays = isLinkedWeek
      ? (schoolDaysMap[String(t.targetId)]?.[week] || [false, false, false, false, false, false, false])
      : [false, false, false, false, false, false, false];
    html += '<div class="room-plan' + (isLinkedWeek ? " room-plan--linked" : "") + '" data-room-anchor="' + t.id + '" style="scroll-margin-top: calc(var(--topbar-h) + 56px)">';
    html += '<div class="room-plan-head">';
    html += '<div class="room-plan-title">' + escapeHtml(t.label) + ' <span class="pill pill-ghost" style="margin-left:6px">Semaine ' + escapeHtml(week) + '</span>' + (isLinkedWeek ? ' <span class="pill pill-accent" style="margin-left:6px">Lié à Pronote</span>' : '') + '</div>';
    html += deviceSelect;
    html += '<button class="btn btn-sm btn-danger btn-icon" type="button" data-clear-room="' + t.id + '" title="Vider cette pièce" aria-label="Vider cette pièce">' + icon("trash", 14) + '</button>';
    html += '</div>';
    html += '<div class="room-plan-model-row">';
    html += modelSelect;
    html += '<button class="' + applyBtnClass + '" type="button" data-apply-model="' + t.id + '"' + (isApplied ? " disabled" : "") + ' title="Appliquer le modèle">' + icon("check", 14) + '</button>';
    html += '</div>';
    let columns = "";
    for (let index = 0; index < days.length; index++) {
      const date = dates[index];
      const d = parseDate(date) || new Date();
      const dom = String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0");
      const isToday = index === nowIndex && week === nowWeek;
      const dayLocked = isLinkedWeek && !!schoolDays[index];
      const slots = days[index] || [];
      const items = slots.slice().sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
      let rows = "";
      if (items.length) {
        for (const slot of items) {
          const end = slotEnd(slots, slot);
          const slotIndex = slots.indexOf(slot);
          rows += '<button type="button" class="week-slot" data-target="' + t.id + '" data-day="' + index + '" data-slotindex="' + slotIndex + '"' + (dayLocked ? ' disabled' : '') + ' style="--slot-color:' + modeColor(slot.mode) + '">';
          rows += '<span class="week-slot-time">' + escapeHtml(slot.time) + (end ? "\u2013" + escapeHtml(end) : "") + '</span>';
          rows += '<span class="week-slot-temp">' + fmtTemp(slot.temp) + ' °C</span>';
          rows += '<span class="week-slot-mode">' + escapeHtml(modeLabel(slot.mode, store.meta)) + '</span>';
          rows += '</button>';
        }
      } else {
        rows = '<div class="week-slot week-slot-empty">—</div>';
      }
      columns += '<div class="week-col' + (isToday ? " is-today" : "") + (dayLocked ? " is-locked" : "") + '">';
      columns += '<div class="week-col-head">';
      columns += '<span class="week-col-dow">' + weekdayShort(index) + '</span>';
      columns += '<span class="week-col-dom">' + dom + '</span>';
      if (dayLocked) columns += '<span class="tiny muted">Pronote</span>';
      columns += '</div>';
      columns += '<div class="week-col-body">';
      columns += rows;
      columns += '<button type="button" class="week-add" data-target="' + t.id + '" data-day="' + index + '" title="Ajouter un créneau"' + (dayLocked ? ' disabled' : '') + '>+</button>';
      columns += '</div></div>';
    }

    html += '<div class="week-grid">' + columns + '</div>';
    html += '</div>';
  }

  host.innerHTML = html;

  // Wire slot clicks
  host.querySelectorAll(".week-slot[data-target]").forEach((el) => {
    if (el.disabled) return;
    el.addEventListener("click", () => {
      const tid = el.dataset.target;
      const tt = targets.find((x) => x.id === tid);
      if (!tt) return;
      target = tt;
      const wd = Number(el.dataset.day);
      const si = Number(el.dataset.slotindex);
      openSlotEditor(target, wd, si >= 0 ? si : null);
    });
  });

  // Wire add buttons
  host.querySelectorAll(".week-add").forEach((el) => {
    if (el.disabled) return;
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const tid = el.dataset.target;
      const tt = targets.find((x) => x.id === tid);
      if (!tt) return;
      target = tt;
      const wd = Number(el.dataset.day);
      openSlotEditor(target, wd, null, 9 * 60);
    });
  });


  // Wire device selects
  host.querySelectorAll(".plan-device-select").forEach((sel) => {
    sel.addEventListener("change", (event) => {
      const tid = sel.dataset.target;
      const t = targets.find((x) => x.id === tid);
      if (!t) return;
      const nextId = Number(event.target.value);
      const device = (t.devices || []).find((d) => Number(d.device_id) === nextId);
      if (!device) return;
      t.device = device;
      rebuildModels();
      paintRooms();
      paintDirty();
      paintSub();
    });
  });

  // Wire apply model buttons
  host.querySelectorAll("[data-apply-model]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tid = btn.dataset.applyModel;
      const t = targets.find((x) => x.id === tid);
      if (!t) return;
      const sel = host.querySelector(`.plan-model-select[data-target="${tid}"]`);
      const idx = Number(sel?.value);
      if (!Number.isInteger(idx) || idx < 0) { toast("Choisis un modèle.", { type: "warn" }); return; }
      let saved = [];
      try { saved = JSON.parse(localStorage.getItem("ecasa.weekModels") || "[]"); } catch { saved = []; }
      const m = saved[idx];
      if (!m) { toast("Modèle introuvable.", { type: "warn" }); return; }
      if (!confirm(`Appliquer le modèle « ${m.name} » à ${t.label} ?`)) return;
      t.device.programs = t.device.programs || {};
      const previous = { A: cloneDays(models[tid]?.A ?? t.device.programs.A), B: cloneDays(models[tid]?.B ?? t.device.programs.B) };
      // Les jours de cours Pronote restent intacts dans les semaines liées.
      t.device.programs.A = keepLockedDays(t, "A", previous.A, cloneDays(m.days?.A));
      t.device.programs.B = keepLockedDays(t, "B", previous.B, cloneDays(m.days?.B));
      models[tid] = { A: cloneDays(t.device.programs.A), B: cloneDays(t.device.programs.B) };
      selectedModelByTarget[tid] = idx;
      appliedModelByTarget[tid] = idx;
      dirty = true;
      paintRooms();
      paintDirty();
      toast(`Modèle « ${m.name} » appliqué.`);
    });
  });

  // Wire clear room buttons
  host.querySelectorAll("[data-clear-room]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = targets.find((x) => x.id === btn.dataset.clearRoom);
      if (!t) return;
      target = t;
      clearRoom(t);
    });
  });

  // Reset applied state when model selection changes
  host.querySelectorAll(".plan-model-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const tid = sel.dataset.target;
      selectedModelByTarget[tid] = sel.value;
      if (String(appliedModelByTarget[tid]) !== String(sel.value)) {
        delete appliedModelByTarget[tid];
      }
      const btn = host.querySelector(`[data-apply-model="${tid}"]`);
      if (btn) {
        btn.classList.remove("is-applied");
        btn.disabled = false;
      }
    });
  });
}

function clearRoom(t) {
  if (!t) return;
  const model = models[t.id];
  const locked = lockedDaysFor(t, week);
  const hasLocked = locked.some(Boolean);
  const message = hasLocked
    ? `Vider les créneaux non Pronote de la semaine ${week} pour ${t.label} ? Les jours de cours Pronote sont conservés.`
    : `Vider tous les créneaux de la semaine ${week} pour ${t.label} ?`;
  if (!confirm(message)) return;
  if (model) {
    model[week] = Array.from({ length: 7 }, (_, index) => (locked[index] ? model[week][index] : []));
  }
  dirty = true;
  paintRooms();
  paintDirty();
  toast(`${t.label} vidée pour la semaine ${week}. Pense à enregistrer.`, { type: "info" });
}

function slotEnd(slots, slot) {
  const sorted = (slots || []).slice().sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
  const idx = sorted.indexOf(slot);
  const next = sorted[idx + 1];
  return next ? next.time : null;
}

function paintDirty() {
  const node = root?.querySelector("#planDirty");
  const button = root?.querySelector("#planSave");
  if (node) {
    node.textContent = dirty ? "Non enregistré" : "À jour";
    node.className = `pill ${dirty ? "pill-warn" : "pill-ghost"}`;
  }
  if (button) button.disabled = !dirty;
}

function paintLive() {
  if (!root || dirty) return;
  const node = root.querySelector("#planDirty");
  if (node) {
    node.textContent = "À jour";
    node.className = "pill pill-ghost";
  }
}

/* --------------------------------------------------------------- éditeur */

function targetTempRange(target) {
  const devices = Array.isArray(target?.devices) && target.devices.length
    ? target.devices
    : (target?.device ? [target.device] : []);
  const hasAtlantic = devices.some((d) => (d?.device_type || "atlantic") === "atlantic");
  if (hasAtlantic) {
    let min = 16;
    let max = 30;
    for (const d of devices.filter((x) => (x?.device_type || "atlantic") === "atlantic")) {
      const r = deviceTempRange(d);
      if (Number.isFinite(r.min)) min = Math.max(min, r.min);
      if (Number.isFinite(r.max)) max = Math.min(max, r.max);
    }
    return min <= max ? { min, max } : { min: 16, max: 30 };
  }
  let min = 5;
  let max = 30;
  for (const d of devices) {
    const r = deviceTempRange(d);
    if (Number.isFinite(r.min)) min = Math.min(min, r.min);
    if (Number.isFinite(r.max)) max = Math.max(max, r.max);
  }
  return min <= max ? { min, max } : { min: 5, max: 30 };
}

function openSlotEditor(target, dayIndex, slotIndex = null, minutes = 0) {
  if (!target) return;
  const model = models[target.id];
  if (!model) return;
  const slots = model[week][dayIndex];
  const existing = slotIndex !== null ? slots[slotIndex] : null;
  let time = existing?.time || minutesToTime(minutes);
  let mode = existing?.mode || "heat";
  const { min: tempMin, max: tempMax } = targetTempRange(target);
  let temp = clamp(Number(existing?.temp ?? 20), tempMin, tempMax);
  // Modes réellement applicables à la pièce : union des appareils.
  const availableModes = targetModes(target);
  const hints = [];
  if (target.device?.device_type === "wiser") {
    hints.push("Sur Wiser, « Arrêt » est appliqué par une consigne hors gel à 6 °C.");
  }
  const cooler = deviceForMode(target, "cool");
  if (cooler && Number(cooler.device_id) !== Number(target.device?.device_id)) {
    hints.push(
      `Cette pièce est aussi équipée de « ${cooler.name} » : le mode Froid lui sera appliqué.`
    );
  }
  const modeHint = hints.length
    ? `<p class="hint">${hints.map((text) => escapeHtml(text)).join(" ")}</p>`
    : "";

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <p class="hint" style="margin:0 0 8px">Semaine ${escapeHtml(week)}</p>
    <div class="form-grid">
      <label class="field">
        <span class="label">Heure</span>
        <input class="input" type="time" step="900" value="${escapeHtml(time)}" data-time>
      </label>
      <label class="field">
        <span class="label">Consigne</span>
        <div class="input-row">
          <button class="btn btn-sm" type="button" data-temp="-1">−</button>
          <input class="input" type="number" min="${tempMin}" max="${tempMax}" step="0.5" value="${temp}" data-temp-input style="text-align:center">
          <button class="btn btn-sm" type="button" data-temp="1">+</button>
        </div>
      </label>
    </div>

    <div class="field">
      <span class="label">Mode</span>
      <div class="mode-grid" data-mode-grid>
        ${modesList(store.meta)
          .filter(([key]) => availableModes.includes(key))
          .map(
            ([key, label]) => `<button class="mode-btn${key === mode ? " is-active" : ""}" type="button" data-mode="${key}">
              ${modeIcon(key, 20)}<span>${escapeHtml(label)}</span>
            </button>`
          )
          .join("")}
      </div>
      ${modeHint}
    </div>

    <label class="field">
      <span class="label">Dupliquer aussi vers</span>
      <select class="select" data-duplicate>
        <option value="">Ne pas dupliquer</option>
        ${Array.from({ length: 7 }, (_, i) => (i === dayIndex ? "" : `<option value="${i}">${weekdayLong(i)}</option>`)).join("")}
        <option value="all">Tous les autres jours</option>
      </select>
    </label>
  `;

  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%";
  footer.innerHTML = `
    ${existing ? `<button class="btn btn-danger" type="button" data-delete>${icon("trash", 17)} Supprimer</button>` : ""}
    <button class="btn btn-primary" type="button" data-validate>${icon("check", 17)} ${existing ? "Mettre à jour" : "Ajouter"}</button>
  `;

  const sheet = openSheet({
    title: existing ? "Modifier le créneau" : "Nouveau créneau",
    sub: `${weekdayLong(dayIndex)} · semaine ${week} · ${target.label}`,
    body,
    footer,
  });

  const timeInput = body.querySelector("[data-time]");
  const tempInput = body.querySelector("[data-temp-input]");

  const paint = () => {
    body.querySelectorAll("[data-mode]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.mode === mode);
    });
    tempInput.value = String(temp);
  };

  timeInput.addEventListener("input", () => {
    time = timeInput.value || time;
  });

  body.querySelectorAll("[data-temp]").forEach((button) => {
    button.addEventListener("click", () => {
      temp = clamp(temp + Number(button.dataset.temp) * 0.5, tempMin, tempMax);
      paint();
    });
  });

  tempInput.addEventListener("input", () => {
    temp = clamp(tempInput.value, tempMin, tempMax);
  });

  body.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      mode = button.dataset.mode;
      paint();
    });
  });

  paint();

  footer.querySelector("[data-validate]")?.addEventListener("click", () => {
    const m = models[target.id];
    const slots = m?.[week]?.[dayIndex];
    if (!slots) return;
    const slot = {
      time: timeInput.value || "00:00",
      mode,
      temp: Number(clamp(temp, tempMin, tempMax)),
    };

    if (existing) {
      const idx = slots.indexOf(existing);
      if (idx >= 0) slots[idx] = slot;
      else slots.push(slot);
    } else {
      slots.push(slot);
    }

    const duplicate = body.querySelector("[data-duplicate]")?.value;
    if (duplicate) {
      const indexes = duplicate === "all" ? [0, 1, 2, 3, 4, 5, 6] : [Number(duplicate)];
      for (const targetDay of indexes) {
        if (targetDay === dayIndex) continue;
        const otherSlots = m?.[week]?.[targetDay];
        if (otherSlots) upsertSlot(otherSlots, slot);
      }
    }

    slots.sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
    dirty = true;
    closeSheet();
    void save();
    paintRooms();
    paintDirty();
    toast(existing ? "Créneau modifié." : "Créneau ajouté.", { type: "info", timeout: 2200 });
  });

  footer.querySelector("[data-delete]")?.addEventListener("click", () => {
    const m = models[target.id];
    const slots = m?.[week]?.[dayIndex];
    if (slots) {
      const idx = existing ? slots.indexOf(existing) : slotIndex;
      if (idx >= 0 && idx < slots.length) slots.splice(idx, 1);
    }
    dirty = true;
    closeSheet();
    paintRooms();
    paintDirty();
    toast("Créneau supprimé.", { type: "info", timeout: 2200 });
  });

  return sheet;
}

function upsertSlot(list, slot) {
  const index = list.findIndex((item) => item.time === slot.time);
  if (index >= 0) list[index] = { ...slot };
  else list.push({ ...slot });
  list.sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
}

function applyPreset(name) {
  const preset = PRESETS[name];
  if (!preset || !target) return;
  const model = models[target.id];
  if (!model) return;
  const slots = model[week][day];
  if (slots.length && !confirm(`Remplacer les ${slots.length} créneaux de ${weekdayLong(day)} par le modèle « ${name} » ?`)) {
    return;
  }
  model[week][day] = preset.map((slot) => ({ ...slot }));
  dirty = true;
  paintRooms();
  paintDirty();
  toast(`Modèle « ${name} » appliqué.`, { type: "info", timeout: 2400 });
}

function applyWeekPreset(key) {
  const preset = WEEK_PRESETS[key];
  if (!preset || !target) return;
  const model = models[target.id];
  if (!model) return;
  const hasLocked = lockedDaysFor(target, week).some(Boolean);
  const warning = hasLocked
    ? "Les jours de cours Pronote sont conservés."
    : "Tous les créneaux existants seront effacés.";
  if (!confirm(`Remplacer la semaine ${week} de ${target.label} par le modèle « ${key} » ? (${warning})`)) {
    return;
  }
  const next = preset.map((daySlots) =>
    Array.isArray(daySlots) ? daySlots.map((slot) => ({ ...slot })).sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time)) : []
  );
  model[week] = keepLockedDays(target, week, model[week], next);
  dirty = true;
  paintRooms();
  paintDirty();
  toast(`Modèle « ${key} » appliqué à toute la semaine ${week}.`, { type: "info", timeout: 2400 });
}

function dominantMode(slots) {
  const counts = new Map();
  for (const slot of slots) counts.set(slot.mode, (counts.get(slot.mode) || 0) + 1);
  let best = null;
  let bestCount = 0;
  for (const [mode, count] of counts) {
    if (count > bestCount) {
      best = mode;
      bestCount = count;
    }
  }
  return best;
}

/* --------------------------------------------------------------- actions */

async function save() {
  const button = root?.querySelector("#planSave");
  if (button) button.disabled = true;
  try {
    for (const t of targets) {
      const model = models[t.id];
      if (!model) continue;
      // Un modèle appliqué modifie les deux semaines : on enregistre A et B.
      for (const w of ["A", "B"]) {
        await put(`/api/programs/${t.targetId}/${w}`, { days: model[w] });
      }
    }
    dirty = false;
    toast(`Planning ${week} enregistré.`);
    await refreshState({ silent: true });
    reloadFromState();
  } catch (err) {
    toastError(err);
    paintDirty();
  } finally {
    if (button) button.disabled = false;
  }
}

async function clearWeek() {
  if (!confirm(`Supprimer tous les créneaux de la semaine ${week} pour toutes les pièces ?`)) return;
  for (const t of targets) {
    const model = models[t.id];
    if (!model) continue;
    model[week] = Array.from({ length: 7 }, () => []);
  }
  dirty = true;
  paintRooms();
  paintDirty();
  toast(`Semaine ${week} vidée. Pense à enregistrer.`, { type: "info" });
}

async function copyToOtherWeek() {
  if (!target) return;
  const model = models[target.id];
  if (!model) return;
  const other = week === "A" ? "B" : "A";
  if (!confirm(`Copier les créneaux de la semaine ${week} vers la semaine ${other} ?`)) return;

  try {
    await put(`/api/programs/${target.targetId}/${week}`, { days: model[week] });
    await put(`/api/programs/${target.targetId}/${other}`, { days: model[week] });
    dirty = false;
    toast(`Semaine ${week} copiée vers ${other}.`);
    await refreshState({ silent: true });
    reloadFromState();
  } catch (err) {
    toastError(err);
  }
}

/* --------------------------------------------------------------- imports */

function openImportSheet() {
  if (!target) return;
  const device = target.device;
  const canWiser = device?.device_type === "wiser";

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <button class="btn btn-block" type="button" data-import="pronote" style="justify-content:flex-start;height:auto;padding:14px">
      <span style="display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:var(--primary-soft);color:var(--primary)">${icon("school", 20)}</span>
      <span style="text-align:left">
        <span style="display:block;font-weight:700">Emploi du temps Pronote</span>
        <span class="hint">Chauffe la pièce selon les cours de la semaine.</span>
      </span>
    </button>
    <button class="btn btn-block" type="button" data-import="wiser" style="justify-content:flex-start;height:auto;padding:14px" ${canWiser ? "" : "disabled"}>
      <span style="display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:var(--accent-soft);color:var(--accent)">${icon("hub", 20)}</span>
      <span style="text-align:left">
        <span style="display:block;font-weight:700">Planning natif Wiser</span>
        <span class="hint">${canWiser ? "Récupère les créneaux du Hub pour cette zone." : "Disponible uniquement pour une zone Wiser."}</span>
      </span>
    </button>
    <p class="hint">L'import remplace la semaine ${week} de ${escapeHtml(target.label)}. Tu pourras encore ajuster les créneaux.</p>
  `;

  const sheet = openSheet({
    title: "Importer un planning",
    sub: `${target.label} · semaine ${week}`,
    body,
  });

  body.querySelector('[data-import="pronote"]')?.addEventListener("click", () => {
    closeSheet();
    openPronoteSheet();
  });

  body.querySelector('[data-import="wiser"]')?.addEventListener("click", () => {
    closeSheet();
    openWiserSheet();
  });

  return sheet;
}

function openPronoteSheet() {
  const linkedRoomIds = Array.isArray(store.config?.pronote_room_ids)
    ? store.config.pronote_room_ids.map(Number).filter(Boolean)
    : (Number(store.config?.pronote_room_id || 0) ? [Number(store.config.pronote_room_id)] : []);
  const rooms = targets.filter(
    (item) => item.kind === "room" && linkedRoomIds.includes(Number(item.targetId))
  );
  if (!rooms.length) {
    toast("Aucune pièce liée à Pronote. Configure-la dans Réglages > Pronote.", { type: "warn" });
    return;
  }
  let plan = null;
  let planStart = null;
  let planSwitch = null;
  const monday = mondayOf(new Date());
  const body = document.createElement("div");
  body.className = "stack";
  const parity = (store.state?.settings?.week_mode || "parity") === "parity";
  // Semaines et jour d'alternance : valeurs enregistrées, modifiables ici.
  const linkedWeeks = pronoteLinkedWeeks();
  const chosenWeeks = new Set(linkedWeeks);
  const initialSwitch = store.state?.settings?.week_switch_day || store.config?.pronote_switch_day || "monday";
  let switchDay = initialSwitch === "friday" ? "friday" : "monday";
  const autoEnabled = store.config?.pronote_auto_enabled !== false;
  const syncMinutes = Number(store.config?.pronote_sync_minutes ?? 180);
  const autoLabel = autoEnabled
    ? `chaque jour à ${String(Math.floor(syncMinutes / 60)).padStart(2, "0")}:00`
    : "désactivée (import manuel)";
  const roomsHtml = rooms.map((room) => `<li>${escapeHtml(room.label)}</li>`).join("");
  body.innerHTML = `
    <div class="field">
      <span class="label">Pièces mises à jour</span>
      <ul class="hint" style="margin:4px 0 0;padding-left:18px">${roomsHtml}</ul>
      <span class="hint">Les pièces sont définies dans Réglages &gt; Pronote.</span>
    </div>
    ${
      parity
        ? `<div class="field">
      <span class="label">Écrire dans les semaines</span>
      <div class="row wrap" style="gap:10px">
        <label class="check"><input type="checkbox" data-pronote-week="A"${chosenWeeks.has("A") ? " checked" : ""}> Semaine A</label>
        <label class="check"><input type="checkbox" data-pronote-week="B"${chosenWeeks.has("B") ? " checked" : ""}> Semaine B</label>
      </div>
      <span class="hint">Les semaines cochées restent gérées par Pronote (et resynchronisées automatiquement) ; les autres restent modifiables.</span>
    </div>`
        : `<p class="hint">Les jours avec cours sont remplacés (ou complétés selon le jour d'alternance) ; les jours sans cours restent modifiables.</p>`
    }
    <label class="field">
      <span class="label">Jour d'alternance (garde)</span>
      <select class="select" data-pronote-switch-day>
        <option value="monday"${switchDay === "monday" ? " selected" : ""}>Lundi — l'enfant arrive après l'école</option>
        <option value="friday"${switchDay === "friday" ? " selected" : ""}>Vendredi — l'enfant part après l'école</option>
      </select>
      <span class="hint">Lundi : l'enfant arrive chez le parent gardien (repas s'il est externe, après les cours s'il est demi-pensionnaire). Vendredi : il part selon les cours. Le reste de la journée reste modifiable.</span>
    </label>
    <div class="field">
      <span class="label">Mise à jour automatique</span>
      <div class="row-between wrap" style="gap:10px">
        <span class="tiny muted">${escapeHtml(autoLabel)}</span>
        <button class="btn" type="button" data-open-settings>${icon("settings", 16)} Réglages</button>
      </div>
      <span class="hint">L'heure et l'activation se règlent dans Réglages &gt; Pronote.</span>
    </div>
    <label class="field">
      <span class="label">Semaine à analyser</span>
      <input class="input" type="date" value="${monday}" data-week-start>
      <span class="hint">Le lundi de la semaine scolaire. L'aperçu ne modifie rien.</span>
    </label>
    <button class="btn" type="button" data-preview>${icon("search", 17)} Charger l'aperçu</button>
    <p class="hint">L'aperçu utilise les réglages globaux ; à l'import, chaque pièce applique ses propres réglages confort/réduit (Réglages → Pronote).</p>
    <div data-preview-zone></div>
  `;

  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%";
  footer.innerHTML = `<button class="btn btn-primary" type="button" data-do-import>${icon("download", 17)} Importer</button>`;

  const sheet = openSheet({
    title: "Import Pronote",
    sub: `${rooms.length} pièce${rooms.length > 1 ? "s" : ""} liée${rooms.length > 1 ? "s" : ""}`,
    body,
    footer,
  });

  const previewZone = body.querySelector("[data-preview-zone]");
  const importButton = footer.querySelector("[data-do-import]");

  body.querySelector("[data-open-settings]")?.addEventListener("click", () => {
    closeSheet();
    // Lien profond : la page Réglages ouvre et fait défiler jusqu'à Pronote.
    location.hash = "#/settings/pronote";
  });

  body.querySelectorAll("[data-pronote-week]").forEach((box) => {
    box.addEventListener("change", () => {
      const key = box.dataset.pronoteWeek === "B" ? "B" : "A";
      if (box.checked) chosenWeeks.add(key);
      else chosenWeeks.delete(key);
      if (!chosenWeeks.size) {
        chosenWeeks.add(key);
        box.checked = true;
      }
    });
  });

  const previewButton = body.querySelector("[data-preview]");
  body.querySelector("[data-pronote-switch-day]")?.addEventListener("change", (event) => {
    switchDay = event.currentTarget.value === "friday" ? "friday" : "monday";
    // L'aperçu dépend du jour de bascule : on le recharge s'il était affiché.
    const hadPlan = !!plan;
    plan = null;
    planStart = null;
    planSwitch = null;
    if (hadPlan) previewButton?.click();
  });

  previewButton?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    previewZone.innerHTML = `<div class="boot" style="min-height:120px"><span class="spinner"></span><p>Lecture de Pronote…</p></div>`;
    try {
      const start = body.querySelector("[data-week-start]")?.value || monday;
      const requestedSwitch = switchDay;
      plan = await post("/api/pronote/preview", { week: start, switch_day: requestedSwitch });
      planStart = start;
      planSwitch = requestedSwitch;
      previewZone.innerHTML = renderPronotePreview(plan);
    } catch (err) {
      previewZone.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
    } finally {
      button.disabled = false;
    }
  });

  importButton.addEventListener("click", async () => {
    if (!rooms.length) return;
    importButton.disabled = true;
    try {
      const start = body.querySelector("[data-week-start]")?.value || monday;
      if (!plan || planStart !== start || planSwitch !== switchDay) {
        importButton.innerHTML = `${icon("download", 17)} Lecture de Pronote…`;
        const requestedSwitch = switchDay;
        plan = await post("/api/pronote/preview", { week: start, switch_day: requestedSwitch });
        planStart = start;
        planSwitch = requestedSwitch;
      }
      const weeksToWrite = [...chosenWeeks];
      if (!weeksToWrite.length) weeksToWrite.push("A");
      const targetLabels = [];
      // Un seul enregistrement par clic : on n'écrit les réglages que s'ils
      // ont changé depuis la dernière fois (sinon aucun PUT inutile).
      const storedWeeks = (store.config?.pronote_weeks || []).slice().sort();
      const storedSwitch = store.state?.settings?.week_switch_day
        || store.config?.pronote_switch_day
        || "monday";
      const weeksChanged = weeksToWrite.slice().sort().join(",") !== storedWeeks.join(",");
      if (weeksChanged || switchDay !== storedSwitch) {
        await put("/api/config", {
          pronote_weeks: weeksToWrite,
          pronote_switch_day: switchDay,
        });
      }
      // Un seul import par pièce : il écrit toutes les semaines choisies et
      // lie la pièce à chacune d'elles (évite d'écraser la liaison de la 1re).
      for (const room of rooms) {
        await post("/api/pronote/import", {
          room_id: Number(room.targetId),
          weeks: weeksToWrite,
          linked_weeks: weeksToWrite,
          week_start: start,
          days: plan.days,
          scopes: plan.scopes,
          preview_week_type: plan.week_type,
          switch_day: switchDay,
        });
        targetLabels.push(room.label);
      }
      toast(`Planning Pronote importé dans ${targetLabels.join(", ")} — semaine${weeksToWrite.length > 1 ? "s" : ""} ${weeksToWrite.join(", ")}.`);
      closeSheet();
      // Affiche la semaine importée : sinon l'utilisateur reste sur la
      // semaine courante et ne voit pas le résultat de son import.
      week = weeksToWrite[0];
      setPref("week", week);
      await refreshConfig({ silent: true });
      await refreshState({ silent: true });
      dirty = false;
      reloadFromState();
    } catch (err) {
      toastError(err);
      importButton.disabled = false;
      importButton.innerHTML = `${icon("download", 17)} Importer`;
    }
  });

  return sheet;
}



function renderPronotePreview(plan) {
  const days = plan?.days || [];
  const dates = weekDates(plan?.week_start || mondayOf(new Date()));
  return `
    <div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
      <div class="row-between">
        <div>
          <div class="tiny muted">Semaine ${escapeHtml(plan?.week_type || "—")} · ISO ${plan?.iso_week ?? "—"}</div>
          <div style="font-weight:700;margin-top:2px">${(plan?.lessons || []).length} cours détectés</div>
        </div>
        <span class="pill pill-ok">Aperçu</span>
      </div>
    </div>
    <div class="stack">
      ${days
        .map(
          (daySlots, index) => `<div class="row-between" style="padding:8px 0;border-bottom:1px solid var(--line)">
            <span style="font-size:12.5px;font-weight:650">${weekdayLong(index)} <span class="muted">${fmtDate(dates[index])}</span></span>
            <span class="tiny muted">${(daySlots || []).map((slot) => escapeHtml(describeSlot(slot, store.meta))).join(" · ") || "aucun cours"}</span>
          </div>`
        )
        .join("")}
    </div>
  `;
}

function openWiserSheet() {
  const device = target?.device;
  if (!device) return;

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `<div class="boot" style="min-height:140px"><span class="spinner"></span><p>Lecture du Hub Wiser…</p></div>`;

  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%";
  footer.innerHTML = `<button class="btn btn-primary" type="button" data-do-import disabled>${icon("download", 17)} Importer dans la semaine ${week}</button>`;

  const sheet = openSheet({
    title: "Planning Wiser",
    sub: `${target.label} · semaine ${week}`,
    body,
    footer,
  });

  const importButton = footer.querySelector("[data-do-import]");

  (async () => {
    try {
      const preview = await get(`/api/wiser/schedule/${device.device_id}`);
      const days = preview.days || [];
      body.innerHTML = `
        <div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
          <div class="row-between">
            <div>
              <div class="tiny muted">Planning natif</div>
              <div style="font-weight:700;margin-top:2px">${escapeHtml(preview.schedule_name || "Sans nom")}</div>
            </div>
            <span class="pill pill-accent">${escapeHtml(preview.room_name || target.label)}</span>
          </div>
        </div>
        <div class="stack">
          ${days
            .map(
              (daySlots, index) => `<div class="row-between" style="padding:8px 0;border-bottom:1px solid var(--line)">
                <span style="font-size:12.5px;font-weight:650">${weekdayLong(index)}</span>
                <span class="tiny muted">${(daySlots || []).map((slot) => escapeHtml(describeSlot(slot, store.meta))).join(" · ") || "aucun créneau"}</span>
              </div>`
            )
            .join("")}
        </div>
        <p class="hint">L'import remplace les créneaux eCasa de la semaine ${week} pour cette zone.</p>
      `;
      importButton.disabled = false;
    } catch (err) {
      body.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
    }
  })();

  importButton.addEventListener("click", async () => {
    importButton.disabled = true;
    try {
      await post("/api/wiser/import", { device_id: Number(device.device_id), weeks: [week] });
      toast("Planning Wiser importé.");
      closeSheet();
      await refreshState({ silent: true });
      dirty = false;
      reloadFromState();
    } catch (err) {
      toastError(err);
      importButton.disabled = false;
    }
  });

  return sheet;
}

/* --------------------------------------------------------------- divers */

function reloadFromState() {
  if (store.prefs.week === "A" || store.prefs.week === "B") week = store.prefs.week;
  buildTargets();
  if (!targets.length) return;
  target = pickTarget();
  rebuildModels();
  paintWeekSwitch();
  paintRooms();
  paintDirty();
  paintSub();
}

function warnUnload(event) {
  if (!dirty) return;
  event.preventDefault();
  event.returnValue = "";
}
