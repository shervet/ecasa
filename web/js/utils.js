// Petits utilitaires partagés : DOM, dates, formats, couleurs.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export const clamp = (value, min, max) => Math.max(min, Math.min(max, Number(value)));

export function debounce(fn, delay = 300) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

/* --------------------------------------------------------------- températures */

export function fmtTemp(value, digits = 1) {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) {
    return "—";
  }
  const rounded = Number(value).toFixed(digits);
  return rounded.replace(/\.0+$/, "").replace(".", ",");
}

export function fmtNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPercent(value) {
  if (value === null || value === undefined) return "—";
  return `${fmtNumber(value, 0)} %`;
}

/* --------------------------------------------------------------- dates */

const WEEKDAYS_SHORT = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."];
const WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];

export const pad2 = (n) => String(n).padStart(2, "0");

export function parseDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  const text = String(value);
  // "2026-11-06" ou ISO complet
  const date = new Date(text.length === 10 ? `${text}T12:00:00` : text);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function isoDay(date = new Date()) {
  const d = parseDate(date) || new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

export const todayIso = () => isoDay(new Date());

export function addDays(value, days) {
  const date = parseDate(value) || new Date();
  const copy = new Date(date.getTime());
  copy.setDate(copy.getDate() + days);
  return copy;
}

export function mondayOf(value = new Date()) {
  const date = parseDate(value) || new Date();
  const weekday = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - weekday);
  return isoDay(date);
}

export function weekDates(mondayIso) {
  const monday = parseDate(mondayIso) || new Date();
  return Array.from({ length: 7 }, (_, i) => isoDay(addDays(monday, i)));
}

export function fmtDate(value, options = {}) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "short",
    ...options,
  });
}

export function fmtDateLong(value) {
  return fmtDate(value, { weekday: "long", day: "numeric", month: "long" });
}

export function fmtTime(value, withSeconds = false) {
  if (!value) return "—";
  const date = value instanceof Date ? value : parseDate(value);
  if (!date) return "—";
  return date.toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
  });
}

export function fmtDateTime(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return `${fmtDate(date, { day: "numeric", month: "short" })} · ${fmtTime(date)}`;
}

export function weekdayShort(index) {
  return WEEKDAYS_SHORT[((index % 7) + 7) % 7];
}

export function weekdayLong(index) {
  return WEEKDAYS[((index % 7) + 7) % 7];
}

export const WEEKDAY_NAMES = WEEKDAYS;
export const WEEKDAY_SHORT = WEEKDAYS_SHORT;

export function todayIndex() {
  return (new Date().getDay() + 6) % 7;
}

export function minutesToTime(minutes) {
  const value = Math.max(0, Math.min(24 * 60 - 1, Math.round(Number(minutes) || 0)));
  return `${pad2(Math.floor(value / 60))}:${pad2(value % 60)}`;
}

export function timeToMinutes(value) {
  const [hours, minutes] = String(value || "00:00").split(":").map(Number);
  return (Number.isFinite(hours) ? hours : 0) * 60 + (Number.isFinite(minutes) ? minutes : 0);
}

export function inWords(minutes) {
  const value = Math.max(0, Math.round(Number(minutes) || 0));
  if (value < 1) return "maintenant";
  if (value < 60) return `dans ${value} min`;
  const hours = Math.floor(value / 60);
  const rest = value % 60;
  if (rest === 0) return `dans ${hours} h`;
  return `dans ${hours} h ${pad2(rest)}`;
}

export function durationLabel(minutes) {
  const value = Math.max(0, Math.round(Number(minutes) || 0));
  if (value < 60) return `${value} min`;
  const hours = Math.floor(value / 60);
  const rest = value % 60;
  return rest ? `${hours} h ${pad2(rest)}` : `${hours} h`;
}

/* --------------------------------------------------------------- modes */

export const FALLBACK_MODES = [
  ["off", "Arrêt"],
  ["cool", "Froid"],
  ["heat", "Chaud"],
  ["dry", "Déshumidification"],
  ["fan", "Ventilation"],
  ["auto", "Auto"],
];

export function modesList(meta) {
  const modes = meta?.modes;
  if (Array.isArray(modes) && modes.length) {
    return modes.map((item) => (Array.isArray(item) ? item : [item.mode, item.label]));
  }
  return FALLBACK_MODES;
}

export function modeLabel(mode, meta) {
  if (!mode) return "—";
  const found = modesList(meta).find(([key]) => key === mode);
  return found ? found[1] : mode;
}

/**
 * Modes réellement applicables à un appareil.
 *
 * - Wiser : « Arrêt » (hors gel) et « Chauffage » uniquement.
 * - Atlantic : tous les modes, si l'appareil expose la consigne de froid
 *   (`cap_setpoint_cool`) ; sans elle, ni « Froid » ni « Déshumidification ».
 */
export function deviceModes(device) {
  if (!device) return [];
  if (device.device_type === "wiser") return ["off", "heat"];
  const cool = Number(device.cap_setpoint_cool || 0) > 0;
  return cool
    ? ["off", "cool", "heat", "dry", "fan", "auto"]
    : ["off", "heat"];
}

/**
 * Modes disponibles pour une cible (pièce ou appareil) : union des modes de
 * ses appareils. Une pièce équipée d'une climatisation propose donc « Froid »
 * même si son actionnaire est un simple radiateur Wiser.
 */
export function targetModes(target) {
  const devices = target?.devices?.length
    ? target.devices
    : (target?.device ? [target.device] : []);
  const modes = new Set();
  for (const device of devices) {
    for (const mode of deviceModes(device)) modes.add(mode);
  }
  return [...modes];
}

/** Appareil de la cible capable d'appliquer un mode donné. */
export function deviceForMode(target, mode) {
  const devices = target?.devices?.length
    ? target.devices
    : (target?.device ? [target.device] : []);
  return devices.find((device) => deviceModes(device).includes(mode)) || null;
}

export const MODE_COLORS = {
  off: "var(--faint)",
  auto: "var(--ok)",
  cool: "var(--cool)",
  heat: "var(--warm)",
  dry: "var(--primary)",
  fan: "var(--muted)",
};

export function modeColor(mode) {
  return MODE_COLORS[mode] || "var(--primary)";
}

export function describeSlot(slot, meta) {
  if (!slot) return "Aucun réglage";
  const label = modeLabel(slot.mode, meta);
  if (slot.mode === "off" || slot.mode === "fan") return label;
  return `${label} ${fmtTemp(slot.temp)} °C`;
}

/* --------------------------------------------------------------- stockage local */

export function readPref(key, fallback = null) {
  try {
    const raw = localStorage.getItem(`mecasa.${key}`);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writePref(key, value) {
  try {
    localStorage.setItem(`mecasa.${key}`, JSON.stringify(value));
  } catch {
    /* stockage plein ou navigation privée : on ignore */
  }
}

/* --------------------------------------------------------------- divers */

export function greeting(date = new Date()) {
  const hour = date.getHours();
  if (hour < 6) return "Bonne nuit";
  if (hour < 12) return "Bonjour";
  if (hour < 18) return "Bon après-midi";
  return "Bonsoir";
}

export function bytes(value) {
  const size = Number(value) || 0;
  if (size < 1024) return `${size} o`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} Ko`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} Mo`;
  return `${(size / 1024 ** 3).toFixed(2)} Go`;
}

export function uptime(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  const days = Math.floor(value / 86400);
  const hours = Math.floor((value % 86400) / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (days) return `${days} j ${hours} h`;
  if (hours) return `${hours} h ${pad2(minutes)}`;
  return `${minutes} min`;
}

export function sortByPosition(items = []) {
  return [...items].sort((a, b) => {
    const pa = Number(a.position ?? 0);
    const pb = Number(b.position ?? 0);
    if (pa !== pb) return pa - pb;
    return Number(a.id ?? a.device_id ?? 0) - Number(b.id ?? b.device_id ?? 0);
  });
}

export function safeId(value) {
  return String(value ?? "").replace(/[^a-zA-Z0-9_-]/g, "");
}

export function isMock(state) {
  return Boolean(state?.status?.mock);
}

export function deviceName(state, deviceId) {
  return state?.devices?.find((d) => Number(d.device_id) === Number(deviceId))?.name || `Appareil ${deviceId}`;
}


export function deviceTempRange(device) {
  const isWiser = device?.device_type === 'wiser';
  const fallbackMin = isWiser ? 0 : 16;
  const fallbackMax = 30;
  const min = Number(device?.temp_min);
  const max = Number(device?.temp_max);
  const resolvedMin = Number.isFinite(min) ? min : fallbackMin;
  const resolvedMax = Number.isFinite(max) ? max : fallbackMax;
  return resolvedMin <= resolvedMax
    ? { min: resolvedMin, max: resolvedMax }
    : { min: resolvedMax, max: resolvedMin };
}
