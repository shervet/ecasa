// Réglages : apparence, moteur, semaines A/B, pièces, appareils et connecteurs.

import { del, get, post, put } from "../api.js";
import { icon } from "../icons.js";
import { closeSheet, openSheet } from "../sheet.js";
import { SITE } from "../site.js";
import {
  refreshConfig,
  refreshState,
  refreshSystem,
  setPref,
  store,
} from "../state.js";
import { toast, toastError } from "../toast.js";
import {
  addDays,
  bytes,
  escapeHtml,
  fmtDate,
  fmtDateTime,
  fmtTemp,
  modeLabel,
  minutesToTime,
  mondayOf,
  parseDate,
  uptime,
  weekdayLong,
} from "../utils.js";

const THEMES = [
  ["auto", "Automatique", "suit le téléphone"],
  ["light", "Clair", "lumineux"],
  ["dark", "Sombre", "le soir"],
];

const STUDENT_STATUSES = [
  ["externe", "Externe (rentre déjeuner)"],
  ["demi_pensionnaire", "Demi-pensionnaire (reste au lycée)"],
];

let root = null;
let weeks = {};
let tailscale = null;
let updateInfo = null;
let pronoteResults = null;

/* --------------------------------------------------------------- helpers */

const cfg = () => store.config || {};

function toggle(key, checked, label = "") {
  return `<label class="switch"${label ? ` title="${escapeHtml(label)}"` : ""}>
    <input type="checkbox" data-key="${key}"${checked ? " checked" : ""}>
    <span class="track"></span>
  </label>`;
}

/** Interrupteur d'un appareil : icône puissance + switch (comme « Moteur actif »). */
function deviceSwitch(attrs, isOn, title) {
  return `<span class="device-power${isOn ? " is-on" : ""}" aria-hidden="true">${icon("power", 14)}</span>
    <label class="switch" title="${title}">
      <input type="checkbox" ${attrs}${isOn ? " checked" : ""}>
      <span class="track"></span>
    </label>`;
}

/**
 * Interrupteur placé dans un <summary> : le clic ne doit ni replier le panneau
 * ni laisser le navigateur double-basculer la case.
 */
function bindSummarySwitch(input) {
  const label = input.closest("label.switch");
  if (!label || label.dataset.summaryBound) return;
  label.dataset.summaryBound = "1";
  label.addEventListener("click", (event) => {
    if (event.target === input) {
      event.stopPropagation();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    input.checked = !input.checked;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function row(title, sub, control, stack = false, extraClass = "") {
  return `<div class="settings-row${stack ? " is-stack" : ""}${extraClass ? ` ${extraClass}` : ""}">
    <div class="sr-main">
      <div class="sr-title">${title}</div>
      ${sub ? `<div class="sr-sub">${sub}</div>` : ""}
    </div>
    <div class="sr-control">${control}</div>
  </div>`;
}

function textRow(title, key, value, { sub = "", type = "text", placeholder = "", attrs = "" } = {}) {
  return row(
    title,
    sub,
    `<input class="input" data-key="${key}" type="${type}" value="${escapeHtml(value ?? "")}" placeholder="${escapeHtml(placeholder)}" ${attrs} style="width:min(320px,62vw)">`,
    true
  );
}

function numberRow(title, key, value, { sub = "", min = "", max = "", step = "1" } = {}) {
  return row(
    title,
    sub,
    `<input class="input" data-key="${key}" data-number type="number" value="${escapeHtml(value ?? "")}" min="${min}" max="${max}" step="${step}" style="width:132px;text-align:right">`
  );
}

function selectRow(title, key, value, options, { sub = "" } = {}) {
  return row(
    title,
    sub,
    `<select class="select" data-key="${key}" style="width:min(320px,62vw)">
      ${options
        .map(
          ([optionValue, label]) =>
            `<option value="${escapeHtml(optionValue)}"${String(optionValue) === String(value) ? " selected" : ""}>${escapeHtml(label)}</option>`
        )
        .join("")}
    </select>`,
    true
  );
}

function section(id, title, iconName, body, footer = "") {
  return `<section class="settings-section card" id="sec-${id}">
    <div class="card-head">
      <div class="card-title">${icon(iconName, 18)} ${title}</div>
    </div>
    <div class="card-body">${body}</div>
    ${footer ? `<div class="card-foot">${footer}</div>` : ""}
  </section>`;
}

function collect(container) {
  const body = {};
  container.querySelectorAll("[data-key]").forEach((node) => {
    const key = node.dataset.key;
    if (node.type === "checkbox") {
      body[key] = node.checked;
      return;
    }
    if (node.dataset.secret && node.value === "") return;
    if ("number" in node.dataset) {
      body[key] = node.value === "" ? null : Number(node.value);
    } else {
      body[key] = node.value;
    }
  });
  return body;
}

function modeOptions(extra = []) {
  return [
    ...extra,
    ...(store.meta?.modes?.length
      ? store.meta.modes.map((item) => (Array.isArray(item) ? item : [item.mode, item.label]))
      : [["heat", "Chaud"], ["off", "Arrêt"], ["cool", "Froid"]]),
  ];
}

/* --------------------------------------------------------------- montage */

export default {
  title: "Réglages",

  async mount(view) {
    root = view;
    window.addEventListener("ecasa:jump", onWindowJump);
    if (!store.config) await refreshConfig({ silent: true });

    const navItems = [
      ["appearance", "Apparence"],
      ["weeks", "Semaines A/B"],
      ["school", "Calendrier scolaire"],
      ["rooms", "Pièces"],
      ["weather", "Météo"],
      ["devices", "Appareils"],
      /* Clim fusionné dans Appareils : plus de section séparée. */
      ["pronote", "Pronote"],
      ["server", "Serveur"],
      ["energy", "Électricité"],
      ["support", "Soutenir"],
    ];

    view.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div>
            <h1 class="page-title">Réglages</h1>
            <p class="page-sub">Tout se configure ici, sans ligne de commande.</p>
          </div>
          <div class="page-actions">
            <button class="btn" type="button" data-stop>${icon("power", 17)} Arrêter</button>
            <button class="btn" type="button" data-restart>${icon("rotate", 17)} Redémarrer</button>
          </div>
        </div>

        <div class="card card-pad" id="restartBanner" hidden style="border-color:color-mix(in srgb, var(--warn) 40%, var(--line));background:var(--warn-soft)">
          <div class="row-between wrap" style="gap:10px">
            <div>
              <strong>Redémarrage nécessaire</strong>
              <p class="hint mt-1">Certains réglages (compte, réseau, stockage) ne s'appliquent qu'au prochain démarrage.</p>
            </div>
            <button class="btn btn-primary" type="button" data-restart>${icon("rotate", 17)} Redémarrer maintenant</button>
          </div>
        </div>

        <div class="settings-nav" id="settingsNav">
          ${navItems
            .map(([id, label]) => `<a href="#/settings" data-jump="${id}">${label}</a>`)
            .join("")}
        </div>

        <div class="settings-layout">
          ${appearanceSection()}
          ${weeksSection()}
          ${schoolSection()}
          ${roomsSection()}
          ${weatherSection()}
          ${devicesSection()}
          ${pronoteSection()}
          ${serverSection()}
          ${energySection()}
          ${supportSection()}
          <p class="hint">
            eCasa ${escapeHtml(store.system?.app?.version || "")} · les secrets ne sont jamais renvoyés à l'interface.
          </p>
        </div>
      </div>
    `;

    bind();
    renderRooms();
    renderDevices();
    renderStorage();
    renderSystem();
    await loadWeeks();
    loadSchoolCalendar();
    await loadTailscale();
    await loadUpdate();

    // Lien profond depuis le Planning : #/settings/pronote ouvre la section.
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    if (parts[0] === "settings" && parts[1]) {
      const target = parts[1];
      // Défilement instantané : la mise en page n'est pas encore stable et un
      // défilement animé repartirait de zéro.
      requestAnimationFrame(() => jumpToSection(target, true));
      // Les sections se chargent de façon asynchrone (pièces, appareils) :
      // on recommence une fois la mise en page stabilisée.
      window.setTimeout(() => jumpToSection(target, true), 500);
    }
  },

  unmount() {
    window.removeEventListener("ecasa:jump", onWindowJump);
    closeSheet();
    root = null;
  },

  onState() {
    if (!root) return;
    const active = document.activeElement;
    // Ne pas réécrire la page pendant une saisie de texte, mais laisser passer
    // les interrupteurs (sinon le grisage ne se rafraîchit jamais).
    const tag = (active?.tagName || "").toLowerCase();
    const type = (active?.getAttribute?.("type") || "").toLowerCase();
    const typing =
      tag === "textarea" ||
      tag === "select" ||
      (tag === "input" &&
        !["checkbox", "radio", "range", "button", "submit"].includes(type));
    if (active && root.contains(active) && typing) return;
    renderRooms();
    renderDevices();
    renderStorage();
    paintEngineState();
  },
};

/* --------------------------------------------------------------- sections */

function appearanceSection() {
  const theme = store.prefs.theme || "auto";
  return section(
    "appearance",
    "Apparence",
    "sun",
    `${row(
      "Thème",
      "Le thème automatique suit le réglage clair/sombre du téléphone.",
      `<div class="segmented" id="themePicker">
        ${THEMES.map(
          ([key, label, sub]) =>
            `<button type="button" data-theme="${key}" class="${key === theme ? "is-active" : ""}" title="${escapeHtml(sub)}">${label}</button>`
        ).join("")}
      </div>`
    )}
    ${row(
      "Installer sur l'écran d'accueil",
      "eCasa s'ajoute comme une application, sans passer par un store.",
      `<button class="btn btn-sm" type="button" id="installHelp">${icon("install", 15)} Comment faire</button>`
    )}
    ${row(
      "Mouvements réduits",
      "Respecte automatiquement le réglage d'accessibilité du système.",
      `<span class="pill pill-ghost">automatique</span>`
    )}
    ${row(
      "Graphique charge CPU",
      "Affiche la charge CPU sur 60 minutes sur l'accueil (info serveur, non sauvegardée).",
      `<label class="switch" title="Afficher la charge CPU"><input type="checkbox" data-pref="showCpuLoad" ${store.prefs?.showCpuLoad !== false ? "checked" : ""}><span class="track"></span></label>`
    )}
    ${row(
      "Graphique température CPU",
      "Affiche la température CPU sur 60 minutes sur l'accueil (info serveur, non sauvegardée).",
      `<label class="switch" title="Afficher la température CPU"><input type="checkbox" data-pref="showCpuTemp" ${store.prefs?.showCpuTemp !== false ? "checked" : ""}><span class="track"></span></label>`
    )}
    ${row(
      "Diagramme utilisation disque",
      "Affiche l'utilisation du disque principal sur l'accueil (système, applications, libre).",
      `<label class="switch" title="Afficher l'utilisation du disque"><input type="checkbox" data-pref="showDiskUsage" ${store.prefs?.showDiskUsage !== false ? "checked" : ""}><span class="track"></span></label>`
    )}`
  );
}

function engineSection() {
  const settings = store.state?.settings || {};
  const enabled = String(settings.engine_enabled ?? "1") !== "0";
  const interval = Number(settings.poll_interval || 60);
  return section(
    "engine",
    "Moteur de planning",
    "gauge",
    `${row(
      "Moteur actif",
      "Quand il est en pause, eCasa ne change plus les consignes tout seul.",
      toggle("engine_enabled", enabled)
    )}
    ${selectRow(
      "Fréquence de lecture",
      "poll_interval",
      String(interval),
      [
        ["30", "Toutes les 30 s"],
        ["60", "Toutes les minutes"],
        ["120", "Toutes les 2 minutes"],
        ["300", "Toutes les 5 minutes"],
        ["600", "Toutes les 10 minutes"],
      ],
      { sub: "Plus la lecture est fréquente, plus le cloud Atlantic est sollicité." }
    )}
    ${numberRow(
      "Avance maximale du préchauffage",
      "preheat_max_minutes",
      String(settings.preheat_max_minutes ?? "120"),
      {
        sub: "Plafond d'anticipation des montées de consigne, en minutes (0 à 360). 0 désactive le préchauffage.",
        min: "0",
        max: "360",
        step: "5",
      }
    )}
    <div class="row wrap" style="gap:8px">
      <span class="pill ${store.state?.status?.mock ? "pill-warn" : "pill-ok"}" id="engineMode">${store.state?.status?.mock ? "Simulation" : "Réel"}</span>
      <span class="pill pill-ghost" id="engineState">${enabled ? "Moteur actif" : "En pause"}</span>
      <span class="pill pill-ghost">Dernière lecture ${store.state?.status?.last_poll ? fmtDateTime(store.state.status.last_poll) : "—"}</span>
    </div>
    ${store.state?.status?.last_error ? `<div class="hint is-bad">${escapeHtml(store.state.status.last_error)}</div>` : ""}`,
    `<button class="btn" type="button" data-sync>${icon("refresh", 16)} Synchroniser les appareils</button>
     <button class="btn" type="button" data-apply>${icon("zap", 16)} Appliquer maintenant</button>`
  );
}

function weeksSection() {
  const settings = store.state?.settings || {};
  const mode = settings.week_mode || "parity";
  const flip = String(settings.week_flip || "0") === "1";
  const defaultWeek = settings.default_week || "A";
  return section(
    "weeks",
    "Semaines A/B",
    "calendar",
    `${selectRow(
      "Alternance",
      "week_mode",
      mode,
      [
        ["parity", "Automatique (parité ISO)"],
        ["none", "Pas d'alternance"],
      ],
      {
        sub: "La parité ISO alterne A et B une semaine sur deux. En mode « Pas d'alternance », toutes les semaines sont traitées comme la semaine A.",
      }
    )}
    <div class="hint">Le jour d'alternance (garde) se règle dans la section Pronote.</div>
    ${row(
      "Inverser Semaine A et B",
      "Permute A et B par rapport à la parité ISO.",
      toggle("week_flip", flip)
    )}
    <div class="row-between wrap" style="gap:10px">
      <div class="tiny muted">
        Semaine en cours : <strong style="color:var(--text)">${escapeHtml(store.state?.week || "—")}</strong>
        ${store.state?.iso_week ? ` · ISO ${store.state.iso_week}` : ""}
      </div>
    </div>`
  );
}

function schoolSection() {
  const c = cfg();
  const zone = String(c.school_zone || "");
  const enabled = c.school_holidays_enabled === true;
  const plan = String(c.school_holiday_plan || "comfort");
  const weeks = String(c.school_holiday_weeks || "both");
  return section(
    "school",
    "Calendrier scolaire",
    "calendar",
    `${selectRow(
      "Zone scolaire",
      "school_zone",
      zone,
      [
        ["", "Désactivé"],
        ["A", "Zone A"],
        ["B", "Zone B"],
        ["C", "Zone C"],
      ],
      {
        sub: "Les vacances officielles sont téléchargées depuis data.education.gouv.fr puis mises en cache.",
      }
    )}
    ${row(
      "Suivre les vacances et jours fériés",
      "Remplace le planning Pronote par une journée à la maison pendant les fermetures.",
      toggle("school_holidays_enabled", enabled)
    )}
    ${selectRow(
      "Planning des vacances",
      "school_holiday_plan",
      plan,
      [
        ["comfort", "Confort (journée à la maison)"],
        ["reduced", "Réduit"],
        ["absent", "Hors gel"],
        ["skip", "Ne pas modifier"],
      ],
      {
        sub: "Appliqué aux jours de vacances et aux jours fériés à la place du planning de l'école.",
      }
    )}
    ${selectRow(
      "Semaines avec l'enfant",
      "school_holiday_weeks",
      weeks,
      [
        ["both", "Toutes les semaines"],
        ["A", "Semaine A seulement"],
        ["B", "Semaine B seulement"],
        ["none", "Aucune"],
      ],
      {
        sub: "En garde alternée, seules les semaines où l'enfant est présent sont ajustées. Les autres conservent leur planning Pronote.",
      }
    )}
    <div class="row-between wrap" style="gap:10px">
      <span class="hint" id="schoolNext">Prochaine fermeture : —</span>
      <button class="btn" type="button" data-school-refresh>${icon("refresh", 16)} Actualiser le calendrier</button>
    </div>`
  );
}

async function loadSchoolCalendar() {
  if (!root) return;
  const node = root.querySelector("#schoolNext");
  if (!node) return;
  const zone = String(
    root.querySelector('[data-key="school_zone"]')?.value || cfg().school_zone || ""
  );
  if (!zone) {
    node.textContent = "Choisis une zone pour importer les vacances.";
    return;
  }
  try {
    const payload = await get(
      `/api/school-calendar?zone=${encodeURIComponent(zone)}`
    );
    const next = payload?.next;
    node.textContent = next
      ? `Prochaine fermeture : ${next.name} le ${fmtDate(next.date)}`
      : "Aucune fermeture à venir dans le calendrier.";
  } catch {
    node.textContent = "Calendrier indisponible : le cache local reste utilisé.";
  }
}

function roomsSection() {
  return section(
    "rooms",
    "Pièces",
    "house",
    `<p class="hint">Une pièce regroupe un thermostat et un actionneur. Le planning s'applique à la pièce entière.</p>
     <div class="stack" id="roomsList"></div>`,
    `<button class="btn btn-primary" type="button" id="addRoom">${icon("plus", 16)} Ajouter une pièce</button>`
  );
}

/**
 * Panneaux dépliables des autres systèmes (Daikin, Bosch, Netatmo, Mitsubishi).
 * Le catalogue vient de `GET /api/meta` : ajouter un connecteur côté backend
 * suffit pour qu'il apparaisse ici.
 */
function connectorPanels(c) {
  const catalog = Array.isArray(store.meta?.connectors) ? store.meta.connectors : [];
  if (!catalog.length) return "";
  return catalog
    .map((item) => {
      const values = c.connectors?.[item.id] || {};
      const enabled = String(values.enabled ?? "1") !== "0";
      const fields = (item.fields || [])
        .filter((field) => !field.hidden)
        .map((field) => {
          const key = `${item.id}:${field.key}`;
          const hint = field.placeholder ? `Exemple : ${escapeHtml(field.placeholder)}` : "";
          if (field.secret) {
            const set = Boolean(values[`${field.key}_set`]);
            return row(
              escapeHtml(field.label),
              set ? "Une valeur est enregistrée. Laisse vide pour la conserver." : hint,
              `<input class="input" data-secret type="password" autocomplete="new-password" placeholder="${set ? "••••••••" : escapeHtml(field.placeholder || "")}" data-connector="${key}" style="width:min(320px,62vw)">`,
              true
            );
          }
          return row(
            escapeHtml(field.label),
            hint,
            `<input class="input" value="${escapeHtml(values[field.key] || "")}" placeholder="${escapeHtml(field.placeholder || "")}" data-connector="${key}" style="width:min(320px,62vw)">`,
            true
          );
        })
        .join("");
      // Le badge ne signale qu'un état transitoire : un connecteur « ready »
      // fonctionne, il n'a donc rien à afficher.
      const badgeLabel = item.status === "beta" ? "bêta" : item.status === "pairing" ? "à venir" : "";
      const badgeClass = item.status === "beta" ? "pill pill-warn" : "pill pill-ghost";
      const badge = badgeLabel
        ? ` <span class="${badgeClass}" style="margin-left:6px">${badgeLabel}</span>`
        : "";
      return `<details class="stack brand-panel" style="margin-top:18px">
        <summary class="card-title summary-row" style="cursor:pointer">${icon("hub", 16)} ${escapeHtml(item.label)}${badge}<span class="summary-switch">${deviceSwitch(`data-connector-enabled="${escapeHtml(item.id)}"`, enabled, enabled ? "Désactiver ce système" : "Activer ce système")}</span></summary>
        <p class="hint" style="margin-top:8px">${escapeHtml(item.description || "")}</p>
        ${fields}
        <div class="row wrap" style="gap:8px;margin-top:4px">
          <button class="btn" type="button" data-connector-sync="${escapeHtml(item.id)}">${icon("refresh", 16)} Synchroniser</button>
          <button class="btn" type="button" data-connector-test="${escapeHtml(item.id)}">${icon("shield", 16)} Tester</button>
        </div>
        <div class="hint" data-connector-result="${escapeHtml(item.id)}"></div>
        <div class="stack" data-connector-devices="${escapeHtml(item.id)}" style="margin-top:8px"></div>
      </details>`;
    })
    .join("");
}

/** Ligne d'un appareil découvert sur un connecteur (interrupteur + mode + consigne). */
function connectorDeviceRow(connectorId, device) {
  const modes = ["off", "heat", "cool", "dry", "fan", "auto"];
  const currentMode = modes.includes(device.mode) ? device.mode : "heat";
  const isOn = device.enabled !== false;
  return `<div class="settings-row${isOn ? "" : " is-off-row"}" data-device-row>
    <div class="sr-main">
      <div class="sr-title${isOn ? "" : " is-off"}">${escapeHtml(device.name || "Appareil")}</div>
      <div class="sr-sub" style="display:block">${escapeHtml(modeLabel(device.mode, store.meta))} · ${fmtTemp(device.temp)} °C${
        device.ambient != null ? ` · ambiance ${fmtTemp(device.ambient)} °C` : ""
      }${device.online === false ? " · hors ligne" : ""}${isOn ? "" : " · pilotage désactivé"}</div>
    </div>
    <div class="sr-control">
      <select class="select" data-device-mode style="width:auto"${isOn ? "" : " disabled"}>
        ${modes
          .map(
            (mode) => `<option value="${mode}"${mode === currentMode ? " selected" : ""}>${escapeHtml(modeLabel(mode, store.meta))}</option>`
          )
          .join("")}
      </select>
      <input class="input" type="number" min="5" max="30" step="0.5" style="width:90px" data-device-temp value="${fmtTemp(device.temp)}"${isOn ? "" : " disabled"}>
      <button class="btn btn-sm" type="button" data-device-apply data-device-id="${escapeHtml(String(device.id))}"${isOn ? "" : " disabled"}>${icon("check", 14)} Appliquer</button>
    </div>
  </div>`;
}

/** Enregistre les réglages d'un connecteur (champs non secrets + internes). */
async function saveConnectorSettings(connectorId, overrides = {}) {
  const current = (cfg().connectors || {})[connectorId] || {};
  const item = (store.meta?.connectors || []).find((entry) => entry.id === connectorId);
  const body = {};
  for (const field of item?.fields || []) {
    if (field.secret) continue;
    body[field.key] = current[field.key] ?? "";
  }
  Object.assign(body, overrides);
  const payload = await put("/api/config", {
    connectors: { [connectorId]: body },
  });
  store.config = payload.config;
  return payload;
}

/** Active / désactive toute la marque ou le modèle (interrupteur du résumé). */
async function setConnectorEnabled(connectorId, enabled) {
  return saveConnectorSettings(connectorId, { enabled: enabled ? "1" : "0" });
}

/** Active/désactive le pilotage d'un appareil d'un connecteur (persisté en config). */
async function setConnectorDeviceEnabled(connectorId, deviceId, enabled) {
  const current = (cfg().connectors || {})[connectorId] || {};
  const ids = new Set(
    String(current.disabled_devices || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
  );
  if (enabled) ids.delete(String(deviceId));
  else ids.add(String(deviceId));
  return saveConnectorSettings(connectorId, { disabled_devices: [...ids].join(",") });
}

/** Lit les appareils d'un connecteur et branche les boutons « Appliquer ». */
async function loadConnectorDevices(connectorId, button = null) {
  const host = root?.querySelector(`[data-connector-devices="${connectorId}"]`);
  if (!host) return;
  if (button) button.disabled = true;
  host.innerHTML = '<p class="hint">Lecture des appareils…</p>';
  try {
    const payload = await get(`/api/connectors/${encodeURIComponent(connectorId)}/devices`);
    const devices = Array.isArray(payload.devices) ? payload.devices : [];
    host.innerHTML = devices.length
      ? devices.map((device) => connectorDeviceRow(connectorId, device)).join("")
      : '<p class="hint">Aucun appareil détecté sur ce connecteur.</p>';
    host.querySelectorAll("[data-device-apply]").forEach((apply) => {
      apply.addEventListener("click", async () => {
        const row = apply.closest("[data-device-row]");
        const mode = row?.querySelector("[data-device-mode]")?.value || "off";
        const temp = Number(row?.querySelector("[data-device-temp]")?.value || 20);
        apply.disabled = true;
        try {
          await post(`/api/connectors/${encodeURIComponent(connectorId)}/apply`, {
            device_id: apply.dataset.deviceId,
            mode,
            temp,
          });
          toast("Consigne envoyée au connecteur.");
          await loadConnectorDevices(connectorId);
        } catch (err) {
          toastError(err);
        } finally {
          apply.disabled = false;
        }
      });
    });
  } catch (err) {
    host.innerHTML = `<p class="hint is-bad">${escapeHtml(err.message)}</p>`;
  } finally {
    if (button) button.disabled = false;
  }
}

function devicesSection() {
  const cfg_ = cfg();
  return section(
    "devices",
    "Appareils",
    "thermometer",
    `<p class="hint">Les appareils sont découverts depuis Atlantic (Cozytouch) ou le Hub Wiser. Tu peux les renommer, les affecter à une pièce et couper leur pilotage. Chaque système garde ses propres réglages et sa liste d'appareils.</p>

    <details class="stack brand-panel" style="gap:14px">
      <summary class="card-title summary-row" style="cursor:pointer">${icon("thermometer", 16)} Cozytouch / Atlantic<span class="summary-switch">${deviceSwitch('data-brand-toggle="atlantic"', true, "Désactiver tout Atlantic")}</span></summary>
      <div id="sec-atlantic" class="stack" style="gap:14px;margin-top:8px">
        <h3 class="card-title" style="margin-top:6px;display:none">Cozytouch / Atlantic</h3>
      ${textRow("Adresse e-mail", "email", cfg_.email, { type: "email", placeholder: "moi@exemple.fr", sub: "Le compte utilisé dans l'application Cozytouch." })}
      ${row(
        "Mot de passe",
        cfg_.password_set ? "Un mot de passe est déjà enregistré. Laisse vide pour le conserver." : "Aucun mot de passe enregistré.",
        `<input class="input" data-key="password" data-secret type="password" autocomplete="new-password" placeholder="${cfg_.password_set ? "••••••••" : "Mot de passe"}" style="width:min(320px,62vw)">`,
        true
      )}
      ${row("Effacer le mot de passe", "Obligatoire pour repasser en simulation proprement.", toggle("password_clear", false))}
      ${row(
        "Mode simulation",
        "Aucune commande réelle n'est envoyée. Le mode s'active aussi si les identifiants manquent.",
        toggle("mock", Boolean(cfg_.mock))
      )}
      <div class="hint" id="atlanticTestResult"></div>

      <div class="row wrap" style="gap:8px;margin-top:4px">
        <button class="btn" type="button" data-test="atlantic">${icon("shield", 16)} Tester</button>
        <button class="btn" type="button" id="discover">${icon("search", 16)} Voir le compte Atlantic</button>
        <button class="btn" type="button" data-sync>${icon("refresh", 16)} Synchroniser</button>
      </div>

      ${Boolean(cfg_.email || cfg_.password_set) ? `
      <details class="card card-pad" style="margin-top:8px">
        <summary class="card-title" style="cursor:pointer">${icon("fan", 16)} Réglages climatisation (soufflerie / orientation)</summary>
        <div class="stack mt-2" style="gap:10px">
          ${selectRow(
            "Soufflerie par défaut",
            "atlantic_fan_default",
            String((store.state?.settings || {}).atlantic_fan_default ?? ""),
            [
              ["", "Inchangée (télécommande)"],
              ["auto", "Automatique"],
              ["low", "Basse"],
              ["med", "Moyenne"],
              ["high", "Haute"],
            ],
            { sub: "Vitesse appliquée quand eCasa pilote la clim." }
          )}
          ${selectRow(
            "Orientation du flux d'air",
            "atlantic_airflow_default",
            String((store.state?.settings || {}).atlantic_airflow_default ?? ""),
            [
              ["", "Inchangée (télécommande)"],
              ["swing", "Balayage automatique"],
              ["pos1", "Position 1 (vers le haut)"],
              ["pos2", "Position 2"],
              ["pos3", "Position 3"],
              ["pos4", "Position 4"],
              ["pos5", "Position 5"],
              ["pos6", "Position 6 (vers le bas)"],
            ],
            { sub: "Position fixe des volets ou balayage." }
          )}
        </div>
      </details>` : ""}

      <!-- Liste des appareils Atlantic (Cozytouch). -->
      <div class="stack" id="atlanticDevicesList" style="margin-top:8px"></div>
    </details>

    <details class="stack brand-panel" style="margin-top:18px">
      <summary class="card-title summary-row" style="cursor:pointer">${icon("hub", 16)} Schneider Wiser<span class="summary-switch">${deviceSwitch('data-brand-toggle="wiser"', true, "Désactiver tout Wiser")}</span></summary>
      <div id="sec-wiser" class="stack" style="gap:14px;margin-top:8px">
        <div class="row-between" style="align-items:center;gap:8px">
          <button class="btn btn-sm" type="button" id="wiserFind">${icon("wifi", 16)} Chercher le Hub</button>
        </div>
      ${textRow("Adresse du Hub", "wiser_host", cfg_.wiser_host, { placeholder: "http://192.168.1.20", sub: "Adresse IP ou nom du Hub sur le réseau local." })}
      ${row(
        "Secret du Hub",
        cfg_.wiser_secret_set ? "Un secret est déjà enregistré. Laisse vide pour le conserver." : "Le secret se récupère au premier appairage.",
        `<input class="input" data-key="wiser_secret" data-secret type="password" autocomplete="new-password" placeholder="${cfg_.wiser_secret_set ? "••••••••" : "secret"}" style="width:min(320px,62vw)">`,
        true
      )}
      ${row("Effacer le secret", "Nécessaire si le Hub a été réinitialisé.", toggle("wiser_secret_clear", false))}
      <div class="stack" id="wiserResults"></div>
      <details class="stack" style="margin-top:8px" open>
        <summary class="card-title" style="cursor:pointer">${icon("hub", 16)} Appareils Wiser</summary>
        <div class="stack" id="wiserDevicesList" style="margin-top:8px"></div>
      </details>
      <div class="row wrap" style="gap:8px;margin-top:4px">
        <button class="btn" type="button" id="wiserImportPrograms">${icon("download", 16)} Import programmes</button>
      </div>
    </details>
    ${connectorPanels(cfg_)}` ,
  );
}



/** Semaines liées à Pronote : réglage explicite, sinon déduites des jours de cours, sinon A et B. */
function pronoteLinkedWeeksFor(c) {
  const raw = Array.isArray(c.pronote_weeks)
    ? c.pronote_weeks.map((value) => String(value).toUpperCase()).filter((value) => value === "A" || value === "B")
    : [];
  if (raw.length) return raw;
  for (const perWeek of Object.values(c.pronote_school_days || {})) {
    const hasA = Array.isArray(perWeek?.A) && perWeek.A.some(Boolean);
    const hasB = Array.isArray(perWeek?.B) && perWeek.B.some(Boolean);
    if (hasA && !hasB) return ["A"];
    if (hasB && !hasA) return ["B"];
  }
  return ["A", "B"];
}

/**
 * Réglages Pronote par pièce (confort / réduit). Vide = réglage global.
 * Permet par exemple « Froid » sur une climatisation et « Chaud » ailleurs.
 */
function pronoteRoomOverrides(c, rooms) {
  const overrides = c.pronote_room_overrides || {};
  const modeSelect = (roomId, key, value) =>
    `<select class="select" data-pronote-room-mode="${roomId}:${key}" style="width:min(200px,58vw)">
      <option value="">— réglage global —</option>
      ${modeOptions()
        .map(
          ([modeValue, label]) =>
            `<option value="${modeValue}"${value === modeValue ? " selected" : ""}>${escapeHtml(label)}</option>`
        )
        .join("")}
    </select>`;
  const tempInput = (roomId, key, value) =>
    `<input class="input" type="number" min="5" max="30" step="0.5" style="width:110px" placeholder="global" value="${value != null ? escapeHtml(value) : ""}" data-pronote-room-temp="${roomId}:${key}">`;
  const rows = rooms
    .map((room) => {
      const item = overrides[String(room.id)] || {};
      return `<div class="stack" style="gap:8px;padding:12px 0;border-top:1px solid var(--line)">
        <div class="row-between wrap" style="gap:8px">
          <strong>${escapeHtml(room.name)}</strong>
          <span class="tiny muted">vide = réglage global</span>
        </div>
        <div class="form-grid">
          <label class="field"><span class="label">Confort : mode</span>${modeSelect(room.id, "comfort_mode", item.comfort_mode)}</label>
          <label class="field"><span class="label">Confort : consigne</span>${tempInput(room.id, "comfort_temp", item.comfort_temp)}</label>
          <label class="field"><span class="label">Réduit : mode</span>${modeSelect(room.id, "reduced_mode", item.reduced_mode)}</label>
          <label class="field"><span class="label">Réduit : consigne</span>${tempInput(room.id, "reduced_temp", item.reduced_temp)}</label>
        </div>
      </div>`;
    })
    .join("");
  return `<details class="stack">
    <summary class="card-title" style="cursor:pointer">${icon("sliders", 16)} Réglages par pièce (confort / réduit)</summary>
    <p class="hint" style="margin-top:8px">Par défaut, toutes les pièces liées suivent les réglages globaux ci-dessus. Ici, une pièce peut avoir son propre mode : par exemple « Froid » pour une climatisation et « Chaud » pour les chambres.</p>
    ${rows || '<p class="hint">Aucune pièce pour le moment.</p>'}
  </details>`;
}

function pronoteSection() {  const c = cfg();
  const rooms = [...(store.state?.rooms || [])].sort(
    (a, b) => Number(a.position ?? 0) - Number(b.position ?? 0)
  );
  const linkedRoom = Number(c.pronote_room_id || 0);
  const linkedRooms = Array.isArray(c.pronote_room_ids)
    ? c.pronote_room_ids.map(Number)
    : (linkedRoom ? [linkedRoom] : []);
  const linkedWeeks = pronoteLinkedWeeksFor(c);
  return section(
    "pronote",
    "Pronote",
    "school",
    `<details class="stack">
      <summary class="card-title" style="cursor:pointer">${icon("server", 16)} Serveur Pronote</summary>
      <div class="stack" style="gap:14px;margin-top:8px">
        <div class="field">
          <span class="label">Retrouver l'établissement</span>
          <div class="input-row">
            <input class="input" id="pronoteQuery" placeholder="Nom, ville ou code postal" autocomplete="off">
            <button class="btn" type="button" id="pronoteSearch">${icon("search", 16)} Chercher</button>
          </div>
          <span class="hint">La recherche remplit l'adresse Pronote. Pense à enregistrer ensuite.</span>
        </div>
        <div class="stack" id="pronoteResults"></div>
        ${textRow("Adresse Pronote", "pronote_url", c.pronote_url, { placeholder: "https://…/pronote/…", sub: "L'adresse de l'établissement, visible dans le navigateur." })}
        ${textRow("Identifiant", "pronote_username", c.pronote_username, { placeholder: "identifiant Pronote" })}
        ${row(
          "Mot de passe",
          c.pronote_password_set ? "Un mot de passe est déjà enregistré. Laisse vide pour le conserver." : "Aucun mot de passe enregistré.",
          `<input class="input" data-key="pronote_password" data-secret type="password" autocomplete="new-password" placeholder="${c.pronote_password_set ? "••••••••" : "mot de passe"}" style="width:min(320px,62vw)">`,
          true
        )}
        ${row("Effacer le mot de passe", "", toggle("pronote_password_clear", false))}
      </div>
    </details>
    <div class="form-grid">
      <label class="field">
        <span class="label">Type de compte</span>
        <select class="select" data-key="pronote_account">
          <option value="student"${c.pronote_account !== "parent" ? " selected" : ""}>Élève</option>
          <option value="parent"${c.pronote_account === "parent" ? " selected" : ""}>Parent</option>
        </select>
      </label>
      <label class="field">
        <span class="label">Enfant <span class="opt">si plusieurs</span></span>
        <input class="input" data-key="pronote_child" value="${escapeHtml(c.pronote_child || "")}" placeholder="Prénom ou nom">
      </label>
      <label class="field">
        <span class="label">Identifiant ENT <span class="opt">optionnel</span></span>
        <input class="input" data-key="pronote_ent" value="${escapeHtml(c.pronote_ent || "")}" placeholder="ac-aix-marseille">
      </label>
      <label class="field">
        <span class="label">Statut scolaire</span>
        <select class="select" data-key="pronote_student_status">
          ${STUDENT_STATUSES.map(
            ([value, label]) => `<option value="${value}"${c.pronote_student_status === value ? " selected" : ""}>${escapeHtml(label)}</option>`
          ).join("")}
        </select>
      </label>
      <label class="field">
        <span class="label">Trajet domicile ↔ établissement</span>
        <input class="input" data-key="pronote_travel_minutes" data-number type="number" min="0" max="180" value="${escapeHtml(c.pronote_travel_minutes ?? 20)}">
      </label>
    </div>
    ${row(
      "Retour direct (jour sans cours l'après-midi)",
      "Un jour dont le dernier cours se termine avant 13:30 : la pièce repasse en confort juste après le dernier cours + trajet, sans attendre la fin de la pause de cantine, et Pronote gère la journée jusqu'au coucher. Désactivé, la pause de cantine (13:30 + trajet) est prolongée et l'après-midi reste votre planning.",
      toggle("pronote_direct_return", c.pronote_direct_return === true)
    )}
    <div class="field">
      <span class="label">Pièces liées au planning (import automatique)</span>
      <div class="stack" style="gap:6px">
        ${rooms
          .map(
            (room) => `<label class="check"><input type="checkbox" data-pronote-room="${room.id}"${linkedRooms.includes(Number(room.id)) ? " checked" : ""}> ${escapeHtml(room.name)}</label>`
          )
          .join("")}
      </div>
      ${rooms.length ? "" : '<span class="hint">Aucune pièce pour le moment.</span>'}
      <span class="hint">Cocher une pièce la lie à Pronote : son planning est récupéré automatiquement (heure ci-dessous) et appliqué (semaines A et B), sans validation. Les pièces liées sont grisées dans le Planning.</span>
    </div>
    ${pronoteRoomOverrides(c, rooms)}
    <div class="field">
      <span class="label">Semaines liées à Pronote</span>
      <div class="row wrap" style="gap:10px">
        <label class="check"><input type="checkbox" data-pronote-week-setting="A"${linkedWeeks.includes("A") ? " checked" : ""}> Semaine A</label>
        <label class="check"><input type="checkbox" data-pronote-week-setting="B"${linkedWeeks.includes("B") ? " checked" : ""}> Semaine B</label>
      </div>
      <span class="hint">Les jours avec cours des semaines cochées sont gérés par Pronote. Au moins une semaine reste cochée.</span>
    </div>
    <div class="field">
      <span class="label">Jour d'alternance (garde)</span>
      <select class="select" data-key="pronote_switch_day">
        <option value="monday"${c.pronote_switch_day !== "friday" ? " selected" : ""}>Lundi — l'enfant arrive après l'école</option>
        <option value="friday"${c.pronote_switch_day === "friday" ? " selected" : ""}>Vendredi — l'enfant part après l'école</option>
      </select>
      <span class="hint">Lundi : l'enfant arrive chez le parent gardien (pour le repas s'il est externe, après les cours s'il est demi-pensionnaire). Vendredi : il part selon les cours. Pronote ne gère alors que la fin (lundi) ou le début (vendredi) de la journée ; le reste du planning reste modifiable à la main.</span>
    </div>
    ${row(
      "Mise à jour automatique",
      "Récupère le planning Pronote chaque jour à l'heure choisie. Désactivée, l'import reste manuel depuis le Planning.",
      toggle("pronote_auto_enabled", c.pronote_auto_enabled !== false)
    )}
    <div class="field">
      <span class="label">Heure de la mise à jour</span>
      <select class="select" data-key="pronote_sync_minutes" style="width:min(160px,40vw)">
        ${Array.from({ length: 24 }, (_, h) => h)
          .map(
            (h) => `<option value="${h * 60}"${Math.floor((c.pronote_sync_minutes ?? 180) / 60) === h ? " selected" : ""}>${String(h).padStart(2, "0")}:00</option>`
          )
          .join("")}
      </select>
      <span class="hint">Heure locale du serveur. L'électricité reste synchronisée à 3 h.</span>
    </div>
    <div class="form-grid">
      <label class="field">
        <span class="label">Heure de lever</span>
        <input class="input" data-key="pronote_wake_minutes" type="time" value="${minutesToTime(c.pronote_wake_minutes ?? 7 * 60)}" style="width:min(160px,40vw)">
        <span class="hint">À cette heure, le chauffage passe en confort pour la journée.</span>
      </label>
      <label class="field">
        <span class="label">Heure de coucher</span>
        <input class="input" data-key="pronote_bedtime_minutes" type="time" value="${minutesToTime(c.pronote_bedtime_minutes ?? 22 * 60)}" style="width:min(160px,40vw)">
        <span class="hint">À partir de cette heure, le chauffage passe en température réduite pour la nuit.</span>
      </label>
    </div>
    <div class="form-grid">
      <label class="field">
        <span class="label">Confort : mode</span>
        <select class="select" data-key="pronote_comfort_mode">
          ${modeOptions()
            .map(([value, label]) => `<option value="${value}"${c.pronote_comfort_mode === value ? " selected" : ""}>${escapeHtml(label)}</option>`)
            .join("")}
        </select>
      </label>
      <label class="field">
        <span class="label">Confort : consigne</span>
        <input class="input" data-key="pronote_comfort_temp" data-number type="number" min="16" max="30" step="0.5" value="${escapeHtml(c.pronote_comfort_temp ?? 20)}">
      </label>
      <label class="field">
        <span class="label">Réduit : mode</span>
        <select class="select" data-key="pronote_reduced_mode">
          ${modeOptions()
            .map(([value, label]) => `<option value="${value}"${c.pronote_reduced_mode === value ? " selected" : ""}>${escapeHtml(label)}</option>`)
            .join("")}
        </select>
      </label>
      <label class="field">
        <span class="label">Réduit : consigne</span>
        <input class="input" data-key="pronote_reduced_temp" data-number type="number" min="16" max="30" step="0.5" value="${escapeHtml(c.pronote_reduced_temp ?? 16)}">
      </label>
    </div>
    <div class="hint" id="pronoteTestResult"></div>`,
    `<button class="btn" type="button" data-test="pronote">${icon("shield", 16)} Tester la connexion</button>`
  );
}

function weatherSection() {
  const c = cfg();
  return section(
    "weather",
    "Météo extérieure",
    "cloud",
    `${row(
      "Afficher la météo",
      "Température extérieure et ressenti dans les statistiques. Source Open-Meteo, sans clé.",
      toggle("weather_enabled", c.weather_enabled !== false)
    )}
    <div class="field">
      <span class="label">Retrouver la ville</span>
      <div class="input-row">
        <input class="input" id="weatherQuery" placeholder="Nom de ville ou code postal" autocomplete="off">
        <button class="btn" type="button" id="weatherSearch">${icon("search", 16)} Chercher</button>
      </div>
      <span class="hint">Remplit automatiquement la latitude et la longitude.</span>
    </div>
    <div class="stack" id="weatherResults"></div>
    <div class="form-grid">
      <label class="field">
        <span class="label">Ville affichée</span>
        <input class="input" data-key="weather_city" value="${escapeHtml(c.weather_city ?? "")}" placeholder="Ex. Lyon">
      </label>
      <label class="field">
        <span class="label">Latitude</span>
        <input class="input" data-key="weather_latitude" data-number type="number" min="-90" max="90" step="0.0001" value="${escapeHtml(c.weather_latitude ?? 48.8566)}">
      </label>
      <label class="field">
        <span class="label">Longitude</span>
        <input class="input" data-key="weather_longitude" data-number type="number" min="-180" max="180" step="0.0001" value="${escapeHtml(c.weather_longitude ?? 2.3522)}">
      </label>
    </div>
    <p class="hint">Astuce : clique droit sur ta maison dans une carte, « Copier les coordonnées ».</p>`
  );
}

function energySection() {
  const c = cfg();
  const provider = c.energy_provider || "myelectricaldata";
  return section(
    "energy",
    "Électricité",
    "zap",
    `${row(
      "Fournisseur actif",
      "Choisis la source utilisée pour la consommation électrique et les statistiques.",
      `<div class="segmented" id="energyProviderPicker">
        <button type="button" data-provider="myelectricaldata" class="${provider === "myelectricaldata" ? "is-active" : ""}">MyElectricalData</button>
        <button type="button" data-provider="consoapi" class="${provider === "consoapi" ? "is-active" : ""}">ConsoAPI</button>
      </div>
      <input type="hidden" data-key="energy_provider" value="${escapeHtml(provider)}">`
    )}

    <details class="stack sub-section" style="margin-top:18px" ${provider === "myelectricaldata" ? "open" : ""}>
      <summary class="card-title summary-row" style="cursor:pointer">${icon("zap", 16)} MyElectricalData (Enedis)</summary>
      <div style="margin-top:8px">
        ${row(
          "Guide de configuration",
          "Autoriser Enedis, récupérer un token et le coller ici.",
          `<button class="btn btn-sm" type="button" id="energyHelp">${icon("info", 15)} Ouvrir l'aide</button>`
        )}
        ${row(
          "Suivre la consommation",
          "Graphique multi-années dans les statistiques, via MyElectricalData (Enedis).",
          toggle("med_enabled", Boolean(c.med_enabled))
        )}
        ${textRow("Adresse MyElectricalData", "med_url", c.med_url || "https://www.myelectricaldata.fr", {
          placeholder: "https://www.myelectricaldata.fr",
          sub: "Instance publique ou auto-hébergée.",
        })}
        ${textRow("Point de livraison (PDL)", "med_pdl", c.med_pdl, {
          placeholder: "14 chiffres",
          sub: "Identifiant Enedis du compteur Linky, sans espaces.",
        })}
        ${row(
          "Token MyElectricalData",
          c.med_token_set
            ? "Un token est déjà enregistré. Laisse vide pour le conserver."
            : "Affiché par MyElectricalData après le consentement Enedis (valable un an).",
          `<input class="input" data-key="med_token" data-secret type="password" autocomplete="new-password" placeholder="${c.med_token_set ? "••••••••" : "token"}" style="width:min(320px,62vw)">`,
          true
        )}
        ${row("Effacer le token", "À faire si le token a expiré ou a été révoqué.", toggle("med_token_clear", false))}
        ${textRow("Début de l'historique", "med_start", c.med_start, {
          placeholder: "2023-01-01",
          sub: "Vide = 3 ans en arrière (limite Enedis). Format AAAA-MM-JJ.",
        })}
        <div class="hint" id="energyTestResult"></div>
        <div class="row wrap mt-2" style="gap:8px">
          <button class="btn" type="button" data-test="energy" data-provider="myelectricaldata">${icon("shield", 16)} Tester la connexion</button>
        </div>
      </div>
    </details>

    <details class="stack sub-section" style="margin-top:18px" ${provider === "consoapi" ? "open" : ""}>
      <summary class="card-title summary-row" style="cursor:pointer">${icon("zap", 16)} ConsoAPI (par bokup)</summary>
      <div style="margin-top:8px">
        ${row(
          "Guide de configuration",
          "Consentement Enedis via conso.boris.sh, puis token personnel.",
          `<button class="btn btn-sm" type="button" id="consoapiHelp">${icon("info", 15)} Ouvrir l'aide</button>`
        )}
        ${row(
          "Suivre la consommation",
          "Graphique multi-années dans les statistiques, via ConsoAPI (bokup).",
          toggle("consoapi_enabled", Boolean(c.consoapi_enabled))
        )}
        ${textRow("Adresse ConsoAPI", "consoapi_url", c.consoapi_url || "https://conso.boris.sh/api", {
          placeholder: "https://conso.boris.sh/api",
          sub: "Instance publique (conso.boris.sh) ou auto-hébergée.",
        })}
        ${textRow("Point de livraison (PDL)", "consoapi_pdl", c.consoapi_pdl, {
          placeholder: "14 chiffres",
          sub: "Identifiant Enedis du compteur Linky, sans espaces.",
        })}
        ${row(
          "Token ConsoAPI",
          c.consoapi_token_set
            ? "Un token est déjà enregistré. Laisse vide pour le conserver."
            : "Token personnel affiché par ConsoAPI après le consentement Enedis.",
          `<input class="input" data-key="consoapi_token" data-secret type="password" autocomplete="new-password" placeholder="${c.consoapi_token_set ? "••••••••" : "token"}" style="width:min(320px,62vw)">`,
          true
        )}
        ${row("Effacer le token", "À faire si le token a expiré ou a été révoqué.", toggle("consoapi_token_clear", false))}
        ${textRow("Début de l'historique", "consoapi_start", c.consoapi_start, {
          placeholder: "2023-01-01",
          sub: "Vide = 3 ans en arrière. Format AAAA-MM-JJ.",
        })}
        <div class="hint" id="consoapiTestResult"></div>
        <div class="row wrap mt-2" style="gap:8px">
          <button class="btn" type="button" data-test="energy" data-provider="consoapi">${icon("shield", 16)} Tester la connexion</button>
        </div>
      </div>
    </details>`
  );
}

function storageSection() {
  const c = cfg();
  return section(
    "storage",
    "Stockage basse consommation",
    "database",
    `<div class="hint">eCasa garde tout en mémoire et n'écrit sur la carte SD que par lots. C'est ce qui protège la carte du Raspberry Pi.</div>
    ${textRow("Fichier d'état", "state_file", c.state_file, { sub: "Snapshot JSON complet, réécrit au plus toutes les quelques secondes." })}
    ${textRow("Dossier d'historique", "history_dir", c.history_dir, { sub: "Fichiers mensuels append-only, une ligne par heure." })}
    <div class="form-grid">
      <label class="field">
        <span class="label">Intervalle d'écriture <span class="opt">secondes</span></span>
        <input class="input" data-key="history_interval" data-number type="number" min="60" max="86400" step="60" value="${escapeHtml(c.history_interval ?? 900)}">
      </label>
      <label class="field">
        <span class="label">Conservation <span class="opt">jours</span></span>
        <input class="input" data-key="history_days" data-number type="number" min="7" max="3650" step="1" value="${escapeHtml(c.history_days ?? 180)}">
      </label>
      <label class="field">
        <span class="label">Délai snapshot <span class="opt">secondes</span></span>
        <input class="input" data-key="structural_delay" data-number type="number" min="1" max="600" step="1" value="${escapeHtml(c.structural_delay ?? 3)}">
      </label>
    </div>
    ${row("Cache des fichiers web", "Les pages sont chargées en RAM au démarrage. Zéro lecture disque par requête.", toggle("cache_static", c.cache_static !== false))}
    ${row("Compression gzip", "Réduit la taille des réponses réseau.", toggle("gzip_enabled", c.gzip_enabled !== false))}
    <div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
      <div class="row-between" style="margin-bottom:8px">
        <strong style="font-size:13px">Activité disque</strong>
        <button class="btn btn-sm" type="button" id="storageFlush">${icon("save", 14)} Forcer l'écriture</button>
      </div>
      <div id="storageInfo" class="stack"></div>
    </div>`
  );
}

function serverSection() {
  const c = cfg();
  const urls = Array.isArray(c.network_urls) ? c.network_urls : [];
  return section(
    "server",
    "Connexion & serveur",
    "server",
    `${row(
      "Écouter sur le réseau local",
      "Désactive pour n'accepter que cette machine (127.0.0.1).",
      `<div class="segmented" id="hostPicker">
        <button type="button" data-host="127.0.0.1" class="${c.lan_enabled ? "" : "is-active"}">Local</button>
        <button type="button" data-host="0.0.0.0" class="${c.lan_enabled ? "is-active" : ""}">Réseau</button>
      </div>`
    )}
    ${row(
      "Adresse d'écoute",
      "0.0.0.0 écoute sur toutes les interfaces, 127.0.0.1 seulement en local.",
      `<input class="input" data-key="host" value="${escapeHtml(c.host || "0.0.0.0")}" style="width:min(240px,50vw)">`,
      true
    )}
    ${numberRow("Port", "port", c.port ?? 8780, { sub: "Par défaut 8780. Entre 1024 et 65535.", min: 1024, max: 65535 })}
    ${row(
      "Réglages depuis le réseau",
      "Autorise les pages sensibles (réglages, Pronote, arrêt) depuis une autre machine. Réservé aux réseaux de confiance.",
      toggle("allow_remote_admin", Boolean(c.allow_remote_admin))
    )}
    <div class="stack" style="gap:6px">
      <span class="label">Adresses d'accès</span>
      ${
        urls.length
          ? urls
              .map(
                (url) => `<a class="tiny" href="${escapeHtml(url)}" target="_blank" rel="noreferrer" style="word-break:break-all">${escapeHtml(url)}</a>`
              )
              .join("")
          : '<span class="hint">Aucune adresse réseau détectée.</span>'
      }
    </div>
    <div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
      <div class="row-between" style="margin-bottom:8px">
        <strong style="font-size:13px">Machine</strong>
        <button class="btn btn-sm" type="button" id="systemRefresh">${icon("refresh", 14)} Actualiser</button>
      </div>
      <div id="systemInfo" class="stack"></div>
    </div>
    <div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
      <div class="row-between wrap" style="gap:10px">
        <div>
          <strong style="font-size:13px">Journal du serveur</strong>
          <p class="hint mt-1">Événements, actions et erreurs du moteur.</p>
        </div>
        <button class="btn" type="button" data-open-logs>${icon("list", 16)} Ouvrir le journal</button>
      </div>
    </div>
    <div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
      <div class="row-between wrap" style="gap:10px">
        <div>
          <strong style="font-size:13px">Sauvegarde complète</strong>
          <p class="hint mt-1">Archive ZIP : config.json (clés API et mot de passe inclus), état, énergie et historique.</p>
        </div>
        <div class="row wrap" style="gap:8px">
          <button class="btn" type="button" id="configExportBtn" title="Exporter la configuration">${icon("download", 16)} Exporter la configuration</button>
          <button class="btn" type="button" id="configImportBtn" title="Importer la configuration">${icon("upload", 16)} Importer la configuration</button>
          <input id="configImportFile" type="file" accept=".zip,application/zip" hidden>
        </div>
      </div>
    </div>
    <div class="row wrap" style="gap:8px">
      <button class="btn btn-danger" type="button" data-stop>${icon("power", 15)} Arrêter le serveur</button>
    </div>
    <details class="stack sub-section" style="margin-top:18px">
      <summary class="card-title summary-row" style="cursor:pointer">${icon("sliders", 16)} Moteur de planning</summary>
      <div style="margin-top:8px">${engineSection()}</div>
    </details>
    <details class="stack sub-section" style="margin-top:18px">
      <summary class="card-title summary-row" style="cursor:pointer">${icon("database", 16)} Stockage basse consommation</summary>
      <div style="margin-top:8px">${storageSection()}</div>
    </details>
    <details class="stack sub-section" style="margin-top:18px">
      <summary class="card-title summary-row" style="cursor:pointer">${icon("shield", 16)} Tailscale</summary>
      <div style="margin-top:8px">${tailscaleSection()}</div>
    </details>
    <details class="stack sub-section" style="margin-top:18px">
      <summary class="card-title summary-row" style="cursor:pointer">${icon("refresh", 16)} Mise à jour</summary>
      <div style="margin-top:8px">${updateSection()}</div>
    </details>
    </div>`
  );
}

function tailscaleSection() {
  const c = cfg();
  return section(
    "tailscale",
    "Tailscale",
    "shield",
    `<p class="hint">Accès à distance chiffré, sans ouvrir de port sur la box. eCasa peut publier son interface sur ton tailnet.</p>
    ${row(
      "Guide de configuration",
      "Installation, connexion et publication du service sur ton tailnet.",
      `<button class="btn btn-sm" type="button" id="tailscaleHelp">${icon("info", 15)} Ouvrir l'aide</button>`
    )}
    ${row(
      "MagicDNS et HTTPS",
      "À activer dans la console Tailscale, sinon l'app Android ne peut pas charger l'URL.",
      `<div class="row wrap" style="gap:8px">
        <button class="btn btn-sm" type="button" id="tailscaleDnsHelp">${icon("info", 15)} Quoi faire</button>
        <a class="btn btn-sm" href="https://login.tailscale.com/admin/dns" target="_blank" rel="noreferrer">${icon("external", 14)} Console Tailscale</a>
      </div>`
    )}
    ${row("Activer Tailscale", "Au démarrage, eCasa peut connecter le nœud et publier le service.", toggle("tailscale_enabled", Boolean(c.tailscale_enabled)))}
    ${row("Publier automatiquement", "Au démarrage, expose l'interface sur le tailnet en HTTPS via « tailscale serve ».", toggle("tailscale_auto_serve", Boolean(c.tailscale_auto_serve)))}
    ${textRow("Nom du nœud", "tailscale_hostname", c.tailscale_hostname || "ecasa", {
      sub: "Nom résolu par l'app Android : &lt;nom&gt;.&lt;tailnet&gt;.ts.net. Doit être « ecasa ».",
      placeholder: "ecasa",
    })}
    ${textRow("Chemin de tailscale", "tailscale_bin", c.tailscale_bin || "", {
      sub: "Laisser vide pour la détection automatique (utile sur Windows : C:\\Program Files\\Tailscale\\tailscale.exe).",
      placeholder: "détection automatique",
    })}
    ${row(
      "Clé d'authentification",
      c.tailscale_authkey_set ? "Une clé est déjà enregistrée. Laisse vide pour la conserver." : "Optionnelle si le nœud est déjà connecté.",
      `<input class="input" data-key="tailscale_authkey" data-secret type="password" autocomplete="new-password" placeholder="${c.tailscale_authkey_set ? "••••••••" : "tskey-…"}" style="width:min(320px,62vw)">`,
      true
    )}
    ${row("Effacer la clé", "", toggle("tailscale_authkey_clear", false))}
    <div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
      <div class="row-between" style="margin-bottom:8px">
        <strong style="font-size:13px">État du nœud</strong>
        <button class="btn btn-sm" type="button" id="tailscaleRefresh">${icon("refresh", 14)} Actualiser</button>
      </div>
      <div id="tailscaleStatus" class="stack"><div class="boot" style="min-height:70px"><span class="spinner"></span><p>Lecture…</p></div></div>
      <div class="row wrap mt-2" style="gap:8px">
        <button class="btn btn-sm" type="button" id="tsUp">${icon("wifi", 14)} Connecter</button>
        <button class="btn btn-sm" type="button" id="tsServeOn">${icon("external", 14)} Publier</button>
        <button class="btn btn-sm" type="button" id="tsServeOff">${icon("x", 14)} Dépublier</button>
        <button class="btn btn-sm" type="button" id="tsInstall">${icon("download", 14)} Installer / désinstaller</button>
      </div>
      <p class="hint mt-2">« Dépublier » coupe l'accès via l'URL HTTPS : pour republier depuis un autre appareil, ouvre eCasa sur le réseau local (port 8780).</p>
    </div>`
  );
}

function updateSection() {
  const data = updateInfo;
  const current = store.system?.app?.version || "";
  const commit = data?.current || "";
  const isGit = data ? data.is_git !== false : true;
  const checking = data === null;
  const available = Boolean(data?.available);
  const behind = data?.behind || 0;
  const remote = data?.remote || "";
  const checked = store.state?.update?.checked_at || "";
  const disabled = checking || !available;
  return section(
    "update",
    "Mise à jour",
    "refresh",
    `<p class="hint">Met à jour eCasa depuis le dépôt Git (git pull), puis redémarre le serveur. Réservé à la machine locale.</p>
    ${row("Version actuelle", "", `<span class="tiny mono">${escapeHtml(current)}${commit ? " (" + escapeHtml(commit) + ")" : ""}</span>`)}
    ${row(
      "État",
      "",
      checking
        ? '<span class="hint">Vérification en cours…</span>'
        : !isGit
          ? '<span class="hint is-bad">eCasa n\'est pas installé depuis Git : mise à jour automatique indisponible.</span>'
          : data?.error
            ? `<span class="hint is-bad">${escapeHtml(data.error)}</span>`
            : available
              ? `<span class="hint is-ok">${behind} commit${behind > 1 ? "s" : ""} en attente${remote ? " (cible " + escapeHtml(remote) + ")" : ""}.</span>`
              : '<span class="hint is-ok">À jour : aucune mise à jour disponible.</span>'
    )}
    <div class="row wrap mt-2" style="gap:8px">
      <button class="btn btn-sm" type="button" id="updateCheck">${icon("refresh", 14)} Vérifier</button>
      <button class="btn btn-primary" type="button" id="updateApply" ${disabled ? "disabled" : ""}>${icon("download", 14)} Mettre à jour</button>
    </div>
    ${checked ? `<p class="hint mt-1">Dernier contrôle automatique : ${escapeHtml(fmtDateTime(checked))}.</p>` : ""}
    ${available ? '<p class="hint mt-1">La mise à jour télécharge le code, installe les dépendances, puis redémarre le serveur (quelques secondes d\'interruption).</p>' : '<p class="hint mt-1">Le bouton est désactivé tant qu\'aucune mise à jour n\'est disponible.</p>'}`,
    ""
  );
}

function supportSection() {
  const donate = String(SITE.donateUrl || "").trim();
  const link = (url, iconName, label) =>
    `<a class="btn" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${icon(iconName, 16)} ${label}</a>`;
  return section(
    "support",
    "Soutenir eCasa",
    "sparkles",
    `<p class="hint" style="margin-top:0">eCasa est libre, sans publicité, sans compte en ligne et sans télémétrie. Toutes les fonctions restent gratuites : un don finance le matériel de test et le temps de développement.</p>
     <div class="row wrap" style="gap:10px">
       ${
         donate
           ? `<a class="btn btn-primary" href="${escapeHtml(donate)}" target="_blank" rel="noopener noreferrer">${icon("sparkles", 16)} Faire un don</a>`
           : ""
       }
       ${link(SITE.repo, "external", "Code source")}
       ${link(SITE.docs, "list", "Documentation")}
       ${link(SITE.issues, "info", "Signaler un problème")}
     </div>
     ${
       donate
         ? ""
         : `<p class="hint">Aucun lien de don n'est configuré pour cette instance (voir <code>web/js/site.js</code>).</p>`
     }`
  );
}

/* --------------------------------------------------------------- liaisons */

function jumpToSection(id, instant = false) {
  const target = root?.querySelector(`#sec-${id}`);
  if (!target) return;
  // Certaines sections sont repliées dans un <details> (ex. Mise à jour dans
  // Serveur) : on déplie les ancêtres avant de mesurer la position.
  let node = target;
  while (node && node !== root) {
    const parent = node.parentElement;
    if (parent && parent.tagName === "DETAILS") parent.open = true;
    node = parent;
  }
  // Laisse la place au bandeau supérieur pour que le titre de la section
  // reste visible après le défilement.
  const topbar = document.querySelector(".topbar, header, .app-bar, .navbar");
  const settingsNav = document.querySelector(".settings-nav");
  const offset = (topbar?.offsetHeight || 64) + (settingsNav?.offsetHeight || 40) + 16;
  const top = target.getBoundingClientRect().top + window.scrollY - offset;
  window.scrollTo({
    top: Math.max(0, top),
    behavior: instant ? "auto" : "smooth",
  });
  root.querySelectorAll("[data-jump]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.jump === id);
  });
}

function onWindowJump(event) {
  const id = String(event?.detail || "");
  if (id) jumpToSection(id, false);
}

function bind() {
  const pronoteAuto = root.querySelector('[data-key="pronote_auto_enabled"]');
  const pronoteHour = root.querySelector('[data-key="pronote_sync_minutes"]');
  if (pronoteAuto && pronoteHour) {
    const syncPronoteHour = () => {
      pronoteHour.disabled = !pronoteAuto.checked;
    };
    pronoteAuto.addEventListener("change", syncPronoteHour);
    syncPronoteHour();
  }

  root.querySelectorAll("[data-brand-toggle]").forEach((input) => {
    bindSummarySwitch(input);
    input.onchange = async () => {
      const key = input.dataset.brandToggle;
      const devices = (store.state?.devices || []).filter((device) =>
        key === "wiser" ? device.device_type === "wiser" : device.device_type !== "wiser"
      );
      const label = key === "wiser" ? "Wiser" : "Atlantic";
      const enable = Boolean(input.checked);
      input.disabled = true;
      try {
        for (const device of devices) {
          if ((Number(device.enabled) !== 0) === enable) continue;
          await put(`/api/devices/${device.device_id}`, { enabled: enable ? 1 : 0 });
          // Mise à jour locale immédiate : le grisage ne doit pas attendre le
          // prochain cycle de rafraîchissement (qui peut être en vol).
          device.enabled = enable ? 1 : 0;
        }
        renderDevices();
        renderRooms();
        refreshState({ silent: true });
        toast(`${label} ${enable ? "activé" : "désactivé"}.`);
      } catch (err) {
        toastError(err);
        input.checked = !input.checked;
      } finally {
        input.disabled = false;
      }
    };
  });

  root.querySelectorAll("[data-connector-enabled]").forEach((input) => {
    bindSummarySwitch(input);
    input.addEventListener("change", async () => {
      const enabled = Boolean(input.checked);
      input.disabled = true;
      try {
        await setConnectorEnabled(input.dataset.connectorEnabled, enabled);
        const devicesHost = root.querySelector(
          `[data-connector-devices="${input.dataset.connectorEnabled}"]`
        );
        if (devicesHost) {
          devicesHost.innerHTML = enabled
            ? '<p class="hint">Clique sur Synchroniser pour lister les appareils.</p>'
            : '<p class="hint">Système désactivé : eCasa n\'interroge plus ce système.</p>';
        }
        syncBrandLocks();
        toast(enabled ? "Système activé." : "Système désactivé.");
      } catch (err) {
        toastError(err);
        input.checked = !input.checked;
      } finally {
        input.disabled = false;
      }
    });
  });

  root.querySelectorAll("[data-connector-sync]").forEach((button) => {
    button.addEventListener("click", () => loadConnectorDevices(button.dataset.connectorSync, button));
  });

  root.querySelectorAll("[data-connector-test]").forEach((button) => {
    button.addEventListener("click", async () => {
      const connectorId = button.dataset.connectorTest;
      const out = root.querySelector(`[data-connector-result="${connectorId}"]`);
      button.disabled = true;
      try {
        const result = await post("/api/connectors/test", { id: connectorId });
        if (out) {
          out.textContent = result.ok
            ? result.message || "Connecteur joignable."
            : result.error || result.message || "Connecteur indisponible.";
          out.classList.toggle("is-bad", !result.ok);
        }
        if (result.ok) toast("Connecteur joignable.");
      } catch (err) {
        if (out) {
          out.textContent = err.message;
          out.classList.add("is-bad");
        }
        toastError(err);
      } finally {
        button.disabled = false;
      }
    });
  });

  root.querySelectorAll("[data-jump]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      jumpToSection(link.dataset.jump);
    });
  });

  root.querySelectorAll("[data-theme]").forEach((button) => {
    button.addEventListener("click", () => {
      const theme = button.dataset.theme;
      setPref("theme", theme);
      window.dispatchEvent(new CustomEvent("ecasa:theme", { detail: theme }));
      root.querySelectorAll("[data-theme]").forEach((item) => item.classList.toggle("is-active", item === button));
    });
  });

  root.querySelectorAll("[data-pref]").forEach((input) => {
    input.addEventListener("change", () => {
      setPref(input.dataset.pref, input.checked);
    });
  });

  // Journal déplacé ici : le menu principal est réservé aux usages quotidiens.
  root.querySelector("[data-open-logs]")?.addEventListener("click", () => {
    location.hash = "#/logs";
  });

  root.querySelector("#installHelp")?.addEventListener("click", () => {
    openSheet({
      title: "Installer eCasa",
      sub: "Une application, pas un onglet",
      body: `<div class="stack">
        <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">Android / Chrome</div><div class="sr-sub">Menu ⋮ → « Installer l'application » ou « Ajouter à l'écran d'accueil ».</div></div></div>
        <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">iPhone / Safari</div><div class="sr-sub">Partager → « Sur l'écran d'accueil ».</div></div></div>
        <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">Ordinateur</div><div class="sr-sub">Une icône d'installation apparaît dans la barre d'adresse.</div></div></div>
      </div>`,
    });
  });

  // Enregistrement automatique : plus de bouton « Enregistrer » dans les
  // sections. On sauvegarde à la validation du champ (change), avec un
  // anti-rebond pour les champs numériques/textes.
  for (const name of [
    "engine",
    "weeks",
    "school",
    "weather",
    "energy",
    "storage",
    "server",
    "pronote",
    "devices",
  ]) {
    const container = root.querySelector(`#sec-${name}`);
    if (!container) continue;
    container.addEventListener("change", (event) => {
      // Les interrupteurs de marque ont leur propre logique (appareils).
      if (event.target.closest("[data-brand-toggle], [data-connector-enabled]")) {
        return;
      }
      // Évite le double enregistrement des sous-sections du serveur
      // (moteur, stockage, Tailscale sont imbriqués dans #sec-server).
      event.stopPropagation();
      scheduleAutoSave(name);
    });
  }

  root.querySelector('[data-key="school_zone"]')?.addEventListener("change", () => {
    loadSchoolCalendar();
  });

  root.querySelector("[data-school-refresh]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const zone = String(
      root.querySelector('[data-key="school_zone"]')?.value || cfg().school_zone || ""
    );
    if (!zone) {
      toastError(new Error("Choisis d'abord une zone scolaire."));
      return;
    }
    button.disabled = true;
    try {
      const payload = await post("/api/school-calendar/refresh", { zone });
      const next = payload?.next;
      const label = next
        ? `Calendrier actualisé : ${next.name} le ${fmtDate(next.date)}.`
        : "Calendrier actualisé.";
      toast(label);
      const node = root.querySelector("#schoolNext");
      if (node) {
        node.textContent = next
          ? `Prochaine fermeture : ${next.name} le ${fmtDate(next.date)}`
          : "Aucune fermeture à venir dans le calendrier.";
      }
    } catch (err) {
      toastError(err);
    } finally {
      button.disabled = false;
    }
  });

  root.querySelectorAll("[data-test]").forEach((button) => {
    button.addEventListener("click", () => testConnection(button.dataset.test, button));
  });

  const energyProviderInput = root.querySelector('[data-key="energy_provider"]');
  root.querySelectorAll("#energyProviderPicker [data-provider]").forEach((button) => {
    button.addEventListener("click", () => {
      const value = button.dataset.provider;
      if (energyProviderInput) {
        energyProviderInput.value = value;
        energyProviderInput.dispatchEvent(new Event("change", { bubbles: true }));
      }
      root.querySelectorAll("#energyProviderPicker [data-provider]").forEach((item) =>
        item.classList.toggle("is-active", item === button)
      );
    });
  });

  root.querySelectorAll("[data-sync]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await post("/api/sync");
        await refreshState({ silent: true });
        toast("Appareils synchronisés.");
      } catch (err) {
        toastError(err);
      } finally {
        button.disabled = false;
      }
    });
  });

  root.querySelectorAll("[data-apply]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await post("/api/apply");
        await refreshState({ silent: true });
        toast("Consignes appliquées.");
      } catch (err) {
        toastError(err);
      } finally {
        button.disabled = false;
      }
    });
  });

  root.querySelectorAll("[data-restart]").forEach((button) => {
    button.addEventListener("click", () => restartServer(button));
  });

  root.querySelectorAll("[data-stop]").forEach((button) => {
    button.addEventListener("click", () => stopServer(button));
  });

  root.querySelector("#addRoom")?.addEventListener("click", () => openRoomEditor(null));

  root.querySelector("#discover")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const payload = await get("/api/discover");
      openSheet({
        title: "Appareils du compte Atlantic",
        sub: `${(payload.devices || []).length} appareil(s) trouvé(s)`,
        body: `<div class="list">${
          (payload.devices || [])
            .map(
              (device) => `<div class="list-row">
                <div class="li-main">
                  <div class="li-title">${escapeHtml(device.name || `Appareil ${device.deviceId}`)}</div>
                  <div class="li-sub">ID ${escapeHtml(device.deviceId)} · modèle ${escapeHtml(device.modelId || "—")}</div>
                </div>
              </div>`
            )
            .join("") || '<p class="hint">Aucun appareil. Vérifie les identifiants puis synchronise.</p>'
        }</div><p class="hint mt-2">Utilise « Synchroniser » pour les ajouter à eCasa.</p>`,
      });
    } catch (err) {
      toastError(err);
    } finally {
      button.disabled = false;
    }
  });

  root.querySelector("#wiserFind")?.addEventListener("click", findWiser);
  root.querySelector("#wiserImportPrograms")?.addEventListener("click", importWiserPrograms);
  root.querySelector("#pronoteSearch")?.addEventListener("click", searchPronote);
  root.querySelector("#pronoteQuery")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      searchPronote();
    }
  });

  root.querySelector("#weatherSearch")?.addEventListener("click", searchWeatherCity);
  root.querySelector("#weatherQuery")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      searchWeatherCity();
    }
  });

  root.querySelector("#storageFlush")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await post("/api/storage/flush");
      await refreshSystem({ silent: true });
      renderStorage();
      toast("État écrit sur le disque.");
    } catch (err) {
      toastError(err);
    } finally {
      button.disabled = false;
    }
  });

  root.querySelector("#systemRefresh")?.addEventListener("click", async () => {
    await refreshSystem({ silent: true });
    renderSystem();
  });

  root.querySelector("#configExportBtn")?.addEventListener("click", exportConfigBackup);
  root.querySelector("#configImportBtn")?.addEventListener("click", () => root.querySelector("#configImportFile")?.click());
  root.querySelector("#configImportFile")?.addEventListener("change", importConfigBackup);

  root.querySelector("#tailscaleRefresh")?.addEventListener("click", loadTailscale);
  root.querySelector("#updateCheck")?.addEventListener("click", loadUpdate);
  root.querySelector("#updateApply")?.addEventListener("click", updateAction);
  root.querySelector("#tailscaleHelp")?.addEventListener("click", openTailscaleHelp);
  root.querySelector("#tailscaleDnsHelp")?.addEventListener("click", openTailscaleDnsHelp);
  root.querySelector("#energyHelp")?.addEventListener("click", openEnergyHelp);
  root.querySelector("#consoapiHelp")?.addEventListener("click", openConsoApiHelp);
  root.querySelector("#tsUp")?.addEventListener("click", connectTailscale);
  root.querySelector("#tsServeOn")?.addEventListener("click", () => tailscaleAction("serve/enable"));
  root.querySelector("#tsServeOff")?.addEventListener("click", () => tailscaleAction("serve/disable"));
root.querySelector("#tsInstall")?.addEventListener("click", () => {
  const installed = Boolean(tailscale?.installed);
  tailscaleAction(installed ? "uninstall" : "install");
});

  root.querySelector("#hostPicker")?.querySelectorAll("[data-host]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = root.querySelector('#sec-server [data-key="host"]');
      if (input) {
        input.value = button.dataset.host;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
      root.querySelectorAll("#hostPicker [data-host]").forEach((item) => item.classList.toggle("is-active", item === button));
    });
  });

  paintEngineState();
}

/* --------------------------------------------------------------- save */

const autoSaveTimers = new Map();

/** Enregistre une section peu après le dernier changement (anti-rebond). */
function scheduleAutoSave(name) {
  const previous = autoSaveTimers.get(name);
  if (previous) clearTimeout(previous);
  const timer = setTimeout(() => {
    autoSaveTimers.delete(name);
    if (!root || !root.isConnected) return;
    saveSection(name, null, { silent: true });
  }, 400);
  autoSaveTimers.set(name, timer);
}

async function saveSection(name, button, { silent = false } = {}) {
  if (!root) return;
  const container = root.querySelector(`#sec-${name}`);
  if (!container) return;

  if (button) button.disabled = true;
  try {
    if (name === "engine") {
      const settings = {
        engine_enabled: container.querySelector('[data-key="engine_enabled"]')?.checked ? "1" : "0",
        poll_interval: String(container.querySelector('[data-key="poll_interval"]')?.value || "60"),
        preheat_max_minutes: String(
          container.querySelector('[data-key="preheat_max_minutes"]')?.value ?? "120"
        ),
      };
      const payload = await put("/api/settings", settings);
      if (store.state) store.state.settings = payload.settings;
      if (!silent) toast("Moteur mis à jour.");
      paintEngineState();
    } else if (name === "weeks") {
      const settings = {
        week_mode: container.querySelector('[data-key="week_mode"]')?.value || "parity",
        week_flip: container.querySelector('[data-key="week_flip"]')?.checked ? "1" : "0",
        // Le jour d'alternance se règle dans la section Pronote : on ne
        // l'écrase que si le champ est présent ici.
        ...(container.querySelector('[data-key="week_switch_day"]')
          ? { week_switch_day: container.querySelector('[data-key="week_switch_day"]').value || "monday" }
          : {}),
        // default_week n'est pas affiché ici : on ne l'écrase que s'il existe.
        ...(container.querySelector('[data-key="default_week"]')
          ? { default_week: container.querySelector('[data-key="default_week"]').value || "A" }
          : {}),
      };
      const payload = await put("/api/settings", settings);
      if (store.state) store.state.settings = payload.settings;
      await refreshState({ silent: true });
      if (!silent) toast("Alternance des semaines enregistrée.");
    } else {
      const body = collect(container);
      if (name === "devices") {
        // Connecteurs des autres systèmes (Daikin, Bosch, Netatmo, Mitsubishi).
        const connectors = {};
        container.querySelectorAll("[data-connector]").forEach((node) => {
          const [connectorId, key] = String(node.dataset.connector || "").split(":");
          if (!connectorId || !key) return;
          connectors[connectorId] = connectors[connectorId] || {};
          connectors[connectorId][key] = node.value;
        });
        if (Object.keys(connectors).length) body.connectors = connectors;
      }
      if (name === "pronote") {
        const checked = [...container.querySelectorAll("[data-pronote-room]")]
          .filter((box) => box.checked)
          .map((box) => Number(box.dataset.pronoteRoom));
        body.pronote_room_ids = checked;
        body.pronote_room_id = checked.length ? checked[0] : 0;
        const weeks = [...container.querySelectorAll("[data-pronote-week-setting]")]
          .filter((box) => box.checked)
          .map((box) => box.dataset.pronoteWeekSetting);
        body.pronote_weeks = weeks.length ? weeks : ["A", "B"];
        // Réglages confort/réduit par pièce (vide = global).
        const roomOverrides = {};
        container.querySelectorAll("[data-pronote-room-mode]").forEach((node) => {
          const [roomId, key] = String(node.dataset.pronoteRoomMode || "").split(":");
          if (!roomId || !key || !node.value) return;
          roomOverrides[roomId] = roomOverrides[roomId] || {};
          roomOverrides[roomId][key] = node.value;
        });
        container.querySelectorAll("[data-pronote-room-temp]").forEach((node) => {
          const [roomId, key] = String(node.dataset.pronoteRoomTemp || "").split(":");
          const raw = String(node.value || "").trim();
          if (!roomId || !key || !raw) return;
          roomOverrides[roomId] = roomOverrides[roomId] || {};
          roomOverrides[roomId][key] = Number(raw);
        });
        body.pronote_room_overrides = roomOverrides;
        // Heures de lever/coucher : input type="time" -> minutes.
        for (const key of ["pronote_wake_minutes", "pronote_bedtime_minutes"]) {
          const timeInput = container.querySelector(`[data-key="${key}"]`);
          if (timeInput && timeInput.value) {
            const parts = String(timeInput.value).split(":");
            const h = Number(parts[0] || 0);
            const m = Number(parts[1] || 0);
            body[key] = h * 60 + m;
          }
        }
      }
      const payload = await put("/api/config", body);
      store.config = payload.config;
      if (name === "devices") {
        // Soufflerie / orientation sont des *settings* du moteur, pas des
        // clés de config.json : elles se sauvegardent via /api/settings.
        const fan = container.querySelector('[data-key="atlantic_fan_default"]');
        const airflow = container.querySelector('[data-key="atlantic_airflow_default"]');
        if (fan || airflow) {
          const settings = {};
          if (fan) settings.atlantic_fan_default = fan.value || "";
          if (airflow) settings.atlantic_airflow_default = airflow.value || "";
          const settingsPayload = await put("/api/settings", settings);
          if (store.state) store.state.settings = settingsPayload.settings;
        }
      }
      if (name === "energy") {
        const clear = container.querySelector('[data-key="med_token_clear"]');
        if (clear) clear.checked = false;
      }
      await refreshConfig({ silent: true });
      await refreshSystem({ silent: true });
      renderStorage();
      renderSystem();
      if (!silent) toast("Réglages enregistrés.");
      if (name === "weather") window.refreshWeather?.();
      if (name === "tailscale") await loadTailscale();
      if (name === "update") await loadUpdate();
      if (name === "pronote") await refreshState({ silent: true });
      if (payload.restart_required) showRestartBanner();
    }
  } catch (err) {
    toastError(err);
  } finally {
    if (button) button.disabled = false;
  }
}

function showRestartBanner() {
  const banner = root?.querySelector("#restartBanner");
  if (banner) banner.hidden = false;
}

function paintEngineState() {
  if (!root) return;
  const settings = store.state?.settings || {};
  const enabled = String(settings.engine_enabled ?? "1") !== "0";
  const node = root.querySelector("#engineState");
  if (node) node.textContent = enabled ? "Moteur actif" : "En pause";
  const mode = root.querySelector("#engineMode");
  if (mode) {
    mode.textContent = store.state?.status?.mock ? "Simulation" : "Réel";
    mode.className = `pill ${store.state?.status?.mock ? "pill-warn" : "pill-ok"}`;
  }
}

/* --------------------------------------------------------------- tests */

async function testConnection(kind, button) {
  const resultId =
    kind === "atlantic"
      ? "#atlanticTestResult"
      : kind === "pronote"
        ? "#pronoteTestResult"
        : button?.dataset?.provider === "consoapi"
          ? "#consoapiTestResult"
          : "#energyTestResult";
  const result = root?.querySelector(resultId);
  if (!result) return;
  button.disabled = true;
  result.className = "hint";
  result.textContent = "Test en cours…";

  try {
    let payload;
    if (kind === "atlantic") {
      const container = root.querySelector("#sec-atlantic");
      const body = collect(container);
      payload = await post("/api/config/test", {
        email: body.email,
        password: body.password || undefined,
      });
    } else if (kind === "pronote") {
      const container = root.querySelector("#sec-pronote");
      const body = collect(container);
      payload = await post("/api/pronote/test", body);
    } else {
      const container = root.querySelector("#sec-energy");
      const body = collect(container);
      if (button?.dataset?.provider) body.energy_provider = button.dataset.provider;
      payload = await post("/api/energy/test", body);
    }

    result.className = `hint ${payload.ok ? "is-ok" : "is-bad"}`;
    const extras = [];
    if (payload.establishment) extras.push(payload.establishment);
    if (payload.child) extras.push(`enfant : ${payload.child}`);
    if (payload.pdl) extras.push(`PDL ${payload.pdl}`);
    if (payload.last_date) extras.push(`dernière donnée ${payload.last_date}`);
    if (payload.device_count !== undefined) extras.push(`${payload.device_count} appareil(s)`);
    if (payload.samples !== undefined) extras.push(`${payload.samples} jour(s)`);
    result.innerHTML = `${escapeHtml(payload.message || (payload.ok ? "Connexion réussie." : "Échec."))}${
      extras.length ? `<br><span class="tiny muted">${escapeHtml(extras.join(" · "))}</span>` : ""
    }`;
    if (payload.ok) {
      toast(
        kind === "atlantic"
          ? "Compte Atlantic validé."
          : kind === "pronote"
            ? "Connexion Pronote validée."
            : "MyElectricalData validé."
      );
    }
  } catch (err) {
    result.className = "hint is-bad";
    result.textContent = err.message;
    toastError(err);
  } finally {
    button.disabled = false;
  }
}

/* ----------------------------------------------------- aide électricité */

function openEnergyHelp() {
  openSheet({
    title: "Configurer MyElectricalData",
    sub: "Consommation Enedis dans eCasa",
    body: `<div class="stack">
      <p class="hint">MyElectricalData est une passerelle gratuite entre Enedis et eCasa. Il faut d'abord autoriser l'accès à tes données Enedis, puis récupérer un token à coller dans les réglages.</p>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">1. Autoriser l'accès à tes données Enedis</div><div class="sr-sub">Sur le site MyElectricalData, clique sur « Faire la demande de consentement » en bas de page, connecte-toi à ton espace Enedis et autorise la passerelle. Le consentement est valable 36 mois.</div></div></div>
      <a class="btn btn-sm" href="https://www.myelectricaldata.fr" target="_blank" rel="noreferrer">${icon("external", 14)} Ouvrir MyElectricalData</a>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">2. Activer la collecte horaire chez Enedis</div><div class="sr-sub">Dans ton espace Enedis, coche « Enregistrement de la consommation horaire » et « Collecte de la consommation horaire ». Sans ça, les données sont incomplètes. À réactiver chaque année.</div></div></div>
      <a class="btn btn-sm" href="https://mon-compte-particulier.enedis.fr/donnees/" target="_blank" rel="noreferrer">${icon("external", 14)} Ouvrir l'espace Enedis</a>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">3. Récupérer le token</div><div class="sr-sub">Après le consentement, MyElectricalData affiche ton token : une chaîne de 54 caractères. Il est valable un an, puis il faut recommencer l'étape 1.</div></div></div>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">4. Renseigner eCasa</div><div class="sr-sub">Colle le PDL (14 chiffres, visible sur ta facture ou dans l'espace Enedis) et le token dans les champs ci-dessus, puis clique sur « Tester la connexion » avant d'enregistrer.</div></div></div>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">Bon à savoir</div><div class="sr-sub">Enedis limite chaque requête à 1095 jours : eCasa découpe par année et affiche jusqu'à 3 ans d'historique. Les données sont mises en cache 6 h en mémoire, rien n'est écrit sur le disque.</div></div></div>

      <p class="hint mt-2">Liens utiles : <a href="https://www.myelectricaldata.fr/documentation/" target="_blank" rel="noreferrer">Documentation</a> · <a href="https://www.myelectricaldata.fr/faq/" target="_blank" rel="noreferrer">FAQ</a> · <a href="https://www.myelectricaldata.fr/error_code/" target="_blank" rel="noreferrer">Codes d'erreur</a></p>
    </div>`,
    footer: `<a class="btn btn-primary" href="https://www.myelectricaldata.fr" target="_blank" rel="noreferrer">${icon("external", 16)} Ouvrir MyElectricalData</a>`,
  });
}

function openConsoApiHelp() {
  openSheet({
    title: "Configurer ConsoAPI (bokup)",
    sub: "Consommation Enedis via ConsoAPI",
    body: `<div class="stack">
      <p class="hint">ConsoAPI est une passerelle gratuite et open-source entre Enedis et eCasa, maintenue par bokup. Il faut d'abord autoriser l'accès à tes données Enedis, puis récupérer un token personnel.</p>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">1. Autoriser l'accès à tes données Enedis</div><div class="sr-sub">Sur conso.boris.sh, démarre le consentement, connecte-toi à ton espace Enedis et autorise la passerelle. Le consentement est valable un an.</div></div></div>
      <a class="btn btn-sm" href="https://conso.boris.sh" target="_blank" rel="noreferrer">${icon("external", 14)} Ouvrir ConsoAPI</a>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">2. Activer la collecte horaire chez Enedis</div><div class="sr-sub">Dans ton espace Enedis, coche « Enregistrement de la consommation horaire » et « Collecte de la consommation horaire ». Sans ça, les données sont incomplètes. À réactiver chaque année.</div></div></div>
      <a class="btn btn-sm" href="https://mon-compte-particulier.enedis.fr/donnees/" target="_blank" rel="noreferrer">${icon("external", 14)} Ouvrir l'espace Enedis</a>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">3. Récupérer le token</div><div class="sr-sub">Après le consentement, ConsoAPI affiche ton token personnel (format xxx.yyy.zzz). Il est valable un an, puis il faut recommencer l'étape 1.</div></div></div>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">4. Renseigner eCasa</div><div class="sr-sub">Colle le PDL (14 chiffres) et le token dans les champs ci-dessus, puis clique sur « Tester la connexion » avant d'enregistrer. Choisis ensuite ConsoAPI comme fournisseur actif.</div></div></div>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">Bon à savoir</div><div class="sr-sub">Les données sont livrées par Enedis avec un jour de décalage (vers 8 h). ConsoAPI est gratuit et limité à 5 req/s et 10 000 req/h partagés : une seule requête par jour suffit. Enedis limite l'historique à 3 ans (1095 jours).</div></div></div>

      <p class="hint mt-2">Liens utiles : <a href="https://conso.boris.sh/documentation" target="_blank" rel="noreferrer">Documentation</a> · <a href="https://conso.boris.sh/exemples" target="_blank" rel="noreferrer">Exemples</a></p>
    </div>`,
    footer: `<a class="btn btn-primary" href="https://conso.boris.sh" target="_blank" rel="noreferrer">${icon("external", 16)} Ouvrir ConsoAPI</a>`,
  });
}

async function findWiser(event) {
  const button = event?.currentTarget;
  if (button) button.disabled = true;
  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `<div class="boot" style="min-height:100px"><span class="spinner"></span><p>Balayage du réseau local…</p></div>
    <p class="hint" style="margin-top:12px">Pour récupérer la clé du Hub : dans l'application Wiser, va dans <strong>Réglages → Passerelle → Secret</strong>, ou utilise la touche physique du Hub lors du premier appairage. L'adresse IP détectée est à reporter dans le champ « Adresse du Hub » ci-dessous, puis enregistre.</p>`;
  const sheet = openSheet({ title: "Hubs Wiser détectés", sub: "Sélectionne un Hub pour remplir l'adresse.", body });
  const host = body;

  try {
    const payload = await get("/api/wiser/find");
    const candidates = payload.candidates || [];
    if (!candidates.length) {
      host.innerHTML = `<p class="hint">Aucun Hub détecté. Vérifie que le Hub est allumé et sur le même réseau.</p>` + host.innerHTML.split("<p class=\"hint\" style=\"margin-top:12px\">")[1] ? `<p class="hint" style="margin-top:12px">Pour récupérer la clé du Hub : dans l'application Wiser, va dans <strong>Réglages → Passerelle → Secret</strong>, ou utilise la touche physique du Hub lors du premier appairage.</p>` : "";
      return;
    }
    const resultsHtml = candidates
      .map(
        (candidate) => `<div class="card card-pad" style="background:var(--surface-2);box-shadow:none">
          <div class="row-between wrap" style="gap:8px">
            <div>
              <div style="font-weight:700">${escapeHtml(candidate.ip)}</div>
              <div class="tiny muted">${candidate.is_wiser ? "Hub Wiser détecté" : "Appareil du réseau"}${candidate.details ? ` · ${escapeHtml(candidate.details)}` : ""}</div>
            </div>
            <button class="btn btn-sm" type="button" data-use-wiser="${escapeHtml(candidate.ip)}">Utiliser</button>
          </div>
        </div>`
      )
      .join("");
    host.innerHTML = resultsHtml + `<p class="hint" style="margin-top:12px">Pour récupérer la clé du Hub : dans l'application Wiser, va dans <strong>Réglages → Passerelle → Secret</strong>, ou utilise la touche physique du Hub lors du premier appairage. L'adresse IP détectée est à reporter dans le champ « Adresse du Hub », puis enregistre.</p>`;

    host.querySelectorAll("[data-use-wiser]").forEach((useButton, index) => {
      useButton.addEventListener("click", () => {
        const hostInput = root.querySelector('#sec-wiser [data-key="wiser_host"]');
        const secretInput = root.querySelector('#sec-wiser [data-key="wiser_secret"]');
        if (hostInput) {
          hostInput.value = `http://${useButton.dataset.useWiser}`;
          hostInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
        const secret = candidates[index]?.secret;
        if (secretInput && secret) {
          secretInput.value = secret;
          secretInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
        toast("Hub renseigné.", { type: "info" });
        closeSheet();
      });
    });
  } catch (err) {
    host.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
    toastError(err);
  } finally {
    if (button) button.disabled = false;
  }
  return sheet;
}

async function importWiserPrograms(event) {
  const button = event.currentTarget;
  button.disabled = true;
  let created = 0;
  let updated = 0;
  const errors = [];
  try {
    const payload = await get("/api/wiser/schedules");
    const schedules = Array.isArray(payload?.schedules) ? payload.schedules : [];
    if (!schedules.length) {
      toast("Aucun programme Wiser trouvé.", { type: "warn" });
      return;
    }

    let models = [];
    try {
      const mp = await get("/api/models");
      models = Array.isArray(mp?.models) ? mp.models : [];
    } catch {
      models = [];
    }

    for (const schedule of schedules) {
      try {
        const baseName = String(schedule.name || "").trim() || `Programme Wiser ${schedule.schedule_id ?? ""}`.trim();
        const name = `Wiser · ${baseName}`;
        const days = Array.isArray(schedule.days) ? schedule.days : [];
        const normalized = Array.from({ length: 7 }, (_, index) =>
          Array.isArray(days[index]) ? JSON.parse(JSON.stringify(days[index])) : []
        );
        const model = {
          name,
          room: baseName,
          days: {
            A: JSON.parse(JSON.stringify(normalized)),
            B: JSON.parse(JSON.stringify(normalized)),
          },
        };
        const index = models.findIndex(
          (item) => item && String(item.name || "").trim().toLowerCase() === name.toLowerCase()
        );
        if (index >= 0) {
          models[index] = model;
          updated += 1;
        } else {
          models.push(model);
          created += 1;
        }
      } catch (err) {
        errors.push(`${schedule.name || schedule.schedule_id} : ${err && err.message ? err.message : err}`);
      }
    }

    try { const saved = await put("/api/models", { models }); settingsModelsCache = Array.isArray(saved?.models) ? saved.models : models; } catch (err) { toastError(err); }

    if (created || updated) {
      toast(
        `Programmes Wiser importés : ${created} créé(s), ${updated} mis à jour (${schedules.length} au total).`,
        { type: "info" }
      );
    }
    if (errors.length) {
      toast(`Certains programmes n'ont pas pu être lus : ${errors.join(" · ")}`, { type: "warn" });
    }
  } catch (err) {
    toastError(err);
  } finally {
    button.disabled = false;
  }
}


async function searchWeatherCity() {
  const queryNode = root?.querySelector("#weatherQuery");
  const host = root?.querySelector("#weatherResults");
  const button = root?.querySelector("#weatherSearch");
  if (!queryNode || !host || !button) return;
  const value = queryNode.value.trim();
  if (!value) {
    queryNode.focus();
    return;
  }

  button.disabled = true;
  host.innerHTML = `<div class="boot" style="min-height:90px"><span class="spinner"></span><p>Recherche…</p></div>`;
  try {
    const payload = await get(`/api/weather/search?q=${encodeURIComponent(value)}`);
    const results = payload?.results || [];
    if (!results.length) {
      host.innerHTML = `<p class="hint">Aucune ville trouvée.</p>`;
      return;
    }
    host.innerHTML = results
      .map(
        (item) => `<div class="list-row">
          <div class="li-main">
            <div class="li-title">${escapeHtml(item.name)}</div>
            <div class="li-sub">${escapeHtml([item.region, item.country].filter(Boolean).join(" · "))}</div>
          </div>
          <div class="li-actions">
            <button class="btn btn-sm" type="button" data-use-city="${escapeHtml(String(item.latitude))},${escapeHtml(String(item.longitude))}" data-city-name="${escapeHtml(item.name)}">${icon("check", 14)} Choisir</button>
          </div>
        </div>`
      )
      .join("");

    host.querySelectorAll("[data-use-city]").forEach((useButton) => {
      useButton.addEventListener("click", () => {
        const [lat, lon] = useButton.dataset.useCity.split(",");
        const latInput = root.querySelector('#sec-weather [data-key="weather_latitude"]');
        const lonInput = root.querySelector('#sec-weather [data-key="weather_longitude"]');
        const cityInput = root.querySelector('#sec-weather [data-key="weather_city"]');
        if (latInput) {
          latInput.value = lat;
          latInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
        if (lonInput) {
          lonInput.value = lon;
          lonInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
        if (cityInput && useButton.dataset.cityName) {
          cityInput.value = useButton.dataset.cityName;
          cityInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
        toast("Ville et coordonnées remplies.", { type: "info" });
      });
    });
  } catch (err) {
    host.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
    toastError(err);
  } finally {
    button.disabled = false;
  }
}

async function searchPronote() {
  const queryNode = root?.querySelector("#pronoteQuery");
  const host = root?.querySelector("#pronoteResults");
  const button = root?.querySelector("#pronoteSearch");
  if (!queryNode || !host || !button) return;
  const value = queryNode.value.trim();
  if (!value) {
    queryNode.focus();
    return;
  }

  button.disabled = true;
  host.innerHTML = `<div class="boot" style="min-height:90px"><span class="spinner"></span><p>Recherche…</p></div>`;
  try {
    const payload = await post("/api/pronote/search", { query: value, limit: 8 });
    pronoteResults = payload.results || [];
    if (!pronoteResults.length) {
      host.innerHTML = `<p class="hint">Aucun établissement trouvé.</p>`;
      return;
    }
    host.innerHTML = pronoteResults
      .map(
        (item) => `<div class="list-row">
          <div class="li-main">
            <div class="li-title">${escapeHtml(item.name || item.uai)}</div>
            <div class="li-sub">${escapeHtml([item.type, item.postal_code, item.city].filter(Boolean).join(" · "))}</div>
          </div>
          <div class="li-actions">
            <button class="btn btn-sm" type="button" data-use-school="${escapeHtml(item.uai)}">Choisir</button>
          </div>
        </div>`
      )
      .join("");

    host.querySelectorAll("[data-use-school]").forEach((useButton) => {
      useButton.addEventListener("click", () => {
        const school = pronoteResults.find((item) => item.uai === useButton.dataset.useSchool);
        if (!school) return;
        const account = root.querySelector('#sec-pronote [data-key="pronote_account"]')?.value || "student";
        const url = account === "parent" ? school.url_parent : school.url_student;
        const urlInput = root.querySelector('#sec-pronote [data-key="pronote_url"]');
        if (urlInput) urlInput.value = url || "";
        toast(`Adresse de ${school.name} renseignée.`, { type: "info" });
      });
    });
  } catch (err) {
    host.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
    toastError(err);
  } finally {
    button.disabled = false;
  }
}

/* --------------------------------------------------------------- semaines */

async function loadWeeks() {
  const host = root?.querySelector("#weeksList");
  if (!host) return;
  try {
    weeks = (await get("/api/weeks")) || {};
    renderWeeks();
  } catch (err) {
    host.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
  }
}

function renderWeeks() {
  const host = root?.querySelector("#weeksList");
  if (!host) return;

  const currentMonday = mondayOf(new Date());
  const rows = Array.from({ length: 8 }, (_, index) => {
    const monday = mondayOf(addDays(currentMonday, index * 7));
    const value = weeks[monday] || "";
    const isCurrent = index === 0;
    return `<div class="settings-row">
      <div class="sr-main">
        <div class="sr-title">${escapeHtml(fmtDate(monday))} → ${escapeHtml(fmtDate(addDays(monday, 6)))}${isCurrent ? ' <span class="pill pill-accent" style="margin-left:6px">cette semaine</span>' : ""}</div>
        <div class="sr-sub">${escapeHtml(weekdayLong(0))}${value ? ` · forcée en ${value}` : " · automatique"}</div>
      </div>
      <div class="sr-control">
        <select class="select" data-week-monday="${monday}" style="width:150px">
          <option value=""${value === "" ? " selected" : ""}>Automatique</option>
          <option value="A"${value === "A" ? " selected" : ""}>Semaine A</option>
          <option value="B"${value === "B" ? " selected" : ""}>Semaine B</option>
        </select>
      </div>
    </div>`;
  });

  host.innerHTML = rows.join("");

  host.querySelectorAll("[data-week-monday]").forEach((select) => {
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        const payload = await put("/api/weeks", {
          monday: select.dataset.weekMonday,
          week: select.value || null,
        });
        weeks = payload.overrides || {};
        await refreshState({ silent: true });
        renderWeeks();
        toast("Semaine mise à jour.");
      } catch (err) {
        toastError(err);
        select.disabled = false;
      }
    });
  });
}

/* --------------------------------------------------------------- pièces */

function renderRooms() {
  const host = root?.querySelector("#roomsList");
  if (!host) return;
  const state = store.state;
  if (!state) {
    host.innerHTML = `<div class="skeleton" style="height:80px"></div>`;
    return;
  }

  const rooms = [...(state.rooms || [])].sort((a, b) => Number(a.position ?? 0) - Number(b.position ?? 0));
  if (!rooms.length) {
    host.innerHTML = `<div class="empty" style="padding:22px 12px">
      <span class="empty-icon">${icon("house", 22)}</span>
      <strong>Aucune pièce</strong>
      <p>Ajoute une pièce, puis affecte-lui des appareils.</p>
    </div>`;
    return;
  }

  host.innerHTML = rooms
    .map((room) => {
      const devices = (state.devices || []).filter((device) => Number(device.room_id) === Number(room.id));
      const thermostat = devices.find((d) => Number(d.device_id) === Number(room.thermostat_device_id));
      const actuator = devices.find((d) => Number(d.device_id) === Number(room.actuator_device_id));
      return `<div class="settings-row">
        <button class="icon-btn room-drag" type="button" data-drag-room="${room.id}" aria-label="Déplacer ${escapeHtml(room.name)}" title="Glisser pour réordonner" draggable="false"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>
        <div class="sr-main">
          <div class="sr-title">${escapeHtml(room.name)}</div>
          <div class="sr-sub">
            ${devices.length} appareil${devices.length > 1 ? "s" : ""}
            ${thermostat ? ` · thermostat ${escapeHtml(thermostat.name)}` : ""}
            ${actuator ? ` · actionneur ${escapeHtml(actuator.name)}` : ""}
          </div>
        </div>
        <div class="sr-control">
          <button class="icon-btn" type="button" data-edit-room="${room.id}" aria-label="Modifier ${escapeHtml(room.name)}">${icon("edit", 18)}</button>
          <button class="icon-btn" type="button" data-delete-room="${room.id}" aria-label="Supprimer ${escapeHtml(room.name)}">${icon("trash", 18)}</button>
        </div>
      </div>`;
    })
    .join("");

  host.querySelectorAll("[data-edit-room]").forEach((button) => {
    button.addEventListener("click", () => {
      const room = (store.state?.rooms || []).find((item) => Number(item.id) === Number(button.dataset.editRoom));
      if (room) openRoomEditor(room);
    });
  });

  host.querySelectorAll("[data-delete-room]").forEach((button) => {
    button.addEventListener("click", async () => {
      const room = (store.state?.rooms || []).find((item) => Number(item.id) === Number(button.dataset.deleteRoom));
      if (!room || !confirm(`Supprimer la pièce « ${room.name} » ? Les appareils seront détachés, pas supprimés.`)) return;
      try {
        await del(`/api/rooms/${room.id}`);
        await refreshState({ silent: true });
        renderRooms();
        renderDevices();
        toast("Pièce supprimée.");
      } catch (err) {
        toastError(err);
      }
    });
  });

  bindRoomDrag(host);
}

function bindRoomDrag(host) {
  host.querySelectorAll("[data-drag-room]").forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      if (event.button != null && event.button !== 0) return;
      const row = handle.closest(".settings-row");
      if (!row) return;
      event.preventDefault();

      const rows = [...host.querySelectorAll(".settings-row")];
      const fromIndex = rows.indexOf(row);
      if (fromIndex < 0) return;

      const rects = rows.map((item) => item.getBoundingClientRect());
      const centers = rects.map((rect) => rect.top + rect.height / 2);
      const rowHeight = rects[fromIndex].height;
      const startY = event.clientY;
      let targetIndex = fromIndex;
      let moved = false;

      row.classList.add("is-dragging");
      try {
        handle.setPointerCapture(event.pointerId);
      } catch {
        /* Les événements synthétiques n'ont pas de capture de pointeur. */
      }

      const paint = (dy) => {
        row.style.transform = `translateY(${dy}px)`;
        rows.forEach((item, index) => {
          if (index === fromIndex) return;
          if (fromIndex < targetIndex && index > fromIndex && index <= targetIndex) {
            item.style.transform = `translateY(${-rowHeight}px)`;
          } else if (targetIndex < fromIndex && index >= targetIndex && index < fromIndex) {
            item.style.transform = `translateY(${rowHeight}px)`;
          } else {
            item.style.transform = "";
          }
        });
      };

      const restore = () => {
        rows.forEach((item) => {
          item.style.transform = "";
        });
        row.classList.remove("is-dragging");
      };

      const finish = () => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("pointercancel", onCancel);
      };

      const onMove = (moveEvent) => {
        if (moveEvent.pointerId !== event.pointerId) return;
        const dy = moveEvent.clientY - startY;
        if (!moved) {
          if (Math.abs(dy) < 4) return;
          moved = true;
        }
        moveEvent.preventDefault();

        let best = Infinity;
        centers.forEach((center, index) => {
          const distance = Math.abs(moveEvent.clientY - center);
          if (distance < best) {
            best = distance;
            targetIndex = index;
          }
        });
        paint(dy);
      };

      const onUp = async (upEvent) => {
        if (upEvent.pointerId !== event.pointerId) return;
        finish();
        restore();
        if (!moved || targetIndex === fromIndex) {
          renderRooms();
          return;
        }

        const ordered = [...rows];
        ordered.splice(fromIndex, 1);
        ordered.splice(targetIndex, 0, row);
        ordered.forEach((item) => host.appendChild(item));

        const updates = [];
        ordered.forEach((item, index) => {
          if (rows.indexOf(item) === index) return;
          const id = item.querySelector("[data-drag-room]")?.dataset.dragRoom;
          if (id != null) updates.push({ id, position: index });
        });

        try {
          for (const update of updates) {
            await put(`/api/rooms/${update.id}`, { position: update.position });
          }
          await refreshState({ silent: true });
          renderRooms();
          toast("Ordre des pièces enregistré.");
        } catch (err) {
          toastError(err);
          renderRooms();
        }
      };

      const onCancel = (cancelEvent) => {
        if (cancelEvent.pointerId !== event.pointerId) return;
        finish();
        restore();
        renderRooms();
      };

      document.addEventListener("pointermove", onMove, { passive: false });
      document.addEventListener("pointerup", onUp);
      document.addEventListener("pointercancel", onCancel);
    });
  });
}

let settingsModelsCache = [];
let settingsLinksCache = {};
async function loadSettingsModels() {
  try {
    const payload = await get("/api/models");
    settingsModelsCache = Array.isArray(payload?.models) ? payload.models : [];
    settingsLinksCache =
      payload?.links && typeof payload.links === "object" ? payload.links : {};
  } catch {
    settingsModelsCache = [];
    settingsLinksCache = {};
  }
  return settingsModelsCache;
}
function modelOptionsSettings(selectedId = "") {
  const saved = Array.isArray(settingsModelsCache) ? settingsModelsCache : [];
  return saved
    .map(
      (m) =>
        `<option value="${escapeHtml(String(m.id || ""))}"${
          String(m.id) === String(selectedId) ? " selected" : ""
        }>${escapeHtml(m.name)}</option>`
    )
    .join("");
}


async function openRoomEditor(room) {
  await loadSettingsModels();
  // Snapshot enrichi : heating_curve porte la table et l'état d'apprentissage.
  room = room
    ? (store.state?.rooms || []).find((item) => Number(item.id) === Number(room.id)) || room
    : null;
  const hc = room?.heating_curve;
  const hcEnabled = hc === true || hc?.enabled === true;
  const preheatEnabled = room?.preheat === true;
  const hcInfo = hc && typeof hc === "object" ? hc : {};
  const hcTable = Array.isArray(hcInfo.table) ? hcInfo.table : [];
  const hcWeatherHint = hcInfo.t_out != null
    ? `🌡 T extérieure utilisée : ${fmtTemp(hcInfo.t_out)} °C${hcInfo.t_out_fallback ? " · apprentissage en repli (10 °C)" : ""}`
    : "🌡 T extérieure indisponible — τ non corrigé par la météo";
  const devices = store.state?.devices || [];
  const roomLink = room ? settingsLinksCache[String(room.id)] : null;
  const linkedModel = roomLink
    ? settingsModelsCache.find(
        (item) => String(item.id) === String(roomLink.model_id)
      ) || null
    : null;
  const options = (selected) =>
    `<option value="">Aucun</option>${devices
      .map(
        (device) =>
          `<option value="${device.device_id}"${Number(selected) === Number(device.device_id) ? " selected" : ""}>${escapeHtml(device.name)}${device.room_id ? "" : " (sans pièce)"}</option>`
      )
      .join("")}`;

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <label class="field">
      <span class="label">Nom de la pièce</span>
      <input class="input" data-name value="${escapeHtml(room?.name || "")}" placeholder="Salon, Chambre…">
    </label>
    <div class="form-grid">
      <label class="field">
        <span class="label">Thermostat</span>
        <select class="select" data-thermostat>${options(room?.thermostat_device_id)}</select>
      </label>
      <label class="field">
        <span class="label">Actionneur</span>
        <select class="select" data-actuator>${options(room?.actuator_device_id)}</select>
      </label>
    </div>
    <p class="hint">Le thermostat mesure la température (appareil Atlantic ou capteur dédié), l'actionneur chauffe ou refroidit. Sur Wiser, la zone fait les deux.</p>
    ${room ? `
    ${row(
      "Courbe de chauffe",
      "Avance à la consigne, calculée depuis l'historique",
      `<label class="switch" title="Courbe de chauffe"><input type="checkbox" data-heating-curve${hcEnabled ? " checked" : ""}><span class="track"></span></label>`
    )}
    ${row(
      "Préchauffage intelligent",
      "Anticipe la montée de consigne avec la météo. Utilise la courbe si elle est activée, sinon un modèle par défaut.",
      `<label class="switch" title="Préchauffage intelligent"><input type="checkbox" data-preheat${preheatEnabled ? " checked" : ""}><span class="track"></span></label>`
    )}
    ${hcEnabled ? `
    <details class="stack">
      <summary class="card-title summary-row">Table d'avance (temps avant la consigne)</summary>
      ${hcInfo.learning
        ? '<p class="hint" style="font-style:italic">Apprentissage en cours — valeurs par défaut (τ=3 h)</p>'
        : `<p class="hint">Modèle appris sur ${hcInfo.n ?? 0} montées (R²=${fmtTemp(hcInfo.r2, 2)}) · recalcul quotidien</p>`}
      <p class="hint">${hcWeatherHint}</p>
      <table class="stats-table">
        <thead><tr><th>Température de la pièce</th><th>Avance</th></tr></thead>
        <tbody>
          ${hcTable
            .map(([temp, minutes]) => `<tr><td>${fmtTemp(temp)} °C</td><td>${Math.round(Number(minutes))} min</td></tr>`)
            .join("")}
        </tbody>
      </table>
    </details>` : ""}
    <label class="field">
      <span class="label">Modèle de semaine</span>
      <div class="input-row">
        <select class="select" data-model style="flex:1"${linkedModel ? " disabled" : ""}>
          <option value="">— choisir un modèle —</option>
          ${modelOptionsSettings(linkedModel?.id)}
        </select>
        ${
          linkedModel
            ? `<button class="btn btn-sm btn-ghost" type="button" data-unlink-model>${icon("unlink", 14)} Délier</button>`
            : `<button class="btn btn-sm" type="button" data-link-model>${icon("link", 14)} Lier</button>`
        }
      </div>
      ${
        linkedModel
          ? `<span class="hint"><span class="pill ${roomLink?.pending ? "pill-warn" : "pill-ok"}">${roomLink?.pending ? "À synchroniser" : "Synchronisée"}</span> « ${escapeHtml(linkedModel.name)} » est lié : le planning de la pièce suit chaque enregistrement du modèle. Délier conserve le planning actuel.</span>`
          : '<span class="hint">Lier copie immédiatement le planning du modèle (semaines A et B), puis le maintient synchronisé à chaque enregistrement.</span>'
      }
    </label>` : ""}
  `;

  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%";
  footer.innerHTML = `
    ${room ? `<button class="btn btn-danger" type="button" data-delete>${icon("trash", 16)} Supprimer</button>` : ""}
    <button class="btn btn-primary" type="button" data-validate>${icon("check", 16)} ${room ? "Mettre à jour" : "Créer"}</button>
  `;

  openSheet({
    title: room ? "Modifier la pièce" : "Nouvelle pièce",
    sub: room ? `Pièce n°${room.id}` : "Elle regroupera un thermostat et un actionneur",
    body,
    footer,
  });

  if (room) {
    const hcToggle = body.querySelector("[data-heating-curve]");
    hcToggle?.addEventListener("change", async () => {
      hcToggle.disabled = true;
      try {
        await put(`/api/rooms/${room.id}`, { heating_curve: hcToggle.checked });
        await refreshState({ silent: true });
        // Re-rendre l'éditeur pour afficher/masquer la table avec les données fraîches.
        await openRoomEditor(room);
      } catch (err) {
        hcToggle.checked = !hcToggle.checked;
        hcToggle.disabled = false;
        toastError(err);
      }
    });

    const preheatToggle = body.querySelector("[data-preheat]");
    preheatToggle?.addEventListener("change", async () => {
      preheatToggle.disabled = true;
      try {
        await put(`/api/rooms/${room.id}`, { preheat: preheatToggle.checked });
        await refreshState({ silent: true });
        await openRoomEditor(room);
      } catch (err) {
        preheatToggle.checked = !preheatToggle.checked;
        preheatToggle.disabled = false;
        toastError(err);
      }
    });

    const linkBtn = body.querySelector("[data-link-model]");
    linkBtn?.addEventListener("click", async () => {
      const select = body.querySelector("[data-model]");
      const modelId = String(select?.value || "");
      if (!modelId) {
        toast("Choisis un modèle.", { type: "warn" });
        return;
      }
      const m = settingsModelsCache.find(
        (item) => String(item.id) === modelId
      );
      if (!m) {
        toast("Modèle introuvable.", { type: "warn" });
        return;
      }
      if (
        !confirm(
          `Lier la pièce « ${room.name} » au modèle « ${m.name} » ?\nLe planning de la pièce est remplacé par le modèle et suivra ensuite ses modifications.`
        )
      ) {
        return;
      }
      linkBtn.disabled = true;
      try {
        await put(`/api/model-links/${room.id}`, { model_id: modelId });
        await refreshState({ silent: true });
        await openRoomEditor(room);
        renderRooms();
        toast(`Pièce liée au modèle « ${m.name} ».`);
      } catch (err) {
        toastError(err);
        linkBtn.disabled = false;
      }
    });

    body.querySelector("[data-unlink-model]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      if (!confirm(`Délier la pièce « ${room.name} » de son modèle ?\nSon planning actuel est conservé.`)) {
        return;
      }
      button.disabled = true;
      try {
        await del(`/api/model-links/${room.id}`);
        await refreshState({ silent: true });
        await openRoomEditor(room);
        renderRooms();
        toast("Pièce déliée du modèle (planning conservé).", { type: "info" });
      } catch (err) {
        toastError(err);
        button.disabled = false;
      }
    });
  }

  const value = (selector) => body.querySelector(selector)?.value ?? "";
  const val = (selector) => body.querySelector(selector);

  val("[data-device-type]")?.addEventListener("change", () => {
    const t = val("[data-device-type]").value;
    const minInput = val("[data-temp-min]");
    const maxInput = val("[data-temp-max]");
    if (!minInput || !maxInput) return;
    if (t === "wiser") {
      minInput.value = 0;
      maxInput.value = 30;
    } else {
      minInput.value = 16;
      maxInput.value = 30;
    }
  });

  footer.querySelector("[data-validate]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const payload = {
      name: value("[data-name]").trim(),
      thermostat_device_id: value("[data-thermostat]") || null,
      actuator_device_id: value("[data-actuator]") || null,
    };
    try {
      if (room) await put(`/api/rooms/${room.id}`, payload);
      else {
        const created = await post("/api/rooms", { name: payload.name });
        await put(`/api/rooms/${created.room.id}`, payload);
      }
      await refreshState({ silent: true });
      closeSheet();
      renderRooms();
      renderDevices();
      toast(room ? "Pièce mise à jour." : "Pièce créée.");
    } catch (err) {
      toastError(err);
      button.disabled = false;
    }
  });

  footer.querySelector("[data-delete]")?.addEventListener("click", async () => {
    if (!room || !confirm(`Supprimer la pièce « ${room.name} » ?`)) return;
    try {
      await del(`/api/rooms/${room.id}`);
      await refreshState({ silent: true });
      closeSheet();
      renderRooms();
      renderDevices();
      toast("Pièce supprimée.");
    } catch (err) {
      toastError(err);
    }
  });

}

/* --------------------------------------------------------------- appareils */

function renderDevices() {
  const atlanticHost = root?.querySelector("#atlanticDevicesList");
  const wiserHost = root?.querySelector("#wiserDevicesList");
  if (!wiserHost) return;
  const state = store.state;
  if (!state) {
    if (atlanticHost) atlanticHost.innerHTML = `<div class="skeleton" style="height:60px"></div>`;
    wiserHost.innerHTML = `<div class="skeleton" style="height:60px"></div>`;
    return;
  }

  const devices = [...(state.devices || [])].sort(
    (a, b) => Number(a.position ?? 0) - Number(b.position ?? 0) || Number(a.device_id) - Number(b.device_id)
  );

  const roomName = (id) => (state.rooms || []).find((room) => Number(room.id) === Number(id))?.name;

  const renderOne = (host, list, emptyText) => {
    if (!list.length) {
      host.innerHTML = `<div class="empty" style="padding:14px 12px">
        <span class="empty-icon">${icon("thermometer", 20)}</span>
        <strong>Aucun appareil</strong>
        <p>${emptyText}</p>
      </div>`;
      return;
    }
    host.innerHTML = list
      .map((device) => {
        const isWiser = device.device_type === "wiser";
        const isOn = Number(device.enabled) !== 0;
        const room = roomName(device.room_id) || "Sans pièce";
        const ambient = device.current?.ambient ?? device.current?.wiser_ambient;
        const humidity = device.current?.humidity ?? device.current?.wiser_humidity;
        const humidityLabel =
          humidity !== null &&
          humidity !== undefined &&
          humidity !== "" &&
          Number(humidity) > 0
            ? ` · ${Math.round(Number(humidity))} %`
            : "";
        const tempMin = device.temp_min ?? (isWiser ? 0 : 16);
        const tempMax = device.temp_max ?? 30;
        const frostTemp = device.frost_temp ?? (isWiser ? 10 : 16);
        const frostMode = isWiser
          ? "Hors gel"
          : modeLabel(device.frost_mode || "heat", store.meta);
        const sensor = !isWiser && device.temp_sensor_device_id
          ? (state.devices || []).find(
              (item) =>
                Number(item.device_id) === Number(device.temp_sensor_device_id)
            )
          : null;
        const status = !isOn
          ? { label: "Désactivé", cls: "pill-ghost" }
          : device.current?.available === false
            ? { label: "Hors ligne", cls: "pill-warn" }
            : { label: "Actif", cls: "pill-ok" };

        const meta = (label, value) => `
          <div class="device-meta">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
          </div>`;

        return `
          <details class="sub-section device-section"${isOn ? "" : " data-off"}>
            <summary class="device-summary">
              <span class="device-summary-title">
                ${icon(isWiser ? "hub" : "fan", 16)}
                <span class="device-name">${escapeHtml(device.name)}</span>
                <span class="pill ${isWiser ? "pill-accent" : "pill-ghost"}">${isWiser ? "Wiser" : "Atlantic"}</span>
              </span>
              <span class="device-summary-meta">
                ${escapeHtml(room)} · ${ambient == null ? "—" : `${fmtTemp(ambient)} °C${humidityLabel}`}
                <span class="pill ${status.cls}">${status.label}</span>
              </span>
            </summary>
            <div class="device-body">
              <div class="device-grid">
                ${meta("Pièce", room)}
                ${meta("Température", ambient == null ? "—" : `${fmtTemp(ambient)} °C${humidityLabel}`)}
                ${meta("Plage", `${fmtTemp(tempMin)}–${fmtTemp(tempMax)} °C`)}
                ${meta("Hors gel", `${fmtTemp(frostTemp)} °C (${frostMode})`)}
                ${isWiser
                  ? meta("Zone Wiser", String(device.wiser_room_id ?? "—"))
                  : meta("Capteur dédié", sensor?.name || "Aucun (sonde de l'appareil)")}
                ${meta("Identifiant", String(device.device_id))}
              </div>
              <div class="device-actions">
                <button class="btn btn-sm" type="button" data-edit-device="${device.device_id}" aria-label="Modifier ${escapeHtml(device.name)}">${icon("edit", 15)} Modifier</button>
                <button class="btn btn-sm btn-danger" type="button" data-delete-device="${device.device_id}" aria-label="Retirer ${escapeHtml(device.name)}">${icon("trash", 15)} Retirer</button>
              </div>
            </div>
          </details>`;
      })
      .join("");

    host.querySelectorAll("[data-edit-device]").forEach((button) => {
      button.addEventListener("click", () => {
        const device = (store.state?.devices || []).find((item) => Number(item.device_id) === Number(button.dataset.editDevice));
        if (device) openDeviceEditor(device);
      });
    });

    host.querySelectorAll("[data-delete-device]").forEach((button) => {
      button.addEventListener("click", async () => {
        const id = Number(button.dataset.deleteDevice);
        const device = (store.state?.devices || []).find((item) => Number(item.device_id) === id);
        if (!device || !confirm(`Retirer « ${device.name} » ?`)) return;
        button.disabled = true;
        try {
          await del(`/api/devices/${id}`);
          await refreshState({ silent: true });
          renderDevices();
          toast("Appareil retiré.");
        } catch (err) {
          toastError(err);
        } finally {
          button.disabled = false;
        }
      });
    });
  };

  const atlanticDevices = devices.filter((d) => d.device_type !== "wiser");
  const wiserDevices = devices.filter((d) => d.device_type === "wiser");

  if (atlanticHost) {
    renderOne(atlanticHost, atlanticDevices, "Synchronise le compte Atlantic pour découvrir les appareils.");
  }
  renderOne(wiserHost, wiserDevices, "Configure le Hub Wiser puis synchronise pour découvrir les zones.");

  // État des interrupteurs de marque (le clic est branché une seule fois
  // dans bind(), donc indépendant de l'arrivée de l'état).
  const atlanticSwitch = root?.querySelector('[data-brand-toggle="atlantic"]');
  const wiserSwitch = root?.querySelector('[data-brand-toggle="wiser"]');
  if (atlanticSwitch) {
    atlanticSwitch.checked =
      atlanticDevices.length > 0 &&
      atlanticDevices.every((d) => Number(d.enabled) !== 0);
  }
  if (wiserSwitch) {
    wiserSwitch.checked =
      wiserDevices.length > 0 && wiserDevices.every((d) => Number(d.enabled) !== 0);
  }
  syncBrandLocks();
}

/** Ferme une marque/un système : ses réglages (URL, identifiants…) ne sont plus accessibles. */
function setPanelLocked(details, locked) {
  if (!details) return;
  details.classList.toggle("is-locked", locked);
  for (const child of details.children) {
    if (child.tagName === "SUMMARY") continue;
    child.inert = locked;
  }
}

function syncBrandLocks() {
  const devices = store.state?.devices || [];
  const allOff = (list) => list.length > 0 && list.every((d) => Number(d.enabled) === 0);
  setPanelLocked(
    root?.querySelector("#sec-atlantic")?.closest("details"),
    allOff(devices.filter((d) => d.device_type !== "wiser"))
  );
  setPanelLocked(
    root?.querySelector("#sec-wiser")?.closest("details"),
    allOff(devices.filter((d) => d.device_type === "wiser"))
  );
  for (const item of store.meta?.connectors || []) {
    const values = (store.config?.connectors || {})[item.id] || {};
    const off = String(values.enabled ?? "1") === "0";
    setPanelLocked(
      root?.querySelector(`[data-connector-devices="${item.id}"]`)?.closest("details"),
      off
    );
  }
}


function openDeviceEditor(device) {
  const rooms = store.state?.rooms || [];
  const devices = store.state?.devices || [];
  const roomOptions = `<option value="">Sans pièce</option>${rooms
    .map(
      (room) =>
        `<option value="${room.id}"${Number(device.room_id) === Number(room.id) ? " selected" : ""}>${escapeHtml(room.name)}</option>`
    )
    .join("")}`;
  const sensorOptions = `<option value="0">Aucun capteur dédié</option>${devices
    .filter((item) => Number(item.device_id) !== Number(device.device_id))
    .map(
      (item) =>
        `<option value="${item.device_id}"${Number(device.temp_sensor_device_id) === Number(item.device_id) ? " selected" : ""}>${escapeHtml(item.name)}</option>`
    )
    .join("")}`;
  const frostMode = device.frost_mode || "heat";
  const frostModeOptions = modeOptions()
    .map(
      ([value, label]) =>
        `<option value="${escapeHtml(value)}"${value === frostMode ? " selected" : ""}>${escapeHtml(label)}</option>`
    )
    .join("");

  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <label class="field">
      <span class="label">Nom affiché</span>
      <input class="input" data-name value="${escapeHtml(device.name || "")}">
    </label>
    <div class="form-grid">
      <label class="field">
        <span class="label">Type</span>
        <select class="select" data-device-type>
          <option value="atlantic"${device.device_type !== "wiser" ? " selected" : ""}>Atlantic / Cozytouch</option>
          <option value="wiser"${device.device_type === "wiser" ? " selected" : ""}>Wiser</option>
        </select>
      </label>
      <label class="field">
        <span class="label">Pièce</span>
        <select class="select" data-room>${roomOptions}</select>
      </label>
      <label class="field">
        <span class="label">Ordre d'affichage <span class="opt">0 = premier</span></span>
        <input class="input" data-position type="number" min="0" value="${escapeHtml(device.position ?? 0)}">
      </label>
      <label class="field">
        <span class="label">Zone Wiser <span class="opt">0 si non Wiser</span></span>
        <input class="input" data-wiser-room type="number" min="0" value="${escapeHtml(device.wiser_room_id ?? 0)}">
      </label>
      <label class="field">
        <span class="label">Temp. min (°C)</span>
        <input class="input" data-temp-min type="number" min="-10" max="30" step="0.5" value="${escapeHtml(device.temp_min ?? (device.device_type === "wiser" ? 0 : 16))}">
      </label>
      <label class="field">
        <span class="label">Temp. max (°C)</span>
        <input class="input" data-temp-max type="number" min="0" max="40" step="0.5" value="${escapeHtml(device.temp_max ?? 30)}">
      </label>
    </div>
    <details open>
      <summary class="label" style="cursor:pointer">Hors gel</summary>
      <div class="form-grid mt-2">
        <label class="field">
          <span class="label">Température hors gel (°C)</span>
          <input class="input" data-frost-temp type="number" step="0.5" min="0" value="${escapeHtml(device.frost_temp ?? (device.device_type === "wiser" ? 10 : 16))}">
        </label>
        ${device.device_type !== "wiser" ? `<label class="field">
          <span class="label">Mode hors gel</span>
          <select class="select" data-frost-mode>${frostModeOptions}</select>
        </label>` : ""}
      </div>
    </details>
    ${row("Pilotage autorisé", "Désactive pour ignorer cet appareil sans le retirer.", `<label class="switch"><input type="checkbox" data-enabled${Number(device.enabled) !== 0 ? " checked" : ""}><span class="track"></span></label>`)}
    <label class="field">
      <span class="label">Capteur de température <span class="opt">optionnel</span></span>
      <select class="select" data-temp-sensor>${sensorOptions}</select>
    </label>
    <details>
      <summary class="tiny muted" style="cursor:pointer">Capacités techniques (avancé)</summary>
      <div class="form-grid mt-2">
        <label class="field"><span class="label">cap_mode</span><input class="input" data-cap-mode type="number" value="${escapeHtml(device.cap_mode ?? 7)}"></label>
        <label class="field"><span class="label">cap_setpoint</span><input class="input" data-cap-setpoint type="number" value="${escapeHtml(device.cap_setpoint ?? 17)}"></label>
        <label class="field"><span class="label">cap_setpoint_cool</span><input class="input" data-cap-setpoint-cool type="number" value="${escapeHtml(device.cap_setpoint_cool ?? 0)}"></label>
        <label class="field"><span class="label">cap_ambient</span><input class="input" data-cap-ambient type="number" value="${escapeHtml(device.cap_ambient ?? 40)}"></label>
        <label class="field"><span class="label">cap_prog</span><input class="input" data-cap-prog type="number" value="${escapeHtml(device.cap_prog ?? 0)}"></label>
      </div>
    </details>
    <p class="hint">Identifiant technique : ${escapeHtml(device.device_id)}</p>
  `;

  const footer = document.createElement("div");
  footer.style.cssText = "display:flex;gap:8px;width:100%";
  footer.innerHTML = `<button class="btn btn-primary" type="button" data-validate>${icon("check", 16)} Mettre à jour</button>`;

  openSheet({
    title: "Modifier l'appareil",
    sub: device.name,
    body,
    footer,
  });

  footer.querySelector("[data-validate]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const val = (selector) => body.querySelector(selector);
    const number = (selector) => Number(val(selector)?.value || 0);

    try {
      const payload = {
        name: val("[data-name]").value.trim(),
        device_type: val("[data-device-type]").value,
        room_id: val("[data-room]").value || null,
        position: number("[data-position]"),
        wiser_room_id: number("[data-wiser-room]"),
        temp_min: Number(val("[data-temp-min]")?.value || 0),
        temp_max: Number(val("[data-temp-max]")?.value || 0),
        frost_temp: number("[data-frost-temp]"),
        enabled: val("[data-enabled]").checked ? 1 : 0,
        temp_sensor_device_id: number("[data-temp-sensor]"),
        cap_mode: number("[data-cap-mode]"),
        cap_setpoint: number("[data-cap-setpoint]"),
        cap_setpoint_cool: number("[data-cap-setpoint-cool]"),
        cap_ambient: number("[data-cap-ambient]"),
        cap_prog: number("[data-cap-prog]"),
      };
      if (device.device_type !== "wiser") {
        payload.frost_mode = val("[data-frost-mode]")?.value || "heat";
      }
      await put(`/api/devices/${device.device_id}`, payload);
      await refreshState({ silent: true });
      closeSheet();
      renderDevices();
      renderRooms();
      toast("Appareil mis à jour.");
    } catch (err) {
      toastError(err);
      button.disabled = false;
    }
  });
}

/* --------------------------------------------------------------- stockage */

function renderStorage() {
  const host = root?.querySelector("#storageInfo");
  if (!host) return;
  const info = store.state?.storage || store.system?.storage;
  if (!info) {
    host.innerHTML = `<span class="hint">Information indisponible.</span>`;
    return;
  }
  host.innerHTML = `
    <div class="row-between"><span class="tiny muted">Fichier d'état</span><span class="tiny mono">${escapeHtml(info.state_path || "—")} · ${bytes(info.state_size)}</span></div>
    <div class="row-between"><span class="tiny muted">Historique</span><span class="tiny mono">${info.history_files || 0} fichier(s) · ${bytes(info.history_size)}</span></div>
    <div class="row-between"><span class="tiny muted">Écritures depuis le démarrage</span><span class="tiny mono">${info.state_writes ?? 0} état · ${info.history_writes ?? 0} historique</span></div>
    <div class="row-between"><span class="tiny muted">En attente d'écriture</span><span class="tiny mono">${info.pending_history ?? 0} mesure(s)</span></div>
    <div class="row-between"><span class="tiny muted">Agrégats en mémoire</span><span class="tiny mono">${info.history_buckets ?? 0} heure(s)</span></div>
    <div class="row-between"><span class="tiny muted">Mode</span><span class="tiny">${escapeHtml(info.mode || "mémoire + snapshots")}</span></div>
  `;
}

function renderSystem() {
  const host = root?.querySelector("#systemInfo");
  if (!host) return;
  const system = store.system;
  if (!system) {
    host.innerHTML = `<span class="hint">Information indisponible.</span>`;
    return;
  }

  const app = system.app || {};
  const hostInfo = system.host || {};
  const memory = system.memory;
  const disk = system.disk;
  host.innerHTML = `
    <div class="row-between"><span class="tiny muted">Version</span><span class="tiny mono">${escapeHtml(app.version || "—")} · PID ${app.pid ?? "—"}</span></div>
    <div class="row-between"><span class="tiny muted">Démarré depuis</span><span class="tiny mono">${uptime(app.uptime)}</span></div>
    <div class="row-between"><span class="tiny muted">Système</span><span class="tiny mono">${escapeHtml(hostInfo.platform || "—")} ${escapeHtml(hostInfo.release || "")} · Python ${escapeHtml(hostInfo.python || "—")}</span></div>
    <div class="row-between"><span class="tiny muted">Processeur</span><span class="tiny mono">${hostInfo.cpu_count ?? "—"} cœur(s)</span></div>
    ${memory ? `<div class="row-between"><span class="tiny muted">Mémoire</span><span class="tiny mono">${bytes(memory.used)} / ${bytes(memory.total)} · ${memory.percent} %</span></div>` : ""}
    ${system.process_rss ? `<div class="row-between"><span class="tiny muted">Mémoire eCasa</span><span class="tiny mono">${bytes(system.process_rss)}</span></div>` : ""}
    ${disk ? `<div class="row-between"><span class="tiny muted">Carte SD</span><span class="tiny mono">${bytes(disk.free)} libres · ${disk.percent} % utilisé</span></div>` : ""}
  `;
}

/* --------------------------------------------------------------- serveur */

async function exportConfigBackup(event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch("/api/config/backup", {
      headers: { Accept: "application/zip" },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.error || `Erreur ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = /filename="?([^";]+)"?/i.exec(disposition);
    const filename =
      match?.[1] || `ecasa-config-${new Date().toISOString().slice(0, 10)}.zip`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast("Configuration exportée.");
  } catch (err) {
    toastError(err);
  } finally {
    button.disabled = false;
  }
}

async function importConfigBackup(event) {
  const input = event.currentTarget;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (
    !window.confirm(
      "Importer cette configuration ? Les réglages, l'état, l'énergie et l'historique actuels seront remplacés. Le serveur redémarrera automatiquement."
    )
  ) {
    return;
  }
  const button = root?.querySelector("#configImportBtn");
  if (button) button.disabled = true;
  try {
    const data = await fileToBase64(file);
    const response = await fetch("/api/config/restore", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ data }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(payload?.error || `Erreur ${response.status}`);
    }
    if (payload?.restart_required) {
      toast("Configuration importée. Redémarrage en cours…", {
        type: "info",
        timeout: 8000,
      });
      await waitForServer();
      renderRooms();
      renderDevices();
      renderStorage();
      renderSystem();
    } else {
      await refreshState({ silent: true });
      await refreshConfig({ silent: true });
      renderRooms();
      renderDevices();
      renderStorage();
      renderSystem();
      toast("Configuration importée.");
    }
  } catch (err) {
    toastError(err);
  } finally {
    if (button) button.disabled = false;
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => {
      reject(reader.error || new Error("Lecture du fichier impossible."));
    };
    reader.readAsDataURL(file);
  });
}

async function restartServer(button) {
  if (!confirm("Redémarrer eCasa ? L'interface sera indisponible quelques secondes.")) return;
  button.disabled = true;
  try {
    await post("/api/control/restart");
    toast("Redémarrage en cours…", { type: "info", timeout: 8000 });
    await waitForServer();
  } catch (err) {
    toastError(err);
    button.disabled = false;
  }
}

async function stopServer(button) {
  if (!confirm("Arrêter le serveur eCasa ? Tu devras le relancer à la main.")) return;
  button.disabled = true;
  try {
    await post("/api/control/stop");
    toast("Serveur arrêté.", { type: "info", timeout: 8000 });
  } catch (err) {
    toastError(err);
  } finally {
    button.disabled = false;
  }
}

async function waitForServer() {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    try {
      const health = await get("/api/health", { timeout: 4000 });
      if (health?.ok) {
        await refreshState({ silent: true });
        await refreshConfig({ silent: true });
        await refreshSystem({ silent: true });
        toast("eCasa est de retour.");
        return;
      }
    } catch {
      /* le serveur redémarre encore */
    }
  }
  toast("Le serveur ne répond pas. Vérifie le journal système.", { type: "error", timeout: 8000 });
}

/* --------------------------------------------------------------- tailscale */

function connectTailscale() {
  if (tailscale?.running) {
    tailscaleAction("up");
    return;
  }
  openTailscaleConnect();
}

function openTailscaleConnect() {
  const saved = Boolean(tailscale?.authkey_set);
  const serviceUser = String(tailscale?.service_user || "admin");
  const sheet = openSheet({
    title: "Connecter ce nœud",
    sub: "Enregistrement piloté par le serveur eCasa",
    footer: `<div class="row wrap" style="gap:8px">
      <button class="btn btn-primary" type="button" data-ts-save>${icon("wifi", 15)} Enregistrer et connecter</button>
      <button class="btn" type="button" data-ts-link>${icon("external", 15)} Autoriser par lien</button>
    </div>`,
    body: `<div class="stack">
      <p class="hint">eCasa enregistre lui-même ce Raspberry Pi : aucune commande à taper.</p>

      <div class="help-step">
        <div class="help-step-head"><span class="help-step-num">1</span><div>
          <div class="help-step-title">Clé d'authentification (recommandé)</div>
          <div class="help-step-sub">La clé reste enregistrée sur le serveur : la reconnexion est automatique après un redémarrage ou une coupure réseau.</div>
        </div></div>
        <div class="help-faq-a">
          <div class="stack" style="gap:8px">
            <input class="input" type="password" data-ts-key-input autocomplete="off" placeholder="${saved ? "•••••••• (clé déjà enregistrée)" : "tskey-auth-…"}">
            <a class="tiny" href="https://login.tailscale.com/admin/settings/keys" target="_blank" rel="noreferrer">${icon("external", 12)} Créer une clé dans la console Tailscale</a>
          </div>
        </div>
      </div>

      <div class="help-step">
        <div class="help-step-head"><span class="help-step-num">2</span><div>
          <div class="help-step-title">Ou autoriser par lien</div>
          <div class="help-step-sub">eCasa ouvre une session et affiche un lien à valider une seule fois dans ton navigateur, puis il se connecte tout seul.</div>
        </div></div>
      </div>

      <p class="hint">Si un message « droits requis » apparaît, lance une seule fois en SSH : <code>sudo tailscale set --operator=${escapeHtml(serviceUser)}</code></p>
    </div>`,
  });
  sheet?.footer?.querySelector("[data-ts-save]")?.addEventListener("click", () => {
    const input = sheet.body?.querySelector("[data-ts-key-input]");
    const key = String(input?.value || "").trim();
    sheet.close();
    if (!key && !saved) {
      toast("Colle une clé d'authentification, ou choisis « Autoriser par lien ».", {
        type: "error",
        timeout: 8000,
      });
      return;
    }
    tailscaleAction("up", key ? { authkey: key } : undefined);
  });
  sheet?.footer?.querySelector("[data-ts-link]")?.addEventListener("click", () => {
    sheet.close();
    tailscaleAction("up");
  });
}

function openTailscaleDnsHelp() {
  const sheet = openSheet({
    title: "Activer MagicDNS et HTTPS",
    sub: "Réglage du tailnet, à faire une fois dans la console Tailscale",
    footer: `<div class="row wrap" style="gap:8px">
      <a class="btn btn-primary" href="https://login.tailscale.com/admin/dns" target="_blank" rel="noreferrer">${icon("external", 15)} Ouvrir la console → DNS</a>
      <button class="btn" type="button" data-dns-refresh>${icon("refresh", 15)} J'ai activé, actualiser</button>
    </div>`,
    body: `<div class="stack">
      <p class="hint">Ces deux réglages appartiennent à ton <strong>tailnet</strong> (le compte Tailscale), pas à eCasa. Ils ne peuvent pas être activés depuis cette page : il faut passer par la console Tailscale, avec un compte administrateur du tailnet.</p>

      <div class="help-step">
        <div class="help-step-head"><span class="help-step-num">1</span><div>
          <div class="help-step-title">Activer MagicDNS</div>
          <div class="help-step-sub">Permet de résoudre <code>ecasa.&lt;tailnet&gt;.ts.net</code> depuis le téléphone.</div>
        </div></div>
        <div class="help-faq-a">Dans la console → <strong>DNS</strong>, active <strong>MagicDNS</strong>.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head"><span class="help-step-num">2</span><div>
          <div class="help-step-title">Activer HTTPS Certificates</div>
          <div class="help-step-sub">Autorise Tailscale à émettre le certificat du nom <code>ecasa.&lt;tailnet&gt;.ts.net</code>.</div>
        </div></div>
        <div class="help-faq-a">Toujours dans <strong>DNS</strong>, section <strong>HTTPS Certificates</strong> → clique sur <strong>Enable HTTPS</strong> (ou « Activer HTTPS »).</div>
      </div>

      <div class="help-step">
        <div class="help-step-head"><span class="help-step-num">3</span><div>
          <div class="help-step-title">Revenir dans eCasa et actualiser</div>
          <div class="help-step-sub">L'émission du certificat peut prendre 30 secondes.</div>
        </div></div>
        <div class="help-faq-a">Réglages → Tailscale → <strong>Actualiser</strong>. Les lignes <strong>MagicDNS</strong> et <strong>HTTPS Certificates</strong> doivent passer au vert. Publie ensuite avec <strong>Publier</strong>.</div>
      </div>

      <p class="hint">Si tu n'es pas administrateur du tailnet, demande à la personne qui gère le compte Tailscale d'activer ces deux options.</p>
      <p class="hint">Scripts prêts à l'emploi (à lancer en SSH sur le serveur) : <a href="https://raw.githubusercontent.com/shervet/ecasa/main/tools/tailscale-ecasa.sh" target="_blank" rel="noreferrer">tailscale-ecasa.sh</a> · <a href="https://raw.githubusercontent.com/shervet/ecasa/main/tools/tailscale-ecasa.bat" target="_blank" rel="noreferrer">tailscale-ecasa.bat</a></p>
    </div>`,
  });
  sheet?.footer?.querySelector("[data-dns-refresh]")?.addEventListener("click", () => {
    sheet.close();
    loadTailscale();
  });
}

function openTailscaleHelp() {
  const sheet = openSheet({
    title: "Configurer Tailscale",
    sub: "Guide local, étape par étape",
    footer: `<button class="btn btn-primary" type="button" data-help-refresh>${icon("refresh", 15)} Actualiser le diagnostic</button>`,
    body: `<div class="stack">
      <p class="hint">Objectif : que l'app Android ouvre <strong>https://ecasa.&lt;tailnet&gt;.ts.net/</strong> (port 443). Toutes les commandes se lancent <strong>sur la machine qui fait tourner eCasa</strong>. Chaque étape se termine par une vérification : passe à la suivante seulement quand elle est verte.</p>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">1</span>
          <div>
            <div class="help-step-title">Installer Tailscale sur cette machine</div>
            <div class="help-step-sub">Une seule fois. L'installateur installe aussi la commande <code>tailscale</code>.</div>
          </div>
        </div>
        <div><span class="help-os-label">Windows (PowerShell)</span><code class="help-code">winget install -e --id Tailscale.Tailscale</code></div>
        <div><span class="help-os-label">macOS</span><code class="help-code">brew install --cask tailscale</code></div>
        <div><span class="help-os-label">Linux / Raspberry Pi</span><code class="help-code">curl -fsSL https://tailscale.com/install.sh | sudo sh</code></div>
        <div class="help-check"><strong>À vérifier :</strong> <code>tailscale version</code> affiche un numéro de version. Sur Windows, eCasa détecte automatiquement <code>C:\\Program Files\\Tailscale\\tailscale.exe</code> même s'il n'est pas dans le PATH.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">2</span>
          <div>
            <div class="help-step-title">Connecter la machine avec le nom « ecasa »</div>
            <div class="help-step-sub">Depuis eCasa, aucun terminal nécessaire : <strong>Réglages → Tailscale → Connecter</strong>, puis colle une clé d'authentification (conservée localement) ou choisis <strong>Autoriser par lien</strong>. Sinon, en ligne de commande :</div>
          </div>
        </div>
        <div><span class="help-os-label">Linux / macOS</span><code class="help-code">sudo tailscale up --hostname=ecasa</code></div>
        <div><span class="help-os-label">Windows</span><code class="help-code">tailscale up --hostname=ecasa</code></div>
        <div class="help-check"><strong>À vérifier :</strong> <code>tailscale status</code> montre une ligne avec <code>ecasa</code> et une adresse <code>100.x.y.z</code>. <code>tailscale ip -4</code> renvoie cette même adresse.</div>
        <div class="help-warn"><strong>« access denied » depuis l'interface :</strong> le service eCasa tourne sans privilèges. Autorise une seule fois son compte : <code>sudo tailscale set --operator=&lt;compte&gt;</code> (<code>install-pi.sh</code> le fait automatiquement).</div>
        <div class="help-warn"><strong>Nom déjà pris :</strong> si le nœud s'appelle <code>ecasa-1</code>, le nom <code>ecasa</code> est réservé ailleurs (autre machine ou <strong>Service Tailscale</strong>). La commande réussit sans rien changer : libère le nom dans la console → Machines (supprimer l'ancien nœud) ou → Services (supprimer le service), puis relance-la.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">3</span>
          <div>
            <div class="help-step-title">Activer MagicDNS et HTTPS dans la console Tailscale</div>
            <div class="help-step-sub">Réglage du tailnet, une seule fois. Sans HTTPS Certificates, <code>tailscale serve</code> ne peut pas créer le certificat de l'app.</div>
          </div>
        </div>
        <div class="help-faq-a">Ouvre <a href="https://login.tailscale.com/admin/dns" target="_blank" rel="noreferrer">console → DNS</a>, puis :</div>
        <div class="help-faq-a">• <strong>MagicDNS</strong> → Enable MagicDNS<br>• <strong>HTTPS Certificates</strong> → Enable HTTPS…</div>
        <div class="help-check"><strong>À vérifier :</strong> dans eCasa, Réglages → Tailscale, les lignes <strong>MagicDNS</strong> et <strong>HTTPS Certificates</strong> passent au vert après <strong>Actualiser</strong>. Sinon, attends 30 s (émission du certificat) et réessaie.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">4</span>
          <div>
            <div class="help-step-title">Publier eCasa en HTTPS sur le port 443</div>
            <div class="help-step-sub">Expose la racine du tailnet vers eCasa, qui écoute en local sur 8780.</div>
          </div>
        </div>
        <div><span class="help-os-label">Linux / macOS</span><code class="help-code">sudo tailscale serve --bg --set-path=/ http://127.0.0.1:8780</code></div>
        <div><span class="help-os-label">Windows</span><code class="help-code">tailscale serve --bg --set-path=/ http://127.0.0.1:8780</code></div>
        <div class="help-check"><strong>À vérifier :</strong> <code>tailscale serve status</code> affiche <code>https://ecasa.&lt;tailnet&gt;.ts.net (tailnet only)</code> puis <code>/ proxy http://127.0.0.1:8780</code>.</div>
        <div class="help-warn"><strong>Prérequis :</strong> eCasa doit écouter sur <code>127.0.0.1</code> ou <code>0.0.0.0</code> (Réglages → Réseau), jamais sur une IP LAN précise.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">5</span>
          <div>
            <div class="help-step-title">Tester depuis le téléphone</div>
            <div class="help-step-sub">Le téléphone doit avoir Tailscale installé et connecté au même compte.</div>
          </div>
        </div>
        <div class="help-faq-a">Ouvre <code>https://ecasa.&lt;tailnet&gt;.ts.net/</code> — sans <code>:8780</code>, sans <code>http://</code>.</div>
        <div class="help-check"><strong>À vérifier :</strong> la page eCasa se charge et le cadenas HTTPS est valide. Si le nom ne résout pas, teste d'abord <code>https://100.x.y.z/</code> (adresse de l'étape 2).</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">6</span>
          <div>
            <div class="help-step-title">Rendre la configuration durable</div>
            <div class="help-step-sub">Pour que tout revienne seul après un redémarrage.</div>
          </div>
        </div>
        <div class="help-faq-a">• <code>--bg</code> conserve déjà la publication après un redémarrage du service Tailscale.<br>• Dans eCasa : active <strong>Activer Tailscale</strong> et <strong>Publier automatiquement</strong>, puis <strong>Enregistrer</strong>.<br>• Optionnel : génère une <a href="https://login.tailscale.com/admin/settings/keys" target="_blank" rel="noreferrer">clé d'authentification</a> et colle-la dans le champ prévu.</div>
        <div class="help-check"><strong>À vérifier :</strong> après redémarrage de la machine, <code>tailscale serve status</code> et <code>https://ecasa.&lt;tailnet&gt;.ts.net/</code> fonctionnent encore.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">?</span>
          <div>
            <div class="help-step-title">Dépannage</div>
            <div class="help-step-sub">Les messages les plus fréquents et leur solution.</div>
          </div>
        </div>
        <div class="help-faq">
          <div><div class="help-faq-q">« Tailscale introuvable »</div><div class="help-faq-a">Le binaire n'est pas dans le PATH. Renseigne son chemin dans <strong>Chemin de tailscale</strong> (Windows : <code>C:\\Program Files\\Tailscale\\tailscale.exe</code>), puis Enregistrer.</div></div>
          <div><div class="help-faq-q">« HTTPS is not enabled in your tailnet »</div><div class="help-faq-a">Étape 3 incomplète : active <strong>HTTPS Certificates</strong> dans la console, attends 30 s, puis Actualiser et Publier.</div></div>
          <div><div class="help-faq-q">La page répond 502 ou « connection refused »</div><div class="help-faq-a">eCasa n'écoute pas sur l'adresse attendue, ou le port a changé. Vérifie <code>host</code>/<code>port</code> dans Réglages → Réseau, puis republie avec le bon port.</div></div>
          <div><div class="help-faq-q">La page répond 404 ou « Not Found »</div><div class="help-faq-a">La publication vise un sous-chemin. Republie avec <code>--set-path=/</code>.</div></div>
          <div><div class="help-faq-q">Le nom MagicDNS ne résout pas sur le téléphone</div><div class="help-faq-a">MagicDNS est désactivé sur le tailnet, ou le téléphone n'est pas connecté. En attendant, utilise l'adresse <code>100.x.y.z</code>.</div></div>
          <div><div class="help-faq-q">Le nœud n'a pas le bon nom</div><div class="help-faq-a">Relance <code>tailscale up --hostname=ecasa</code>, ou renomme la machine dans la console → Machines.</div></div>
          <div><div class="help-faq-q">Le nœud s'appelle « ecasa-1 »</div><div class="help-faq-a">Le nom <code>ecasa</code> est déjà pris : soit une ancienne machine, soit un <strong>Service Tailscale</strong> (visible dans le diagnostic, ligne « Service Tailscale »). Supprime-le dans <a href="https://login.tailscale.com/admin/services" target="_blank" rel="noreferrer">console → Services</a> (ou l'ancienne machine dans console → Machines), puis relance <code>tailscale up --hostname=ecasa</code>.</div></div>
          <div><div class="help-faq-q">Retirer l'accès distant</div><div class="help-faq-a"><code>sudo tailscale serve reset</code> (sans <code>sudo</code> sur Windows). Le nœud reste connecté.</div></div>
        </div>
      </div>

      <p class="hint mt-2">Liens utiles : <a href="https://login.tailscale.com/admin/dns" target="_blank" rel="noreferrer">DNS du tailnet</a> · <a href="https://login.tailscale.com/admin/machines" target="_blank" rel="noreferrer">Machines</a> · <a href="https://login.tailscale.com/admin/settings/keys" target="_blank" rel="noreferrer">Clés d'authentification</a> · <a href="https://tailscale.com/download" target="_blank" rel="noreferrer">Installer Tailscale</a> · <a href="https://tailscale.com/kb/1242/tailscale-serve" target="_blank" rel="noreferrer">Documentation serve</a></p>
      <p class="hint">Scripts prêts à l'emploi (à lancer en SSH sur le serveur) : <a href="https://raw.githubusercontent.com/shervet/ecasa/main/tools/tailscale-ecasa.sh" target="_blank" rel="noreferrer">tailscale-ecasa.sh</a> · <a href="https://raw.githubusercontent.com/shervet/ecasa/main/tools/tailscale-ecasa.bat" target="_blank" rel="noreferrer">tailscale-ecasa.bat</a></p>
    </div>`,
  });
  sheet?.footer?.querySelector("[data-help-refresh]")?.addEventListener("click", () => {
    sheet.close();
    loadTailscale();
  });
}

async function loadUpdate() {
  const sec = root?.querySelector("#sec-update");
  try {
    updateInfo = await get("/api/update/check");
  } catch (err) {
    // Échec du check (serveur occupé, redémarrage…) : ne pas prétendre que
    // l'installation n'est pas issue de Git. is_git=null => afficher l'erreur.
    updateInfo = { is_git: null, error: err.message || String(err), available: false };
  }
  if (sec) {
    sec.outerHTML = updateSection();
    root.querySelector("#updateCheck")?.addEventListener("click", loadUpdate);
    root.querySelector("#updateApply")?.addEventListener("click", updateAction);
  }
}

async function updateAction() {
  const button = root?.querySelector("#updateApply");
  if (button) button.disabled = true;
  let failure = null;
  try {
    const payload = await post("/api/update/apply");
    const message = payload.detail || payload.output || "Terminé.";
    toast(message, { type: payload.ok ? "info" : "error", timeout: 10000 });
    if (payload.command && !payload.ok) {
      toast(`Commande : ${payload.command}`, { type: "info", timeout: 12000 });
    }
    if (payload.ok) {
      toast("Redémarrage du serveur en cours… la page va se recharger.", { type: "info", timeout: 8000 });
      setTimeout(() => location.reload(), 4000);
    }
  } catch (err) {
    failure = err;
    toast(err.message || String(err), { type: "error", timeout: 10000 });
  }
  if (button) button.disabled = false;
  await loadUpdate();
}

async function loadTailscale() {
  const host = root?.querySelector("#tailscaleStatus");
  if (!host) return;
  try {
    tailscale = await get("/api/tailscale/status");
    renderTailscale();
  } catch (err) {
    host.innerHTML = `<div class="hint is-bad">${escapeHtml(err.message)}</div>`;
  }
}

function renderTailscale() {
  const host = root?.querySelector("#tailscaleStatus");
  if (!host) return;
  const data = tailscale;
  if (!data) {
    host.innerHTML = `<span class="hint">Information indisponible.</span>`;
    return;
  }
  const stateLabel = !data.installed
    ? "Non installé"
    : data.running
      ? "Connecté"
      : data.backend_state || "Arrêté";
  const url = data.https_url || data.serve_url || "";
  const checks = Array.isArray(data.checks) ? data.checks : [];
  const failed = checks.filter((item) => !item.ok);
  const serveLabel = data.serve_ok
    ? "prête"
    : data.serve_active
      ? "à corriger"
      : "inactive";
  const summary = !checks.length
    ? ""
    : failed.length
      ? `<p class="hint is-bad mt-1">${failed.length} point${failed.length > 1 ? "s" : ""} à corriger pour que l'app Android fonctionne.</p>`
      : `<p class="hint is-ok mt-1">Tout est prêt : l'app peut charger ${escapeHtml(url || "https://ecasa.<tailnet>.ts.net/")}.</p>`;
  const pageHost = String(location.hostname || "").toLowerCase();
  const nodeDns = String(data.dns_name || "").toLowerCase();
  const nodeHost = nodeDns ? nodeDns.split(".")[0] : "";
  const ipLike = /^(\d{1,3}\.){3}\d{1,3}$/.test(pageHost) || pageHost.includes(":");
  const localHost = pageHost === "localhost" || pageHost === "127.0.0.1" || pageHost === "::1";
  const wrongHost = Boolean(nodeHost && pageHost && !ipLike && !localHost && pageHost !== nodeHost);
  const hostWarning = wrongHost
    ? `<p class="hint is-bad mt-1">Cette page est ouverte via <strong>${escapeHtml(pageHost)}</strong>, alors que le nœud Tailscale est <strong>${escapeHtml(nodeDns)}</strong> : les actions peuvent être refusées. Ouvre plutôt ${url ? `<a href="${escapeHtml(url)}">${escapeHtml(url)}</a>` : "l'URL du nœud"}.</p>`
    : "";
  const installBtn = root?.querySelector("#tsInstall");
  if (installBtn) {
    installBtn.innerHTML = `${icon("download", 14)} ${data?.installed ? "Désinstaller Tailscale" : "Installer Tailscale"}`;
  }
  host.innerHTML = `
    <div class="row-between">
      <span class="tiny muted">État</span>
      <span class="pill ${data.running ? "pill-ok" : "pill-warn"}">${escapeHtml(stateLabel)}</span>
    </div>
    <div class="row-between"><span class="tiny muted">Adresse Tailscale</span><span class="tiny mono">${escapeHtml(data.ip || "—")}</span></div>
    <div class="row-between"><span class="tiny muted">Nom DNS</span><span class="tiny mono" style="word-break:break-all;text-align:right">${escapeHtml(data.dns_name || "—")}</span></div>
    <div class="row-between">
      <span class="tiny muted">MagicDNS</span>
      <span class="pill ${data.magic_dns_enabled ? "pill-ok" : "pill-ghost"}">${data.magic_dns_enabled ? escapeHtml(data.magic_dns_suffix || "activé") : "inactif"}</span>
    </div>
    <div class="row-between">
      <span class="tiny muted">HTTPS Certificates</span>
      <span class="pill ${data.https_ready ? "pill-ok" : "pill-ghost"}">${data.https_ready ? "prêt" : "inactif"}</span>
    </div>
    <div class="row-between">
      <span class="tiny muted">Publication</span>
      <span class="pill ${data.serve_ok ? "pill-ok" : data.serve_active ? "pill-warn" : "pill-ghost"}">${serveLabel}</span>
    </div>
    ${data.serve_target ? `<div class="row-between"><span class="tiny muted">Cible locale</span><span class="tiny mono" style="word-break:break-all;text-align:right">${escapeHtml(data.serve_target)}</span></div>` : ""}
    ${data.binary ? `<div class="row-between"><span class="tiny muted">Binaire</span><span class="tiny mono" style="word-break:break-all;text-align:right">${escapeHtml(data.binary)}</span></div>` : ""}
    ${Array.isArray(data.services) && data.services.length ? `<div class="row-between"><span class="tiny muted">Service Tailscale</span><span class="tiny mono" style="word-break:break-all;text-align:right">${escapeHtml(data.services.map((item) => item.name).join(", "))}</span></div>` : ""}
    ${url ? `<div class="row-between"><span class="tiny muted">URL de l'app</span><a class="tiny" href="${escapeHtml(url)}" target="_blank" rel="noreferrer" style="word-break:break-all;text-align:right">${escapeHtml(url)}</a></div>` : ""}
    ${hostWarning}
    ${data.auth_url ? `<p class="hint mt-1">Ce nœud attend une autorisation : <a href="${escapeHtml(data.auth_url)}" target="_blank" rel="noreferrer">ouvrir la page de connexion Tailscale</a>.</p>` : ""}
    ${summary}
    ${checks.length ? `<div class="stack mt-1" style="gap:4px">${checks.map((item) => `<div class="row-between" style="gap:8px"><span class="hint ${item.ok ? "muted" : "is-bad"}">${item.ok ? icon("check", 12) : icon("alert", 12)} ${escapeHtml(item.label)}</span>${item.ok ? "" : `<span class="hint is-bad" style="text-align:right;max-width:60%">${escapeHtml(item.detail || "")}</span>`}</div>`).join("")}</div>` : ""}
    ${!data.installed ? '<p class="hint mt-1">Installe Tailscale sur le Raspberry Pi : <code>curl -fsSL https://tailscale.com/install.sh | sudo sh</code></p>' : ""}
    ${data.can_pilot === false ? `<p class="hint is-bad mt-1">Le service eCasa n'a pas le droit de piloter Tailscale. Lance une seule fois en SSH : <code>sudo tailscale set --operator=${escapeHtml(data.service_user || "admin")}</code></p>` : ""}
  `;
}

async function tailscaleAction(path, body) {
  const button = root?.querySelector(
  path === "up" ? "#tsUp"
    : path === "serve/enable" ? "#tsServeOn"
    : path === "serve/disable" ? "#tsServeOff"
    : path === "install" || path === "uninstall" ? "#tsInstall"
    : "#tsServeOff"
);
  if (button) button.disabled = true;
  let failure = null;
  try {
    const payload = await post(`/api/tailscale/${path}`, body);
    if (payload.warning) {
      toast(payload.warning, { type: "error", timeout: 10000 });
    } else if (path === "serve/disable") {
      toast("Publication retirée. Accès distant coupé : republie depuis le réseau local.", {
        type: "info",
        timeout: 7000,
      });
    } else if (path === "install" || path === "uninstall") {
      const message = payload.detail || payload.output || "Terminé.";
      toast(message, { type: payload.ok ? "info" : "error", timeout: 10000 });
      if (payload.command && !payload.ok) {
        toast(`Commande : ${payload.command}`, { type: "info", timeout: 12000 });
      }
    } else if (payload.needs_login) {
      toast(
        "Nœud à autoriser : ouvre le lien affiché dans « État du nœud », puis Actualiser.",
        { type: "info", timeout: 12000 },
      );
    } else {
      const message = payload.authkey_saved
        ? "Clé enregistrée et nœud connecté."
        : payload.url
          ? `Publié : ${payload.url}`
          : payload.output || payload.message || "Commande envoyée.";
      toast(message, { type: "info", timeout: 7000 });
    }
  } catch (err) {
    failure = err;
  }
  // « Dépublier » coupe le proxy qui a servi cette page : la réponse peut
  // être perdue. On laisse au serveur le temps de retirer la publication,
  // puis on relit l'état réel avant d'afficher une erreur.
  if (path === "serve/disable") {
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  try {
    await loadTailscale();
  } catch {
    /* le panneau affiche déjà l'erreur */
  }
  if (failure) {
    const done = path === "serve/disable" && tailscale && !tailscale.serve_active;
    if (done) {
      toast("Publication retirée. Accès distant coupé : republie depuis le réseau local.", {
        type: "info",
        timeout: 7000,
      });
    } else if (path === "serve/disable" && (failure.status === 0 || failure.status === 502 || failure.status === 504)) {
      // La page était ouverte via l'URL HTTPS publiée : sa disparition coupe
      // aussi la réponse. L'état réel sera relu au prochain chargement.
      toast("Publication retirée. Accès distant coupé : republie depuis le réseau local.", {
        type: "info",
        timeout: 7000,
      });
    } else {
      toastError(failure);
    }
  }
  if (button) button.disabled = false;
}
