// Feuille de réglage d'un appareil : thermostat, mode, forçage temporaire.

import { post, del, put } from "../api.js";
import { icon, modeIcon } from "../icons.js";
import { openSheet, closeSheet } from "../sheet.js";
import { setPendingPlanningRoom } from "../pages/planning.js";
import { notify, refreshState, store } from "../state.js";
import { toast, toastError } from "../toast.js";
import {
  clamp,
  describeSlot,
  deviceTempRange,
  escapeHtml,
  fmtTemp,
  fmtTime,
  inWords,
  modeColor,
  modeLabel,
  modesList,
  deviceModes,
  todayIndex,
  weekdayShort,
} from "../utils.js";

const DURATIONS = [
  [30, "30 min"],
  [60, "1 h"],
  [120, "2 h"],
  [240, "4 h"],
  [480, "8 h"],
];

function modelOptionsDevice() {
  try {
    const saved = JSON.parse(localStorage.getItem("ecasa.weekModels") || "[]");
    if (!Array.isArray(saved)) return "";
    return saved.map((m, i) => `<option value="${i}">${escapeHtml(m.name)}</option>`).join("");
  } catch {
    return "";
  }
}

function cloneDaysDevice(days) {
  const source = Array.isArray(days) ? days : [];
  return Array.from({ length: 7 }, (_, index) => {
    const list = Array.isArray(source[index]) ? source[index] : [];
    return list.map((slot) => ({
      time: slot.time || "00:00",
      mode: slot.mode || "off",
      temp: Number(slot.temp ?? 20),
    }));
  });
}

export function openDeviceSheet(deviceId) {
  const device = store.state?.devices?.find((item) => Number(item.device_id) === Number(deviceId));
  if (!device) {
    toast("Appareil introuvable.", { type: "error" });
    return;
  }

  const current = device.current || {};
  const desired = device.desired || {};
  const isWiser = device.device_type === "wiser";
  const { min, max } = deviceTempRange(device);
  let temp = clamp(desired.temp ?? current.temp ?? 20, min, max);
  let mode = desired.mode || current.mode || (isWiser ? "heat" : "cool");
  let minutes = 60;

  const room = store.state.rooms?.find((item) => Number(item.id) === Number(device.room_id));

  // Modes de la pièce : un Wiser ne sait que chauffer, mais la pièce peut
  // contenir une climatisation. On affiche alors les modes de clim, grisés
  // pour cet appareil, avec un raccourci vers l'appareil qui les applique.
  const roomDevices = (store.state?.devices || []).filter(
    (item) => Number(item.room_id) === Number(device.room_id)
  );
  const ownModes = deviceModes(device);
  const otherClim = roomDevices.find(
    (item) =>
      Number(item.device_id) !== Number(device.device_id) &&
      deviceModes(item).includes("cool")
  );
  const roomModes = new Set(ownModes);
  for (const mode of deviceModes(otherClim)) roomModes.add(mode);

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <div class="row wrap" style="gap:8px">
      <span class="pill ${isWiser ? "pill-accent" : ""}">${isWiser ? "Wiser" : "Atlantic"}</span>
      ${device.source ? `<span class="pill pill-ghost">${icon("clock", 13)} ${escapeHtml(device.source)}</span>` : ""}
      ${device.until ? `<span class="pill pill-warn">jusqu'à ${fmtTime(device.until)}</span>` : ""}
      ${device.current?.available === false ? '<span class="pill pill-bad">hors ligne</span>' : ""}
    </div>

    <div class="thermo">
      <div class="thermo-dial">
        <div class="thermo-value">
          <span class="t" data-thermo-temp>${fmtTemp(temp)}</span><span class="u">°C</span>
          <div class="m" data-thermo-mode>${escapeHtml(modeLabel(mode, store.meta))}</div>
        </div>
      </div>
      <div class="thermo-controls">
        <button class="thermo-step" type="button" data-step="-1" aria-label="Diminuer la consigne">−</button>
        <input class="thermo-range" type="range" min="${min}" max="${max}" step="0.5" value="${temp}" data-temp-range aria-label="Consigne">
        <button class="thermo-step" type="button" data-step="1" aria-label="Augmenter la consigne">+</button>
      </div>
    </div>

    <div class="mode-grid" data-mode-grid>
      ${modesList(store.meta)
        .filter(([key]) => roomModes.has(key))
        .map(
          ([key, label]) => {
            const allowed = ownModes.includes(key);
            return `<button class="mode-btn${key === mode ? " is-active" : ""}" type="button" data-mode="${key}"${allowed ? "" : ' disabled title="Non disponible sur cet appareil"'}>
              ${modeIcon(key, 20)}<span>${escapeHtml(label)}</span>
            </button>`;
          }
        )
        .join("")}
    </div>
    ${
      otherClim
        ? `<p class="hint">« Froid », « Déshumidification » et « Ventilation » s'appliquent à <strong>${escapeHtml(otherClim.name)}</strong> dans cette pièce.</p>
           <button class="btn btn-sm" type="button" data-open-clim="${escapeHtml(otherClim.device_id)}">${icon("sliders", 15)} Ouvrir ${escapeHtml(otherClim.name)}</button>`
        : ""
    }

    <div class="field">
      <span class="label">Durée du forçage</span>
      <div class="segmented is-block" data-durations>
        ${DURATIONS.map(
          ([value, label]) =>
            `<button type="button" data-minutes="${value}" class="${value === minutes ? "is-active" : ""}">${label}</button>`
        ).join("")}
      </div>
      <p class="hint">Le planning reprend automatiquement à la fin du forçage.</p>
    </div>

    <div class="card card-pad" style="box-shadow:none;background:var(--surface-2)">
      <div class="row-between">
        <div>
          <div class="tiny muted">Ambiance mesurée</div>
          <div style="font-size:20px;font-weight:750">${fmtTemp(current.ambient ?? current.wiser_ambient)} °C</div>
        </div>
        <div style="text-align:right">
          <div class="tiny muted">Consigne actuelle</div>
          <div style="font-size:20px;font-weight:750">${fmtTemp(current.temp)} °C</div>
        </div>
      </div>
      ${!isWiser && (current.fan_label || current.airflow_label)
        ? `<div class="row wrap mt-2" style="gap:6px">
            ${current.fan_label ? `<span class="pill pill-ghost">Soufflerie ${escapeHtml(current.fan_label)}</span>` : ""}
            ${current.airflow_label ? `<span class="pill pill-ghost">Orientation ${escapeHtml(current.airflow_label)}</span>` : ""}
          </div>`
        : ""}
      ${device.next ? `<div class="hint mt-2">Prochain changement ${inWords(device.next.in_minutes)} : ${escapeHtml(device.next.describe || "")}</div>` : ""}
    </div>

    <div>
      <div class="row-between">
        <span class="label">Programme du jour${store.state?.week ? ` · semaine ${store.state.week}` : ""}</span>
        <button class="tiny link-btn" type="button" data-goto-planning>Modifier</button>
      </div>
      <div class="stack mt-1" data-program></div>
    </div>

    <div class="field">
      <span class="label">Appliquer un modèle</span>
      <div class="input-row">
        <select class="select" data-model style="flex:1">
          <option value="">— choisir un modèle —</option>
          ${modelOptionsDevice()}
        </select>
        <button class="btn btn-sm" type="button" data-apply-model>${icon("check", 14)} Appliquer</button>
      </div>
    </div>
  `;

  const footer = document.createElement("div");
  footer.style.display = "flex";
  footer.style.gap = "8px";
  footer.style.width = "100%";
  footer.innerHTML = `
    ${device.source === "override" ? `<button class="btn btn-ghost" type="button" data-clear>${icon("rotate", 17)} Planning</button>` : ""}
    <button class="btn btn-primary" type="button" data-apply>${icon("check", 17)} Appliquer</button>
  `;

  const sheet = openSheet({
    title: device.name || `Appareil ${deviceId}`,
    sub: room ? `${room.name} · ${isWiser ? "zone Wiser" : "climatisation Atlantic"}` : isWiser ? "Zone Wiser" : "Climatisation Atlantic",
    body,
    footer,
  });

  const tempNode = body.querySelector("[data-thermo-temp]");
  const modeNode = body.querySelector("[data-thermo-mode]");
  const range = body.querySelector("[data-temp-range]");

  function paint() {
    tempNode.textContent = fmtTemp(temp);
    modeNode.textContent = modeLabel(mode, store.meta);
    range.value = String(temp);
    body.querySelectorAll("[data-mode]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.mode === mode);
    });
    body.querySelectorAll("[data-minutes]").forEach((button) => {
      button.classList.toggle("is-active", Number(button.dataset.minutes) === minutes);
    });
    const dial = body.querySelector(".thermo-dial");
    if (dial) {
      const ratio = (temp - min) / (max - min);
      dial.style.background = `radial-gradient(circle at 50% 42%, var(--surface) 0 56%, transparent 57%), conic-gradient(from 215deg, var(--cool) 0deg, var(--primary) 80deg, var(--warm) ${Math.round(
        90 + ratio * 120
      )}deg, var(--surface-3) ${Math.round(90 + ratio * 120)}deg 360deg)`;
    }
  }

  body.querySelectorAll("[data-step]").forEach((button) => {
    button.addEventListener("click", () => {
      temp = clamp(temp + Number(button.dataset.step) * 0.5, min, max);
      paint();
    });
  });

  range.addEventListener("input", () => {
    temp = clamp(range.value, min, max);
    paint();
  });

  body.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      mode = button.dataset.mode;
      paint();
    });
  });

  body.querySelectorAll("[data-minutes]").forEach((button) => {
    button.addEventListener("click", () => {
      minutes = Number(button.dataset.minutes);
      paint();
    });
  });

  paint();
  paintProgram(body.querySelector("[data-program]"), device);

  body.querySelector("[data-open-clim]")?.addEventListener("click", (event) => {
    const id = Number(event.currentTarget.dataset.openClim);
    if (!Number.isInteger(id)) return;
    closeSheet();
    window.setTimeout(() => openDeviceSheet(id), 120);
  });

  body.querySelector("[data-goto-planning]")?.addEventListener("click", () => {
    closeSheet();
    const roomId = device.room_id == null ? null : Number(device.room_id);
    if (Number.isInteger(roomId)) {
      setPendingPlanningRoom(roomId);
      // Lien profond : le Planning s'ouvre directement sur la pièce.
      location.hash = `#/planning/room:${roomId}`;
    } else {
      location.hash = "#/planning";
    }
  });

  body.querySelector("[data-apply-model]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const select = body.querySelector("[data-model]");
    const idx = Number(select?.value);
    if (!Number.isInteger(idx) || idx < 0) { toast("Choisis un modèle.", { type: "warn" }); return; }
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem("ecasa.weekModels") || "[]"); } catch { saved = []; }
    const m = saved[idx];
    if (!m) { toast("Modèle introuvable.", { type: "warn" }); return; }
    if (!confirm(`Appliquer le modèle « ${m.name} » à ${device.name} ?`)) return;
    button.disabled = true;
    try {
      await put(`/api/programs/${device.device_id}/A`, { days: cloneDaysDevice(m.days?.A) });
      await put(`/api/programs/${device.device_id}/B`, { days: cloneDaysDevice(m.days?.B) });
      await refreshState({ silent: true });
      toast(`Modèle « ${m.name} » appliqué.`);
    } catch (err) {
      toastError(err);
    } finally {
      button.disabled = false;
    }
  });

  footer.querySelector("[data-apply]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const snapshot = await post("/api/override", {
        device_id: Number(device.device_id),
        mode,
        temp: Number(temp),
        minutes,
      });
      if (snapshot && typeof snapshot === "object") {
        store.state = snapshot;
        store.connected = true;
        store.error = null;
        notify("state");
      } else {
        await refreshState({ silent: true });
      }
      toast("Forçage appliqué.");
      closeSheet();
    } catch (err) {
      toastError(err);
      button.disabled = false;
    }
  });

  footer.querySelector("[data-clear]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await del(`/api/override/${device.device_id}`);
      toast("Reprise du planning normal.");
      closeSheet();
      await refreshState({ silent: true });
    } catch (err) {
      toastError(err);
      button.disabled = false;
    }
  });

  return sheet;
}

function paintProgram(host, device) {
  if (!host) return;
  const week = store.state?.week || "A";
  const days = device.programs?.[week] || device.programs?.A || [];
  const day = days[todayIndex()] || [];

  if (!day.length) {
    host.innerHTML = `<p class="hint">Aucun créneau aujourd'hui. La consigne reste celle du dernier créneau connu.</p>`;
    return;
  }

  host.innerHTML = day
    .map(
      (slot, index) => `<div class="row-between" style="padding:8px 0;border-bottom:1px solid var(--line)">
        <span class="row" style="gap:9px">
          <span style="width:8px;height:8px;border-radius:50%;background:${modeColor(slot.mode)}"></span>
          <span class="mono" style="font-weight:650">${escapeHtml(slot.time)}</span>
          <span class="tiny muted">${weekdayShort(todayIndex())}</span>
        </span>
        <span class="tiny" style="font-weight:600">${escapeHtml(describeSlot(slot, store.meta))}</span>
      </div>`
    )
    .join("");
}
