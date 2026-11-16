// Modèles de planning : liste pleine page, édition au clic, liaison aux pièces
// (cocher une pièce applique le modèle et le maintient synchronisé). Les
// modèles et leurs liaisons sont persistés côté serveur.

import { del, get, put } from "../api.js";
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
let links = {};
let selectedIndex = null;
let week = "A";
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
      id: String(model.id || ""),
      name: String(model.name || "Sans nom"),
      days: { A: cloneWeek(model.days?.A), B: cloneWeek(model.days?.B) },
    }));
}

function normalizeLinks(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out = {};
  for (const [roomId, link] of Object.entries(raw)) {
    if (!link || typeof link !== "object") continue;
    out[String(roomId)] = {
      model_id: String(link.model_id || ""),
      pending: Boolean(link.pending),
    };
  }
  return out;
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
    links = normalizeLinks(payload?.links);
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
    links = normalizeLinks(payload?.links);
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

function roomLabel(roomId) {
  const id = String(roomId);
  const room = (store.state?.rooms || []).find((item) => String(item.id) === id);
  return room ? room.name || `Pièce ${room.id}` : "";
}

/** Noms des pièces liées au modèle, dans l'ordre d'affichage des pièces. */
function linkedRooms(modelId) {
  const id = String(modelId || "");
  if (!id) return [];
  const wanted = new Set(
    Object.entries(links)
      .filter(([, link]) => String(link.model_id) === id)
      .map(([roomId]) => String(roomId))
  );
  if (!wanted.size) return [];
  return sortByPosition(store.state?.rooms || [])
    .filter((room) => wanted.has(String(room.id)))
    .map((room) => room.name || `Pièce ${room.id}`);
}

function summary(model) {
  const parts = WEEKS.map((key) => {
    const count = countSlots(model.days?.[key]);
    return `Semaine ${key} : ${count ? `${count} créneau${count > 1 ? "x" : ""}` : "vide"}`;
  });
  const linked = linkedRooms(model.id);
  if (linked.length) {
    parts.push(`Lié à ${linked.join(", ")}`);
  }
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
        <p class="page-sub">Un modèle = un planning A/B réutilisable. Tape une ligne pour l'éditer, puis coche les pièces à y associer.</p>
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

    ${weekTools(model)}

    ${linkPanel(model)}

    <div class="card card-pad">
      ${gridHtml(days)}
      <div class="row wrap mt-2" style="gap:8px;justify-content:flex-end">
        <button class="btn btn-primary" type="button" data-save>${icon("save", 15)} Enregistrer</button>
      </div>
      <p class="hint mt-2">Tape un créneau pour le modifier, sur « + » pour en ajouter un. Reviens à la liste puis clique sur « Enregistrer » pour sauvegarder tous les modèles.</p>
    </div>
  </div>`;
}

function weekTools() {
  const other = week === "A" ? "B" : "A";
  return `<div class="card card-pad stack">
    <div>
      <div class="card-title">${icon("copy", 18)} Copier une semaine</div>
      <p class="hint mt-1">Duplique les créneaux d'une semaine sur l'autre : la semaine cible est remplacée.</p>
    </div>
    <div class="row wrap" style="gap:8px">
      <button class="btn btn-sm" type="button" data-copy="A">${icon("copy", 15)} Copier A → B</button>
      <button class="btn btn-sm" type="button" data-copy="B">${icon("copy", 15)} Copier B → A</button>
      <button class="btn btn-sm btn-ghost" type="button" data-clear-week>${icon("trash", 15)} Vider la semaine ${week}</button>
    </div>
    <p class="hint">La semaine ${other} n'est pas modifiée par ces actions.</p>
  </div>`;
}

function copyWeekTo(source) {
  const model = currentModel();
  if (!model) return;
  const from = source === "B" ? "B" : "A";
  const to = from === "A" ? "B" : "A";
  if (!confirm(`Remplacer la semaine ${to} de « ${model.name} » par la semaine ${from} ?`)) return;
  model.days[to] = cloneWeek(model.days[from]);
  markDirty();
  week = to;
  render();
  toast(`Semaine ${from} copiée sur la semaine ${to}.`);
}

function clearWeek() {
  const model = currentModel();
  if (!model) return;
  if (!confirm(`Vider la semaine ${week} de « ${model.name} » ?`)) return;
  model.days[week] = emptyWeek();
  markDirty();
  render();
  toast(`Semaine ${week} vidée.`, { type: "info" });
}

function linkPanel(model) {
  const rooms = roomTargets();
  const canLink = Boolean(model.id) && !dirty;

  const rows = rooms.length
    ? rooms
        .map((room) => {
          const link = links[String(room.id)];
          const checked = Boolean(link) && String(link.model_id) === String(model.id);
          const other = link && !checked ? modelName(link.model_id) : "";
          return `<label class="list-row model-pick${checked ? " is-on" : ""}">
            <span class="li-main model-room">
              <input class="model-link-check" type="checkbox" data-link-room="${room.id}"${checked ? " checked" : ""}${canLink ? "" : " disabled"}>
              <span class="li-title">${escapeHtml(room.label)}</span>
              ${other ? `<span class="li-sub">liée à « ${escapeHtml(other)} »</span>` : ""}
            </span>
          </label>`;
        })
        .join("")
    : '<p class="hint">Aucune pièce pilotable pour le moment.</p>';

  const hint = !model.id
    ? "Enregistre le modèle pour pouvoir le lier à des pièces."
    : dirty
      ? "Enregistre tes modifications avant de lier des pièces."
      : "Cocher une pièce y applique le modèle et suit ensuite ses modifications. Décocher délie la pièce (son planning est conservé).";

  return `<div class="card card-pad stack">
    <div>
      <div class="card-title">${icon("link", 18)} Pièces liées à « ${escapeHtml(model.name)} »</div>
      <p class="hint mt-1">${hint}</p>
    </div>
    <div class="list">${rows}</div>
  </div>`;
}

function modelName(modelId) {
  const found = models.find((item) => String(item.id) === String(modelId));
  return found ? found.name : "";
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

async function toggleRoomLink(roomId, linked, model) {
  if (!model?.id) return;
  const input = root?.querySelector(`[data-link-room="${roomId}"]`);
  if (input) input.disabled = true;
  const label = roomLabel(roomId) || `pièce ${roomId}`;
  try {
    if (linked) {
      await put(`/api/model-links/${roomId}`, { model_id: model.id });
    } else {
      await del(`/api/model-links/${roomId}`);
    }
    await loadModels();
    await refreshState({ silent: true });
    render();
    toast(
      linked
        ? `« ${model.name} » lié à ${label}.`
        : `${label} déliée du modèle (planning conservé).`,
      { type: linked ? "ok" : "info" }
    );
  } catch (err) {
    toastError(err);
    render();
  }
}

/* --------------------------------------------------------------- montage */

function bind() {
  const page = root;
  if (!page) return;

  page.querySelectorAll("[data-save]").forEach((btn) => {
    btn.addEventListener("click", saveAllModels);
  });
  page.querySelector("[data-new]")?.addEventListener("click", createModel);
  page.querySelector("[data-back]")?.addEventListener("click", () => {
    selectedIndex = null;
    render();
  });

  page.querySelectorAll("[data-open]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedIndex = Number(button.dataset.open);
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

  page.querySelectorAll("[data-link-room]").forEach((input) => {
    input.addEventListener("change", () => {
      const model = currentModel();
      if (!model) return;
      toggleRoomLink(input.dataset.linkRoom, input.checked, model);
    });
  });

  page.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", () => copyWeekTo(button.dataset.copy));
  });
  page.querySelector("[data-clear-week]")?.addEventListener("click", clearWeek);

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
    dirty = false;
    render();
  },

  unmount() {
    closeSheet();
    root = null;
  },
};
