// Planning hebdomadaire : agenda tactile, éditeur de créneaux, imports Pronote/Wiser.

import { get, post, put } from "../api.js";
import { icon, modeIcon } from "../icons.js";
import { openSheet, closeSheet } from "../sheet.js";
import { devicesInRoom, refreshState, setPref, store } from "../state.js";
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
            <div class="week-switch" id="planWeekSwitch">
              <button type="button" data-week="A">Semaine A</button>
              <button type="button" data-week="B">Semaine B</button>
            </div>
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

    paintPlanNav();

    week = store.prefs.week === "A" || store.prefs.week === "B" ? store.prefs.week : store.state?.week || "A";
    setPref("week", week);

    if (!targets.length) return;
    target = targets[0];
    rebuildModels();
    paintWeekSwitch();
    paintRooms();
    paintDirty();
    paintSub();

    const presetRoomSelect = view.querySelector("#planPresetRoom");
    const roomOptions = targets.map((t) => `<option value="${t.id}">${escapeHtml(t.label)}</option>`).join("");
    if (presetRoomSelect) presetRoomSelect.innerHTML = roomOptions;

    view.querySelectorAll("[data-week]").forEach((button) => {
      button.addEventListener("click", () => {
        const next = button.dataset.week;
        if (next === week) return;
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

    view.querySelector("#planSave").addEventListener("click", save);
    view.querySelector("#planCopy").addEventListener("click", copyToOtherWeek);
    view.querySelector("#planImport").addEventListener("click", openImportSheet);
    view.querySelector("#planPronote")?.addEventListener("click", () => {
      if (!target) { toast("Choisis d'abord une zone.", { type: "warn" }); return; }
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
    const linkedRooms = Array.isArray(store.state?.settings?.pronote_room_ids)
      ? store.state.settings.pronote_room_ids.map(Number)
      : (Number(store.state?.settings?.pronote_room_id || 0) ? [Number(store.state.settings.pronote_room_id)] : []);
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

function paintPlanNav() {
  const nav = root?.querySelector("#planNav");
  if (!nav) return;
  let html = targets.map((t) => `<button type="button" class="plan-nav-btn" data-jump-room="${t.id}">${escapeHtml(t.label)}</button>`).join("");
  html += `<button type="button" class="plan-nav-btn" data-jump-models>Modèles</button>`;
  nav.innerHTML = html;

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
}

function paintSub() {
  const node = root?.querySelector("#planSub");
  if (!node) return;
  node.textContent = `Semaine ${week} · ${targets.length} pièce${targets.length > 1 ? "s" : ""}`;
}

function paintRooms() {
  const host = root?.querySelector("#planRooms");
  if (!host) return;
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
    try { savedModels = JSON.parse(localStorage.getItem("mecasa.weekModels") || "[]"); } catch { savedModels = []; }
    let modelSelect = '<select class="select plan-model-select" data-target="' + t.id + '" style="width:auto;min-width:150px"><option value="">— modèle —</option>';
    for (let i = 0; i < savedModels.length; i++) {
      const sel = String(i) === String(selVal) ? " selected" : "";
      modelSelect += '<option value="' + i + '"' + sel + '>' + escapeHtml(savedModels[i].name) + '</option>';
    }
    modelSelect += "</select>";
    const isApplied = selVal !== "" && appliedIdx !== undefined && Number(appliedIdx) === Number(selVal);
    const applyBtnClass = "btn btn-sm plan-apply-btn" + (isApplied ? " is-applied" : "");
    const applyBtnDisabled = isApplied ? " disabled" : "";

    const isLinked = !!t.pronoteLinked;
    html += '<div class="room-plan' + (isLinked ? " room-plan--readonly" : "") + '" data-room-anchor="' + t.id + '">';
    html += '<div class="room-plan-head">';
    html += '<div class="room-plan-title">' + escapeHtml(t.label) + ' <span class="pill pill-ghost" style="margin-left:6px">Semaine ' + escapeHtml(week) + '</span>' + (isLinked ? ' <span class="pill pill-accent" style="margin-left:6px">Lié à Pronote</span>' : '') + '</div>';
    html += deviceSelect;
    if (isLinked) {
      html += '<span class="pill pill-accent" style="margin-left:6px">Lié à Pronote</span>';
    }
    html += '<button class="btn btn-sm btn-danger" type="button" data-clear-room="' + t.id + '"' + (isLinked ? " disabled" : "") + '>' + icon("trash", 14) + ' Vider cette pièce</button>';
    html += '</div>';
    html += '<div class="room-plan-model-row">';
    html += modelSelect;
    html += '<button class="btn btn-sm plan-apply-btn' + (isApplied ? " is-applied" : "") + '" type="button" data-apply-model="' + t.id + '"' + (isApplied || isLinked ? " disabled" : "") + ' title="Appliquer le modèle">' + icon("check", 14) + '</button>';
    html += '</div>';
    let columns = "";
    for (let index = 0; index < days.length; index++) {
      const date = dates[index];
      const d = parseDate(date) || new Date();
      const dom = String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0");
      const isToday = index === nowIndex && week === nowWeek;
      const slots = days[index] || [];
      const items = slots.slice().sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
      let rows = "";
      if (items.length) {
        for (const slot of items) {
          const end = slotEnd(slots, slot);
          const slotIndex = slots.indexOf(slot);
          rows += '<button type="button" class="week-slot" data-target="' + t.id + '" data-day="' + index + '" data-slotindex="' + slotIndex + '"' + (isLinked ? ' disabled' : '') + ' style="--slot-color:' + modeColor(slot.mode) + '">';
          rows += '<span class="week-slot-time">' + escapeHtml(slot.time) + (end ? "\u2013" + escapeHtml(end) : "") + '</span>';
          rows += '<span class="week-slot-temp">' + fmtTemp(slot.temp) + ' °C</span>';
          rows += '<span class="week-slot-mode">' + escapeHtml(modeLabel(slot.mode, store.meta)) + '</span>';
          rows += '</button>';
        }
      } else {
        rows = '<div class="week-slot week-slot-empty">—</div>';
      }
      columns += '<div class="week-col' + (isToday ? " is-today" : "") + '">';
      columns += '<div class="week-col-head">';
      columns += '<span class="week-col-dow">' + weekdayShort(index) + '</span>';
      columns += '<span class="week-col-dom">' + dom + '</span>';
      columns += '</div>';
      columns += '<div class="week-col-body">';
      columns += rows;
      columns += '<button type="button" class="week-add" data-target="' + t.id + '" data-day="' + index + '" title="Ajouter un créneau"' + (isLinked ? ' disabled' : '') + '>+</button>';
      columns += '</div></div>';
    }

    html += '<div class="week-grid">' + columns + '</div>';
    html += '</div>';
  }

  host.innerHTML = html;

  // Wire slot clicks
  host.querySelectorAll(".week-slot[data-target]").forEach((el) => {
    el.addEventListener("click", () => {
      const tid = el.dataset.target;
      const tt = targets.find((x) => x.id === tid);
      if (!tt) return;
      target = tt;
      rebuildModels();
      const wd = Number(el.dataset.day);
      const si = Number(el.dataset.slotindex);
      openSlotEditor(target, wd, si >= 0 ? si : null);
    });
  });

  // Wire add buttons
  host.querySelectorAll(".week-add").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const tid = el.dataset.target;
      const tt = targets.find((x) => x.id === tid);
      if (!tt) return;
      target = tt;
      rebuildModels();
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
      try { saved = JSON.parse(localStorage.getItem("mecasa.weekModels") || "[]"); } catch { saved = []; }
      const m = saved[idx];
      if (!m) { toast("Modèle introuvable.", { type: "warn" }); return; }
      if (!confirm(`Appliquer le modèle « ${m.name} » à ${t.label} ?`)) return;
      t.device.programs = t.device.programs || {};
      t.device.programs.A = cloneDays(m.days?.A);
      t.device.programs.B = cloneDays(m.days?.B);
      selectedModelByTarget[tid] = idx;
      appliedModelByTarget[tid] = idx;
      rebuildModels();
      dirty = true;
      paintRooms();
      paintDirty();
      toast(`Modèle « ${m.name} » appliqué.`);
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
  if (!confirm(`Vider tous les créneaux de la semaine ${week} pour ${t.label} ?`)) return;
  const model = models[t.id];
  if (model) {
    model[week] = Array.from({ length: 7 }, () => []);
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

function openSlotEditor(target, dayIndex, slotIndex = null, minutes = 0) {
  if (!target) return;
  const model = models[target.id];
  if (!model) return;
  const slots = model[week][dayIndex];
  const existing = slotIndex !== null ? slots[slotIndex] : null;
  let time = existing?.time || minutesToTime(minutes);
  let mode = existing?.mode || "heat";
  const { min: tempMin, max: tempMax } = deviceTempRange(target.device);
  let temp = clamp(Number(existing?.temp ?? 20), tempMin, tempMax);

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
          .filter(([key]) => (target.device?.device_type === "wiser" ? key === "heat" || key === "off" : true))
          .map(
            ([key, label]) => `<button class="mode-btn${key === mode ? " is-active" : ""}" type="button" data-mode="${key}">
              ${modeIcon(key, 20)}<span>${escapeHtml(label)}</span>
            </button>`
          )
          .join("")}
      </div>
      ${target.device?.device_type === "wiser" ? '<p class="hint">Sur Wiser, « Arrêt » est appliqué par une consigne hors gel à 6 °C.</p>' : ""}
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
  if (!confirm(`Remplacer toute la semaine ${week} de ${target.label} par le modèle « ${key} » ? (Tous les créneaux existants seront effacés)`)) {
    return;
  }
  model[week] = preset.map((daySlots) => 
    Array.isArray(daySlots) ? daySlots.map((slot) => ({ ...slot })).sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time)) : []
  );
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
      await put(`/api/programs/${t.targetId}/${week}`, { days: model[week] });
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
  const linkedRooms = Array.isArray(store.state?.settings?.pronote_room_ids)
    ? store.state.settings.pronote_room_ids.map(Number)
    : (Number(store.state?.settings?.pronote_room_id || 0) ? [Number(store.state.settings.pronote_room_id)] : []);
  let selectedTarget = target;
  if (linkedRooms.length) {
    const found = targets.find((t) => t.kind === "room" && linkedRooms.includes(Number(t.targetId)));
    if (found) selectedTarget = found;
  }
  if (!selectedTarget) {
    selectedTarget = targets[0] || null;
  }
  let device = selectedTarget?.device;
  if (!device) { toast("Aucun appareil pour cette zone.", { type: "warn" }); return; }

  let plan = null;
  const monday = mondayOf(new Date());
  let savedPronoteTarget = null;
  try {
    savedPronoteTarget = localStorage.getItem("mecasa.pronoteTargetId");
  } catch {}

  if (!linkedRoomId && savedPronoteTarget) {
    const found = targets.find((t) => t.id === savedPronoteTarget);
    if (found) {
      selectedTarget = found;
      device = selectedTarget.device;
    }
  }

  const body = document.createElement("div");
  body.className = "stack";
  const parity = (store.state?.settings?.week_mode || "parity") === "parity";
  let chosenWeeks = new Set(week === "B" ? ["B"] : ["A"]);
  const isLinked = (t) => t && t.kind === "room" && linkedRooms.includes(Number(t.targetId));
  body.innerHTML = `
    <label class="field">
      <span class="label">Importer dans</span>
      <select class="select" data-pronote-target>
        ${targets.map((t) => `<option value="${t.id}"${t.id === selectedTarget?.id ? " selected" : ""}>${escapeHtml(t.label)}</option>`).join("")}
      </select>
    </label>
    ${
      parity
        ? `<div class="field">
      <span class="label">Écrire dans les semaines</span>
      <div class="row wrap" style="gap:10px">
        <label class="check"><input type="checkbox" data-pronote-week="A"${chosenWeeks.has("A") ? " checked" : ""}> Semaine A</label>
        <label class="check"><input type="checkbox" data-pronote-week="B"${chosenWeeks.has("B") ? " checked" : ""}> Semaine B</label>
      </div>
      <span class="hint">Seule la journée (7 h → 20 h) est remplacée : le réveil et la nuit du soir sont conservés.</span>
    </div>`
        : ""
    }
    <label class="field">
      <span class="label">Rafraîchissement automatique</span>
      <label class="check"><input type="checkbox" data-pronote-auto${isLinked(selectedTarget) ? " checked" : ""}> Récupérer l'emploi du temps Pronote chaque nuit et l'appliquer à cette pièce</label>
      <span class="hint">Si coché, cette pièce devient la pièce liée à Pronote (les autres sont déliées).</span>
    </label>
    <label class="field">
      <span class="label">Semaine à analyser</span>
      <input class="input" type="date" value="${monday}" data-week-start>
      <span class="hint">Le lundi de la semaine scolaire. L'aperçu ne modifie rien.</span>
    </label>
    <button class="btn" type="button" data-preview>${icon("search", 17)} Charger l'aperçu</button>
    <div data-preview-zone></div>
  `;

  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%";
  footer.innerHTML = `<button class="btn btn-primary" type="button" data-do-import disabled>${icon("download", 17)} Importer</button>`;

  const sheet = openSheet({
    title: "Import Pronote",
    sub: `Emploi du temps → ${selectedTarget.label}`,
    body,
    footer,
  });

  const previewZone = body.querySelector("[data-preview-zone]");
  const importButton = footer.querySelector("[data-do-import]");
  const targetSelect = body.querySelector("[data-pronote-target]");

  targetSelect?.addEventListener("change", () => {
    const tid = targetSelect.value;
    const t = targets.find((x) => x.id === tid);
    if (t) {
      selectedTarget = t;
      device = selectedTarget.device;
      try { localStorage.setItem("mecasa.pronoteTargetId", t.id); } catch {}
      sheet.querySelector(".sheet-sub").textContent = `Emploi du temps → ${t.label}`;
      const autoBox = body.querySelector("[data-pronote-auto]");
      if (autoBox) {
        const linked = Number(store.state?.settings?.pronote_room_id || 0);
        autoBox.checked = t.kind === "room" && Number(t.targetId) === linked;
      }
    }
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

  body.querySelector("[data-pronote-auto]")?.addEventListener("change", async (event) => {
    const box = event.currentTarget;
    const roomPayload =
      selectedTarget?.kind === "room"
        ? (box.checked
            ? { pronote_room_ids: [...new Set([...linkedRooms, Number(selectedTarget.targetId)])] }
            : { pronote_room_ids: linkedRooms.filter((id) => id !== Number(selectedTarget.targetId)) })
        : {};
    try {
      if (Object.keys(roomPayload).length) {
        const cfg = await put("/api/config", roomPayload);
        const newIds = box.checked
          ? [...new Set([...linkedRooms, Number(selectedTarget.targetId)])]
          : linkedRooms.filter((id) => id !== Number(selectedTarget.targetId));
        store.state = { ...store.state, settings: { ...(store.state?.settings || {}), pronote_room_ids: newIds, pronote_room_id: newIds[0] || 0 } };
        await refreshState({ silent: true });
      }
      toast(box.checked ? "Rafraîchissement automatique activé." : "Rafraîchissement automatique désactivé.");
    } catch (err) {
      toastError(err);
      box.checked = !box.checked;
    }
  });

  body.querySelector("[data-preview]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    previewZone.innerHTML = `<div class="boot" style="min-height:120px"><span class="spinner"></span><p>Lecture de Pronote…</p></div>`;
    try {
      const start = body.querySelector("[data-week-start]")?.value || monday;
      plan = await post("/api/pronote/preview", { week: start });
      previewZone.innerHTML = renderPronotePreview(plan);
      importButton.disabled = false;
    } catch (err) {
      previewZone.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
      importButton.disabled = true;
    } finally {
      button.disabled = false;
    }
  });

  importButton.addEventListener("click", async () => {
    if (!plan || !device) return;
    importButton.disabled = true;
    try {
      const start = body.querySelector("[data-week-start]")?.value || monday;
      try { localStorage.setItem("mecasa.pronoteTargetId", selectedTarget.id); } catch {}
      // La synchro automatique écrit sur la pièce : l'import manuel doit
      // viser la même cible, sinon les deux imports se contredisent.
      const targetPayload =
        selectedTarget.kind === "room"
          ? { room_id: Number(selectedTarget.targetId) }
          : { device_id: Number(device.device_id) };
      const weeksToWrite = [...chosenWeeks];
      if (!weeksToWrite.length) weeksToWrite.push("A");
      for (const w of weeksToWrite) {
        await post("/api/pronote/import", {
          ...targetPayload,
          week: w,
          week_start: start,
          days: plan.days,
          preview_week_type: plan.week_type,
        });
      }
      toast(`Planning Pronote importé dans ${selectedTarget.label} · semaine${weeksToWrite.length > 1 ? "s" : ""} ${weeksToWrite.join(", ")}.`);
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
        <p class="hint">L'import remplace les créneaux MeCasa de la semaine ${week} pour cette zone.</p>
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
  buildTargets();
  if (!targets.length) return;
  target = targets[0];
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
