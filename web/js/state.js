// État global de l'application : snapshot moteur, méta, config, système.

import { get, post } from "./api.js";
import { toast, toastError } from "./toast.js";
import { readPref, writePref } from "./utils.js";

export const store = {
  state: null,
  meta: null,
  config: null,
  system: null,
  connected: false,
  error: null,
  loaded: false,
  lastSync: 0,
  prefs: {
    theme: readPref("theme", "auto"),
    target: readPref("target", null),
    week: readPref("week", null),
    day: readPref("day", null),
    showCpuLoad: readPref("showCpuLoad", true),
    showCpuTemp: readPref("showCpuTemp", true),
    showDiskUsage: readPref("showDiskUsage", true),
  },
};

const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function notify(reason = "state") {
  emit(reason);
}

function emit(reason) {
  for (const fn of [...listeners]) {
    try {
      fn(store, reason);
    } catch (err) {
      console.error("[ecasa] listener", err);
    }
  }
}

export function setPref(key, value) {
  store.prefs[key] = value;
  writePref(key, value);
  emit(`pref:${key}`);
}

/* --------------------------------------------------------------- chargements */

let stateInFlight = null;

export async function refreshState({ silent = true } = {}) {
  if (stateInFlight) return stateInFlight;

  stateInFlight = (async () => {
    try {
      const snapshot = await get("/api/state");
      store.state = snapshot;
      store.connected = true;
      store.error = null;
      store.loaded = true;
      store.lastSync = Date.now();
      emit("state");
      return snapshot;
    } catch (err) {
      store.connected = false;
      store.error = err.message;
      emit("state");
      if (!silent) toastError(err);
      return null;
    } finally {
      stateInFlight = null;
    }
  })();

  return stateInFlight;
}

export async function refreshSystem({ silent = true } = {}) {
  try {
    store.system = await get("/api/system");
    emit("system");
    return store.system;
  } catch (err) {
    if (!silent) toastError(err);
    return null;
  }
}

export async function refreshConfig({ silent = true } = {}) {
  try {
    store.config = await get("/api/config");
    emit("config");
    return store.config;
  } catch (err) {
    if (!silent) toastError(err);
    return null;
  }
}

export async function loadMeta() {
  try {
    store.meta = await get("/api/meta");
    emit("meta");
  } catch {
    /* le serveur renverra les libellés par défaut */
  }
}

/* --------------------------------------------------------------- actions */

export async function runAction(action, { success = "", silent = false } = {}) {
  try {
    const result = await action();
    if (success) toast(success);
    await refreshState({ silent: true });
    return result;
  } catch (err) {
    if (!silent) toastError(err);
    throw err;
  }
}

export async function applyNow() {
  return runAction(() => post("/api/apply"), {
    success: "Consignes appliquées.",
  });
}

export async function syncDevices() {
  const snapshot = await runAction(() => post("/api/sync"), {
    success: "Appareils synchronisés.",
  });
  if (snapshot) {
    store.state = snapshot;
    emit("state");
  }
  return snapshot;
}

/* --------------------------------------------------------------- polling */

let stateTimer = null;
let systemTimer = null;
let pollPaused = false;

function schedule() {
  clearInterval(stateTimer);
  clearInterval(systemTimer);
  if (pollPaused) return;

  stateTimer = setInterval(() => {
    if (document.visibilityState === "visible") refreshState();
  }, 15000);

  systemTimer = setInterval(() => {
    if (document.visibilityState === "visible") refreshSystem();
  }, 60000);
}

export function startPolling() {
  pollPaused = false;
  schedule();
  document.addEventListener("visibilitychange", onVisibility);
}

export function stopPolling() {
  pollPaused = true;
  clearInterval(stateTimer);
  clearInterval(systemTimer);
  document.removeEventListener("visibilitychange", onVisibility);
}

function onVisibility() {
  if (document.visibilityState === "visible") {
    refreshState();
    refreshSystem();
  }
}

export async function bootstrap() {
  await loadMeta();
  await refreshState({ silent: true });
  await refreshConfig({ silent: true });
  await refreshSystem({ silent: true });
  startPolling();
}

/* --------------------------------------------------------------- dérivés */

export function devicesInRoom(state, roomId) {
  return (state?.devices || []).filter((device) => Number(device.room_id) === Number(roomId));
}

export function primaryDevice(state, room) {
  const devices = devicesInRoom(state, room.id);
  if (!devices.length) return null;
  const actuator = devices.find((d) => Number(d.device_id) === Number(room.actuator_device_id));
  if (actuator) return actuator;
  const thermostat = devices.find((d) => Number(d.device_id) === Number(room.thermostat_device_id));
  return thermostat || devices[0];
}

export function roomSummary(state, room) {
  const devices = devicesInRoom(state, room.id);
  if (!devices.length) return null;

  const primary = primaryDevice(state, room);
  const ambients = devices
    .map((d) => Number(d.current?.ambient ?? d.current?.wiser_ambient))
    .filter((value) => Number.isFinite(value) && value > -50);
  const targets = devices
    .map((d) => (d.desired ? Number(d.desired.temp) : null))
    .filter((value) => Number.isFinite(value));

  // Humidité relative : uniquement les appareils qui en exposent une (Wiser).
  const humidities = devices
    .map((d) => Number(d.current?.humidity ?? d.current?.wiser_humidity))
    .filter((value) => Number.isFinite(value) && value >= 0 && value <= 100);

  const ambient = ambients.length
    ? ambients.reduce((sum, value) => sum + value, 0) / ambients.length
    : null;
  const humidity = humidities.length
    ? humidities.reduce((sum, value) => sum + value, 0) / humidities.length
    : null;
  const target = targets.length ? targets[targets.length - 1] : null;

  return {
    devices,
    primary,
    ambient,
    humidity,
    target,
    mode: primary?.desired?.mode || primary?.current?.mode || null,
    source: primary?.source || null,
    until: primary?.until || null,
    next: primary?.next || null,
    override: devices.some((d) => d.source === "override"),
  };
}
