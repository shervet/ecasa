// Modèles de planning : liste pleine page, édition au clic, application à
// plusieurs pièces. Les modèles sont persistés côté serveur.

import { get, put } from "../api.js";
import { icon, modeIcon } from "../icons.js";
import { openSheet, closeSheet } from "../sheet.js";
import { devicesInRoom, refreshState, store } from "../state.js";
import { toast, toastError } from "../toast.js";
import {
  clamp,
  escapeHtml,
  fmtTemp,
  modeColor,
  modeLabel,
  modesList,
  sortByPosition,
  timeToMinutes,
  weekdayLong,
  weekdayShort,
} from "../utils.js";

const STORAGE_KEY = "ecasa.weekModels";
const WEEKS = ["A", "B"];

let root = null;
let models = [];
let selectedIndex = null;
let week = "A";
let applyWeeks = new Set(["A"]);
let chosenRooms = new Set();
let dirty = false;
let saving = false;

/* --------------------------------------------------------------- stockage */

function emptyWeek() {
  return Array.from({ length: 7 }, () => []);
}

function cloneWeek(days) {
  return Array.from({ length: 7 }, (_, index) => {
    const slots = Array.isArray(days?.[index]) ? days[index] : [];
    return slots
      .filter((slot) => slot && slot.time)
      .map((slot) => ({
        time: String(slot.time),
        mode: String(slot.mode || "heat"),
        temp: Number(slot.temp ?? 20),
      }))
      .sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
  });
}

function normalizeModels(raw) {
  return (Array.isArray(raw) ? raw : [])
    .filter((model) => model && typeof model === "object")
    .map((model) => ({
      name: String(model.name || "Sans nom"),
      days: { A: cloneWeek(model.days?.A), B: cloneWeek(model.days?.B) },
    }));
}

function loadLocalModels() {
  try {
    return normalizeModels(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"));
  } catch {
    return [];
  }
}

async function loadModels() {
  try {
    const payload = await get("/api/models");
    models = normalizeModels(payload?.models);
  } catch {
    models = loadLocalModels();
    return;
  }

  if (!models.length) {
    const local = loadLocalModels();
    if (local.length) {
      try {
        const payload = await put("/api/models", { models: local });
        models = normalizeModels(payload?.models);
        localStorage.removeItem(STORAGE_KEY);
        toast("Modèles importés depuis cet appareil.", { type: "info", timeout: 3000 });
      } catch {
        models = local;
      }
    }
  }
}

function markDirty() {
  dirty = true;
  if (root) paintSaveState();
}

function paintSaveState() {
  const btn = root?.querySelector("[data-save]");
  const hint = root?.querySelector("[data-save-hint]");
  if (btn) {
    btn.disabled = saving || !dirty;
    btn.innerHTML = saving
      ? `${icon("refresh", 17)} Enregistrement…`
      : `${icon("save", 17)} Enregistrer`;
  }
  if (hint) {
    hint.textContent = dirty
      ? "Modifications non enregistrées."
      : "Tous les modèles sont enregistrés.";
    hint.classList.toggle("is-bad", dirty);
  }
}

async function saveAllModels() {
  if (saving || !dirty) return;
  saving = true;
  paintSaveState();
  try {
    const payload = await put("/api/models", { models });
    models = normalizeModels(payload?.models);
    dirty = false;
    localStorage.removeItem(STORAGE_KEY);
    toast("Modèles enregistrés.");
  } catch (err) {
    toastError(err);
  } finally {
    saving = false;
    paintSaveState();
  }
}

function countSlots(days) {
  return (days || []).reduce(
    (total, slots) => total + (Array.isArray(slots) ? slots.length : 0),
    0
  );
}

/* --------------------------------------------------------------- cibles */

function roomTargets() {
  const state = store.state;
  if (!state) return [];
  return sortByPosition(state.rooms || [])
    .filter((room) => devicesInRoom(state, room.id).length > 0)
    .map((room) => ({
      id: room.id,
      label: room.name || `Pièce ${room.id}`,
    }));
}

/* ------------------------------------------------------------------ vues */

function summary(model) {
  const parts = WEEKS.map((key) => {
    const count = countSlots(model.days?.[key]);
    return `Semaine ${key} : ${count ? `${count} créneau${count > 1 ? "x" : ""}` : "vide"}`;
  });
  return parts.join(" · ");
}

function listView() {
  const rows = models.length
    ? models
        .map(
          (model, index) => `<div class="list-row model-pick">
            <button class="li-main" type="button" data-open="${index}">
              <span class="li-title">${escapeHtml(model.name)}</span>
              <span class="li-sub">${summary(model)}</span>
            </button>
            <div class="li-actions">
              <button class="btn btn-sm btn-danger" type="button" data-delete-model="${index}" aria-label="Supprimer ${escapeHtml(model.name)}">${icon("trash", 14)}</button>
            </div>
          </div>`
        )
        .join("")
    : `<div class="empty">
        <span class="empty-icon">${icon("sliders", 24)}</span>
        <strong>Aucun modèle</strong>
        <p>Crée un modèle pour réutiliser un planning sur plusieurs pièces.</p>
      </div>`;

  return `<div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">Modèles</h1>
        <p class="page-sub">Un modèle = un planning A/B réutilisable. Tape une ligne pour l'éditer, puis applique-la à plusieurs pièces.</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-save ${dirty ? "" : "disabled"}>${icon("save", 17)} Enregistrer</button>
        <button class="btn btn-primary" type="button" data-new>${icon("plus", 17)} Nouveau</button>
      </div>
    </div>
    <div class="card"><div class="list">${rows}</div></div>
    <p class="hint" data-save-hint>${dirty ? "Modifications non enregistrées." : "Tous les modèles sont enregistrés."}</p>
  </div>`;
}

function detailView() {
  const model = models[selectedIndex];
  if (!model) {
    selectedIndex = null;
    return listView();
  }
  const days = model.days[week] || (model.days[week] = emptyWeek());

  return `<div class="page">
    <div class="page-head">
      <div>
        <button class="btn btn-sm btn-ghost" type="button" data-back>${icon("chevron-left", 16)} Tous les modèles</button>
        <h1 class="page-title" data-title>${escapeHtml(model.name)}</h1>
      </div>
    </div>

    <div class="card card-pad stack">
      <label class="field">
        <span class="label">Nom du modèle</span>
        <input class="input" type="text" value="${escapeHtml(model.name)}" data-rename>
      </label>
      <div class="row wrap" style="gap:10px;align-items:center">
        <div class="week-switch" data-week-switch>
          ${WEEKS.map(
            (key) =>
              `<button type="button" data-week="${key}" class="${key === week ? "is-active" : ""}">Semaine ${key}${
                countSlots(model.days[key]) ? " ·" : ""
              }</button>`
          ).join("")}
        </div>
        <button class="btn btn-sm" type="button" data-duplicate>${icon("copy", 15)} Dupliquer</button>
        <button class="btn btn-sm btn-danger" type="button" data-delete>${icon("trash", 15)} Supprimer</button>
      </div>
    </div>

    ${applyPanel(model)}

    <div class="card card-pad">
      ${gridHtml(days)}
      <p class="hint mt-2">Tape un créneau pour le modifier, sur « + » pour en ajouter un. Reviens à la liste puis clique sur « Enregistrer » pour sauvegarder tous les modèles.</p>
    </div>
  </div>`;
}

function applyPanel(model) {
  const rooms = roomTargets();
  const weeks = [...applyWeeks];
  const filled = weeks.filter((key) => countSlots(model.days[key]) > 0);

  const rows = rooms.length
    ? rooms
        .map(
          (room) => `<div class="list-row model-pick${chosenRooms.has(String(room.id)) ? " is-on" : ""}">
            <button class="li-main model-room" type="button" data-room="${room.id}">
              <span class="li-check">${icon("check", 14)}</span>
              <span class="li-title">${escapeHtml(room.label)}</span>
            </button>
          </div>`
        )
        .join("")
    : '<p class="hint">Aucune pièce pilotable pour le moment.</p>';

  const disabled = !chosenRooms.size || !filled.length;

  return `<div class="card card-pad stack">
    <div class="row-between wrap" style="gap:10px">
      <div>
        <div class="card-title">${icon("sliders", 18)} Appliquer « ${escapeHtml(model.name)} »</div>
        <p class="hint mt-1">Choisis les semaines et les pièces : chaque semaine cochée est remplacée par le modèle.</p>
      </div>
      <div class="segmented" data-apply-weeks>
        ${WEEKS.map(
          (key) =>
            `<button type="button" data-aw="${key}" class="${applyWeeks.has(key) ? "is-active" : ""}">Semaine ${key}</button>`
        ).join("")}
      </div>
    </div>
    <div class="list">${rows}</div>
    <button class="btn btn-primary btn-block" type="button" data-apply-run${disabled ? " disabled" : ""}>
      ${icon("check", 17)} Appliquer${chosenRooms.size ? ` à ${chosenRooms.size} pièce${chosenRooms.size > 1 ? "s" : ""}` : ""}
    </button>
  </div>`;
}

function gridHtml(days) {
  let html = '<div class="week-grid">';
  for (let index = 0; index < 7; index++) {
    const slots = days[index] || [];
    html += '<div class="week-col">';
    html += '<div class="week-col-head">';
    html += `<span class="week-col-dow">${weekdayShort(index)}</span>`;
    html += `<span class="week-col-dom">${slots.length ? `${slots.length} créneau${slots.length > 1 ? "x" : ""}` : "—"}</span>`;
    html += "</div>";
    html += '<div class="week-col-body">';
    slots.forEach((slot, slotIndex) => {
      html += `<button type="button" class="week-slot" data-day="${index}" data-slot="${slotIndex}" style="--slot-color:${modeColor(slot.mode)}">`;
      html += `<span class="week-slot-time">${escapeHtml(slot.time)}</span>`;
      html += `<span class="week-slot-temp">${fmtTemp(slot.temp)} °C</span>`;
      html += `<span class="week-slot-mode">${escapeHtml(modeLabel(slot.mode, store.meta))}</span>`;
      html += "</button>";
    });
    if (!slots.length) html += '<div class="week-slot week-slot-empty">—</div>';
    html += `<button type="button" class="week-add" data-add="${index}" title="Ajouter un créneau">+</button>`;
    html += "</div></div>";
  }
  return `${html}</div>`;
}

/* ------------------------------------------------------------ édition */

function currentModel() {
  return selectedIndex === null ? null : models[selectedIndex];
}

function render() {
  if (!root) return;
  root.innerHTML = selectedIndex === null ? listView() : detailView();
  bind();
  if (selectedIndex === null) paintSaveState();
}

function createModel() {
  let name = "Nouveau modèle";
  let suffix = 2;
  while (models.some((model) => model.name === name)) {
    name = `Nouveau modèle ${suffix++}`;
  }
  models.push({ name, days: { A: emptyWeek(), B: emptyWeek() } });
  markDirty();
  selectedIndex = models.length - 1;
  week = "A";
  chosenRooms.clear();
  render();
  const input = root?.querySelector("[data-rename]");
  input?.focus();
  if (input?.select) input.select();
}

function duplicateModel() {
  const model = currentModel();
  if (!model) return;
  let name = `${model.name} (copie)`;
  let suffix = 2;
  while (models.some((item) => item.name === name)) {
    name = `${model.name} (copie ${suffix++})`;
  }
  models.push({ name, days: { A: cloneWeek(model.days.A), B: cloneWeek(model.days.B) } });
  markDirty();
  selectedIndex = models.length - 1;
  render();
  toast(`Modèle « ${name} » créé.`);
}

function deleteModel() {
  const model = currentModel();
  if (!model) return;
  if (!confirm(`Supprimer le modèle « ${model.name} » ?`)) return;
  models.splice(selectedIndex, 1);
  markDirty();
  selectedIndex = null;
  chosenRooms.clear();
  render();
  toast("Modèle supprimé.", { type: "info" });
}

function openSlotEditor(dayIndex, slotIndex) {
  const model = currentModel();
  if (!model) return;
  const days = model.days[week] || (model.days[week] = emptyWeek());
  const slots = days[dayIndex] || (days[dayIndex] = []);
  const existing = slotIndex !== null && slotIndex >= 0 ? slots[slotIndex] : null;

  let time = existing?.time || "09:00";
  let mode = existing?.mode || "heat";
  let temp = Number(existing?.temp ?? 20);

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <div class="form-grid">
      <label class="field">
        <span class="label">Heure</span>
        <input class="input" type="time" step="900" value="${escapeHtml(time)}" data-time>
      </label>
      <label class="field">
        <span class="label">Consigne</span>
        <div class="input-row">
          <button class="btn btn-sm" type="button" data-temp="-1">−</button>
          <input class="input" type="number" min="0" max="30" step="0.5" value="${temp}" data-temp-input style="text-align:center">
          <button class="btn btn-sm" type="button" data-temp="1">+</button>
        </div>
      </label>
    </div>
    <div class="field">
      <span class="label">Mode</span>
      <div class="mode-grid" data-mode-grid>
        ${modesList(store.meta)
          .map(
            ([key, label]) => `<button class="mode-btn${key === mode ? " is-active" : ""}" type="button" data-mode="${key}">
              ${modeIcon(key, 20)}<span>${escapeHtml(label)}</span>
            </button>`
          )
          .join("")}
      </div>
    </div>
  `;

  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%";
  footer.innerHTML = `
    ${existing ? `<button class="btn btn-danger" type="button" data-delete>${icon("trash", 17)} Supprimer</button>` : ""}
    <button class="btn btn-primary" type="button" data-validate>${icon("check", 17)} ${existing ? "Mettre à jour" : "Ajouter"}</button>
  `;

  openSheet({
    title: existing ? "Modifier le créneau" : "Nouveau créneau",
    sub: `${weekdayLong(dayIndex)} · ${model.name}`,
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
      temp = clamp(temp + Number(button.dataset.temp) * 0.5, 0, 30);
      paint();
    });
  });
  tempInput.addEventListener("input", () => {
    temp = clamp(tempInput.value, 0, 30);
  });
  body.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      mode = button.dataset.mode;
      paint();
    });
  });
  paint();

  footer.querySelector("[data-validate]")?.addEventListener("click", () => {
    const slot = {
      time: timeInput.value || "00:00",
      mode,
      temp: Number(clamp(temp, 0, 30)),
    };
    const index = existing ? slots.indexOf(existing) : -1;
    if (index >= 0) slots[index] = slot;
    else slots.push(slot);
    slots.sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
    markDirty();
    closeSheet();
    render();
  });

  footer.querySelector("[data-delete]")?.addEventListener("click", () => {
    const index = existing ? slots.indexOf(existing) : -1;
    if (index >= 0) slots.splice(index, 1);
    markDirty();
    closeSheet();
    render();
  });
}

async function applyToRooms() {
  const model = currentModel();
  if (!model) return;
  const rooms = roomTargets().filter((room) => chosenRooms.has(String(room.id)));
  const weeks = [...applyWeeks].filter((key) => countSlots(model.days[key]) > 0);
  if (!rooms.length) {
    toast("Choisis au moins une pièce.", { type: "warn" });
    return;
  }
  if (!weeks.length) {
    toast("Le modèle est vide : ajoute des créneaux d'abord.", { type: "warn" });
    return;
  }
  const target = rooms.map((room) => room.label).join(", ");
  const label = weeks.map((key) => `semaine ${key}`).join(" et ");
  if (!confirm(`Remplacer la ${label} de ${target} par « ${model.name} » ?`)) return;

  try {
    for (const room of rooms) {
      for (const key of weeks) {
        await put(`/api/programs/${room.id}/${key}`, {
          days: cloneWeek(model.days[key]),
        });
      }
    }
    toast(`Modèle « ${model.name} » appliqué à ${rooms.length} pièce${rooms.length > 1 ? "s" : ""}.`);
    await refreshState({ silent: true });
    chosenRooms.clear();
    render();
  } catch (err) {
    toastError(err);
  }
}

/* --------------------------------------------------------------- montage */

function bind() {
  const page = root;
  if (!page) return;

  page.querySelector("[data-save]")?.addEventListener("click", saveAllModels);
  page.querySelector("[data-new]")?.addEventListener("click", createModel);
  page.querySelector("[data-back]")?.addEventListener("click", () => {
    selectedIndex = null;
    chosenRooms.clear();
    render();
  });

  page.querySelectorAll("[data-open]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedIndex = Number(button.dataset.open);
      chosenRooms.clear();
      render();
    });
  });

  page.querySelectorAll("[data-delete-model]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const index = Number(button.dataset.deleteModel);
      const model = models[index];
      if (!model) return;
      if (!confirm(`Supprimer le modèle « ${model.name} » ?`)) return;
      models.splice(index, 1);
      markDirty();
      if (selectedIndex === index) selectedIndex = null;
      render();
      toast("Modèle supprimé.", { type: "info" });
    });
  });

  page.querySelector("[data-rename]")?.addEventListener("input", (event) => {
    const model = currentModel();
    if (!model) return;
    model.name = event.currentTarget.value;
    markDirty();
    const title = page.querySelector("[data-title]");
    if (title) title.textContent = model.name || "Sans nom";
  });
  page.querySelector("[data-rename]")?.addEventListener("change", () => {
    const model = currentModel();
    if (!model) return;
    model.name = String(model.name || "").trim() || "Sans nom";
    markDirty();
  });

  page.querySelectorAll("[data-week]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.week === "B" ? "B" : "A";
      if (next === week) return;
      week = next;
      render();
    });
  });

  page.querySelector("[data-duplicate]")?.addEventListener("click", duplicateModel);
  page.querySelector("[data-delete]")?.addEventListener("click", deleteModel);

  page.querySelectorAll("[data-aw]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.aw === "B" ? "B" : "A";
      if (applyWeeks.has(key)) applyWeeks.delete(key);
      else applyWeeks.add(key);
      if (!applyWeeks.size) applyWeeks.add(key);
      render();
    });
  });

  page.querySelectorAll("[data-room]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.room;
      if (chosenRooms.has(id)) chosenRooms.delete(id);
      else chosenRooms.add(id);
      render();
    });
  });

  page.querySelector("[data-apply-run]")?.addEventListener("click", applyToRooms);

  page.querySelectorAll("[data-day]").forEach((slot) => {
    slot.addEventListener("click", () => {
      openSlotEditor(Number(slot.dataset.day), Number(slot.dataset.slot));
    });
  });
  page.querySelectorAll("[data-add]").forEach((button) => {
    button.addEventListener("click", () => {
      openSlotEditor(Number(button.dataset.add), null);
    });
  });
}

export default {
  title: "Modèles",

  async mount(view) {
    root = view;
    root.innerHTML = `<div class="boot" style="min-height:220px"><span class="spinner"></span><p>Chargement des modèles…</p></div>`;
    await loadModels();
    selectedIndex = null;
    week = "A";
    chosenRooms.clear();
    applyWeeks = new Set(["A"]);
    dirty = false;
    render();
  },

  unmount() {
    closeSheet();
    root = null;
  },
};
