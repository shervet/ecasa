// Réglages : apparence, moteur, semaines A/B, pièces, appareils et connecteurs.

import { del, get, post, put } from "../api.js";
import { icon } from "../icons.js";
import { closeSheet, openSheet } from "../sheet.js";
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
let pronoteResults = null;

/* --------------------------------------------------------------- helpers */

const cfg = () => store.config || {};

function toggle(key, checked, label = "") {
  return `<label class="switch"${label ? ` title="${escapeHtml(label)}"` : ""}>
    <input type="checkbox" data-key="${key}"${checked ? " checked" : ""}>
    <span class="track"></span>
  </label>`;
}

function row(title, sub, control, stack = false) {
  return `<div class="settings-row${stack ? " is-stack" : ""}">
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
    if (!store.config) await refreshConfig({ silent: true });

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
          ${[
            ["appearance", "Apparence"],
            ["engine", "Moteur"],
            ["climate", "Clim"],
            ["weeks", "Semaines A/B"],
            ["rooms", "Pièces"],
            ["devices", "Appareils"],
            ["pronote", "Pronote"],
            ["weather", "Météo"],
            ["energy", "Électricité"],
            ["storage", "Stockage"],
            ["server", "Serveur"],
            ["tailscale", "Tailscale"],
          ]
            .map(([id, label]) => `<a href="#/settings" data-jump="${id}">${label}</a>`)
            .join("")}
        </div>

        <div class="settings-layout">
          ${appearanceSection()}
          ${engineSection()}
          ${climateSection()}
          ${weeksSection()}
          ${roomsSection()}
          ${devicesSection()}
          ${pronoteSection()}
          ${weatherSection()}
          ${energySection()}
          ${storageSection()}
          ${serverSection()}
          ${tailscaleSection()}
          <p class="hint">
            MeCasa ${escapeHtml(store.system?.app?.version || "")} · les secrets ne sont jamais renvoyés à l'interface.
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
    await loadTailscale();
  },

  unmount() {
    closeSheet();
    root = null;
  },

  onState() {
    if (!root) return;
    const active = document.activeElement;
    if (active && root.contains(active)) return;
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
      "MeCasa s'ajoute comme une application, sans passer par un store.",
      `<button class="btn btn-sm" type="button" id="installHelp">${icon("install", 15)} Comment faire</button>`
    )}
    ${row(
      "Mouvements réduits",
      "Respecte automatiquement le réglage d'accessibilité du système.",
      `<span class="pill pill-ghost">automatique</span>`
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
      "Quand il est en pause, MeCasa ne change plus les consignes tout seul.",
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
    <div class="row wrap" style="gap:8px">
      <span class="pill ${store.state?.status?.mock ? "pill-warn" : "pill-ok"}" id="engineMode">${store.state?.status?.mock ? "Simulation" : "Réel"}</span>
      <span class="pill pill-ghost" id="engineState">${enabled ? "Moteur actif" : "En pause"}</span>
      <span class="pill pill-ghost">Dernière lecture ${store.state?.status?.last_poll ? fmtDateTime(store.state.status.last_poll) : "—"}</span>
    </div>
    ${store.state?.status?.last_error ? `<div class="hint is-bad">${escapeHtml(store.state.status.last_error)}</div>` : ""}`,
    `<button class="btn" type="button" data-sync>${icon("refresh", 16)} Synchroniser les appareils</button>
     <button class="btn" type="button" data-apply>${icon("zap", 16)} Appliquer maintenant</button>
     <button class="btn btn-primary" type="button" data-save="engine">${icon("save", 16)} Enregistrer</button>`
  );
}

function climateSection() {
  const settings = store.state?.settings || {};
  const fan = String(settings.atlantic_fan_default ?? "");
  const airflow = String(settings.atlantic_airflow_default ?? "");
  return section(
    "climate",
    "Climatisation Atlantic",
    "fan",
    `${selectRow(
      "Soufflerie par défaut",
      "atlantic_fan_default",
      fan,
      [
        ["", "Inchangée (télécommande)"],
        ["auto", "Automatique"],
        ["low", "Basse"],
        ["med", "Moyenne"],
        ["high", "Haute"],
      ],
      {
        sub: "Vitesse appliquée quand MeCasa pilote la clim. « Inchangée » laisse le réglage de la télécommande.",
      }
    )}
    ${selectRow(
      "Orientation du flux d'air",
      "atlantic_airflow_default",
      airflow,
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
      {
        sub: "Position fixe des volets ou balayage. Les positions disponibles dépendent du modèle de clim.",
      }
    )}`,
    `<button class="btn btn-primary" type="button" data-save="climate">${icon("save", 16)} Enregistrer</button>`
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
    <div class="row-between wrap" style="gap:10px">
      <div class="tiny muted">
        Semaine en cours : <strong style="color:var(--text)">${escapeHtml(store.state?.week || "—")}</strong>
        ${store.state?.iso_week ? ` · ISO ${store.state.iso_week}` : ""}
      </div>
    </div>`,
    `<button class="btn btn-primary" type="button" data-save="weeks">${icon("save", 16)} Enregistrer</button>`
  );
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

function devicesSection() {
  const cfg_ = cfg();
  return section(
    "devices",
    "Appareils",
    "thermometer",
    `<p class="hint">Les appareils sont découverts depuis Atlantic (Cozytouch) ou le Hub Wiser. Tu peux les renommer, les affecter à une pièce et couper leur pilotage. Chaque système garde ses propres réglages et sa liste d'appareils.</p>

    <div id="sec-atlantic" class="stack" style="gap:14px">
      <h3 class="card-title" style="margin-top:6px">${icon("thermometer", 16)} Cozytouch / Atlantic</h3>
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
      <div class="stack" id="atlanticDevicesList"></div>
      <div class="row wrap" style="gap:8px;margin-top:4px">
        <button class="btn" type="button" data-test="atlantic">${icon("shield", 16)} Tester</button>
        <button class="btn btn-primary" type="button" data-save="atlantic">${icon("save", 16)} Enregistrer</button>
        <button class="btn" type="button" id="discover">${icon("search", 16)} Voir le compte Atlantic</button>
        <button class="btn" type="button" data-sync>${icon("refresh", 16)} Synchroniser</button>
      </div>
    </div>

    <div id="sec-wiser" class="stack" style="gap:14px;margin-top:18px">
      <div class="row-between" style="align-items:center;gap:8px">
        <h3 class="card-title">${icon("hub", 16)} Schneider Wiser</h3>
        <button class="btn btn-sm" type="button" id="wiserFind">${icon("wifi", 16)} Chercher le Hub</button>
      </div>
      ${row("Activer Wiser", "Pilote aussi les zones du Hub Wiser.", toggle("wiser_enabled", Boolean(cfg_.wiser_enabled)))}
      ${textRow("Adresse du Hub", "wiser_host", cfg_.wiser_host, { placeholder: "http://192.168.1.20", sub: "Adresse IP ou nom du Hub sur le réseau local." })}
      ${row(
        "Secret du Hub",
        cfg_.wiser_secret_set ? "Un secret est déjà enregistré. Laisse vide pour le conserver." : "Le secret se récupère au premier appairage.",
        `<input class="input" data-key="wiser_secret" data-secret type="password" autocomplete="new-password" placeholder="${cfg_.wiser_secret_set ? "••••••••" : "secret"}" style="width:min(320px,62vw)">`,
        true
      )}
      ${row("Effacer le secret", "Nécessaire si le Hub a été réinitialisé.", toggle("wiser_secret_clear", false))}
      <div class="stack" id="wiserResults"></div>
      <div class="stack" id="wiserDevicesList"></div>
      <div class="row wrap" style="gap:8px;margin-top:4px">
        <button class="btn" type="button" id="wiserImportPrograms">${icon("download", 16)} Import programmes</button>
        <button class="btn btn-primary" type="button" data-save="wiser">${icon("save", 16)} Enregistrer</button>
      </div>
    </div>`,
    ``
  );
}



function pronoteSection() {
  const c = cfg();
  return section(
    "pronote",
    "Pronote",
    "school",
    `<div class="field">
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
      <label class="field">
        <span class="label">Préparation / lever</span>
        <input class="input" data-key="pronote_morning_prep_minutes" data-number type="number" min="0" max="180" value="${escapeHtml(c.pronote_morning_prep_minutes ?? 20)}">
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
    `<button class="btn" type="button" data-test="pronote">${icon("shield", 16)} Tester la connexion</button>
     <button class="btn btn-primary" type="button" data-save="pronote">${icon("save", 16)} Enregistrer</button>`
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
    <p class="hint">Astuce : clique droit sur ta maison dans une carte, « Copier les coordonnées ».</p>`,
    `<button class="btn btn-primary" type="button" data-save="weather">${icon("save", 16)} Enregistrer</button>`
  );
}

function energySection() {
  const c = cfg();
  return section(
    "energy",
    "Électricité",
    "zap",
    `${row(
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
    <div class="hint" id="energyTestResult"></div>`,
    `<button class="btn" type="button" data-test="energy">${icon("shield", 16)} Tester la connexion</button>
     <button class="btn btn-primary" type="button" data-save="energy">${icon("save", 16)} Enregistrer</button>`
  );
}

function storageSection() {
  const c = cfg();
  return section(
    "storage",
    "Stockage basse consommation",
    "database",
    `<div class="hint">MeCasa garde tout en mémoire et n'écrit sur la carte SD que par lots. C'est ce qui protège la carte du Raspberry Pi.</div>
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
    </div>`,
    `<button class="btn btn-primary" type="button" data-save="storage">${icon("save", 16)} Enregistrer</button>`
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
    <div class="row wrap" style="gap:8px">
      <button class="btn" type="button" id="exportBtn">${icon("download", 15)} Exporter les données</button>
      <button class="btn" type="button" id="importBtn">${icon("upload", 15)} Importer</button>
      <input id="importFile" type="file" accept="application/json,.json" hidden>
      <button class="btn btn-danger" type="button" data-stop>${icon("power", 15)} Arrêter le serveur</button>
    </div>`,
    `<button class="btn btn-primary" type="button" data-save="server">${icon("save", 16)} Enregistrer</button>`
  );
}

function tailscaleSection() {
  const c = cfg();
  return section(
    "tailscale",
    "Tailscale",
    "shield",
    `<p class="hint">Accès à distance chiffré, sans ouvrir de port sur la box. MeCasa peut publier son interface sur ton tailnet.</p>
    ${row(
      "Guide de configuration",
      "Installation, connexion et publication du service sur ton tailnet.",
      `<button class="btn btn-sm" type="button" id="tailscaleHelp">${icon("info", 15)} Ouvrir l'aide</button>`
    )}
    ${row("Activer Tailscale", "Au démarrage, MeCasa peut connecter le nœud et publier le service.", toggle("tailscale_enabled", Boolean(c.tailscale_enabled)))}
    ${row("Publier automatiquement", "Au démarrage, expose l'interface sur le tailnet en HTTPS via « tailscale serve ».", toggle("tailscale_auto_serve", Boolean(c.tailscale_auto_serve)))}
    ${textRow("Nom du nœud", "tailscale_hostname", c.tailscale_hostname || "mecasa", {
      sub: "Nom résolu par l'app Android : &lt;nom&gt;.&lt;tailnet&gt;.ts.net. Doit être « mecasa ».",
      placeholder: "mecasa",
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
      </div>
      <p class="hint mt-2">« Dépublier » coupe l'accès via l'URL HTTPS : pour republier depuis un autre appareil, ouvre MeCasa sur le réseau local (port 8780).</p>
    </div>`,
    `<button class="btn btn-primary" type="button" data-save="tailscale">${icon("save", 16)} Enregistrer</button>`
  );
}

/* --------------------------------------------------------------- liaisons */

function bind() {
  root.querySelectorAll("[data-jump]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const target = root.querySelector(`#sec-${link.dataset.jump}`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      root.querySelectorAll("[data-jump]").forEach((item) => item.classList.toggle("is-active", item === link));
    });
  });

  root.querySelectorAll("[data-theme]").forEach((button) => {
    button.addEventListener("click", () => {
      const theme = button.dataset.theme;
      setPref("theme", theme);
      window.dispatchEvent(new CustomEvent("mecasa:theme", { detail: theme }));
      root.querySelectorAll("[data-theme]").forEach((item) => item.classList.toggle("is-active", item === button));
    });
  });

  root.querySelector("#installHelp")?.addEventListener("click", () => {
    openSheet({
      title: "Installer MeCasa",
      sub: "Une application, pas un onglet",
      body: `<div class="stack">
        <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">Android / Chrome</div><div class="sr-sub">Menu ⋮ → « Installer l'application » ou « Ajouter à l'écran d'accueil ».</div></div></div>
        <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">iPhone / Safari</div><div class="sr-sub">Partager → « Sur l'écran d'accueil ».</div></div></div>
        <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">Ordinateur</div><div class="sr-sub">Une icône d'installation apparaît dans la barre d'adresse.</div></div></div>
      </div>`,
    });
  });

  root.querySelectorAll("[data-save]").forEach((button) => {
    button.addEventListener("click", () => saveSection(button.dataset.save, button));
  });

  root.querySelectorAll("[data-test]").forEach((button) => {
    button.addEventListener("click", () => testConnection(button.dataset.test, button));
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
        }</div><p class="hint mt-2">Utilise « Synchroniser » pour les ajouter à MeCasa.</p>`,
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

  root.querySelector("#exportBtn")?.addEventListener("click", exportData);
  root.querySelector("#importBtn")?.addEventListener("click", () => root.querySelector("#importFile")?.click());
  root.querySelector("#importFile")?.addEventListener("change", importData);

  root.querySelector("#tailscaleRefresh")?.addEventListener("click", loadTailscale);
  root.querySelector("#tailscaleHelp")?.addEventListener("click", openTailscaleHelp);
  root.querySelector("#energyHelp")?.addEventListener("click", openEnergyHelp);
  root.querySelector("#tsUp")?.addEventListener("click", () => tailscaleAction("up"));
  root.querySelector("#tsServeOn")?.addEventListener("click", () => tailscaleAction("serve/enable"));
  root.querySelector("#tsServeOff")?.addEventListener("click", () => tailscaleAction("serve/disable"));

  root.querySelector("#hostPicker")?.querySelectorAll("[data-host]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = root.querySelector('#sec-server [data-key="host"]');
      if (input) input.value = button.dataset.host;
      root.querySelectorAll("#hostPicker [data-host]").forEach((item) => item.classList.toggle("is-active", item === button));
    });
  });

  paintEngineState();
}

/* --------------------------------------------------------------- save */

async function saveSection(name, button) {
  if (!root) return;
  const container = root.querySelector(`#sec-${name}`);
  if (!container) return;

  if (button) button.disabled = true;
  try {
    if (name === "engine") {
      const settings = {
        engine_enabled: container.querySelector('[data-key="engine_enabled"]')?.checked ? "1" : "0",
        poll_interval: String(container.querySelector('[data-key="poll_interval"]')?.value || "60"),
      };
      const payload = await put("/api/settings", settings);
      if (store.state) store.state.settings = payload.settings;
      toast("Moteur mis à jour.");
      paintEngineState();
    } else if (name === "climate") {
      const settings = {
        atlantic_fan_default: container.querySelector('[data-key="atlantic_fan_default"]')?.value || "",
        atlantic_airflow_default: container.querySelector('[data-key="atlantic_airflow_default"]')?.value || "",
      };
      const payload = await put("/api/settings", settings);
      if (store.state) store.state.settings = payload.settings;
      toast("Réglages de la clim enregistrés.");
    } else if (name === "weeks") {
      const settings = {
        week_mode: container.querySelector('[data-key="week_mode"]')?.value || "parity",
        week_flip: container.querySelector('[data-key="week_flip"]')?.checked ? "1" : "0",
        default_week: container.querySelector('[data-key="default_week"]')?.value || "A",
      };
      const payload = await put("/api/settings", settings);
      if (store.state) store.state.settings = payload.settings;
      await refreshState({ silent: true });
      toast("Alternance des semaines enregistrée.");
    } else {
      const body = collect(container);
      const payload = await put("/api/config", body);
      store.config = payload.config;
      if (name === "energy") {
        const clear = container.querySelector('[data-key="med_token_clear"]');
        if (clear) clear.checked = false;
      }
      await refreshConfig({ silent: true });
      await refreshSystem({ silent: true });
      renderStorage();
      renderSystem();
      toast("Réglages enregistrés.");
      if (name === "weather") window.refreshWeather?.();
      if (name === "tailscale") await loadTailscale();
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
    sub: "Consommation Enedis dans MeCasa",
    body: `<div class="stack">
      <p class="hint">MyElectricalData est une passerelle gratuite entre Enedis et MeCasa. Il faut d'abord autoriser l'accès à tes données Enedis, puis récupérer un token à coller dans les réglages.</p>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">1. Autoriser l'accès à tes données Enedis</div><div class="sr-sub">Sur le site MyElectricalData, clique sur « Faire la demande de consentement » en bas de page, connecte-toi à ton espace Enedis et autorise la passerelle. Le consentement est valable 36 mois.</div></div></div>
      <a class="btn btn-sm" href="https://www.myelectricaldata.fr" target="_blank" rel="noreferrer">${icon("external", 14)} Ouvrir MyElectricalData</a>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">2. Activer la collecte horaire chez Enedis</div><div class="sr-sub">Dans ton espace Enedis, coche « Enregistrement de la consommation horaire » et « Collecte de la consommation horaire ». Sans ça, les données sont incomplètes. À réactiver chaque année.</div></div></div>
      <a class="btn btn-sm" href="https://mon-compte-particulier.enedis.fr/donnees/" target="_blank" rel="noreferrer">${icon("external", 14)} Ouvrir l'espace Enedis</a>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">3. Récupérer le token</div><div class="sr-sub">Après le consentement, MyElectricalData affiche ton token : une chaîne de 54 caractères. Il est valable un an, puis il faut recommencer l'étape 1.</div></div></div>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">4. Renseigner MeCasa</div><div class="sr-sub">Colle le PDL (14 chiffres, visible sur ta facture ou dans l'espace Enedis) et le token dans les champs ci-dessus, puis clique sur « Tester la connexion » avant d'enregistrer.</div></div></div>

      <div class="settings-row is-stack"><div class="sr-main"><div class="sr-title">Bon à savoir</div><div class="sr-sub">Enedis limite chaque requête à 1095 jours : MeCasa découpe par année et affiche jusqu'à 3 ans d'historique. Les données sont mises en cache 6 h en mémoire, rien n'est écrit sur le disque.</div></div></div>

      <p class="hint mt-2">Liens utiles : <a href="https://www.myelectricaldata.fr/documentation/" target="_blank" rel="noreferrer">Documentation</a> · <a href="https://www.myelectricaldata.fr/faq/" target="_blank" rel="noreferrer">FAQ</a> · <a href="https://www.myelectricaldata.fr/error_code/" target="_blank" rel="noreferrer">Codes d'erreur</a></p>
    </div>`,
    footer: `<a class="btn btn-primary" href="https://www.myelectricaldata.fr" target="_blank" rel="noreferrer">${icon("external", 16)} Ouvrir MyElectricalData</a>`,
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
        if (hostInput) hostInput.value = `http://${useButton.dataset.useWiser}`;
        const secret = candidates[index]?.secret;
        if (secretInput && secret) secretInput.value = secret;
        toast("Hub renseigné. Pense à enregistrer.", { type: "info" });
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
      models = JSON.parse(localStorage.getItem("mecasa.weekModels") || "[]");
      if (!Array.isArray(models)) models = [];
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

    localStorage.setItem("mecasa.weekModels", JSON.stringify(models));

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
        if (latInput) latInput.value = lat;
        if (lonInput) lonInput.value = lon;
        if (cityInput && useButton.dataset.cityName) cityInput.value = useButton.dataset.cityName;
        toast("Ville et coordonnées remplies. Pense à enregistrer.", { type: "info" });
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
}

function modelOptionsSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem("mecasa.weekModels") || "[]");
    if (!Array.isArray(saved)) return "";
    return saved.map((m, i) => `<option value="${i}">${escapeHtml(m.name)}</option>`).join("");
  } catch {
    return "";
  }
}

function cloneDaysSettings(days) {
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
function openRoomEditor(room) {
  const devices = store.state?.devices || [];
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
      <label class="field">
        <span class="label">Ordre d'affichage <span class="opt">0 = premier</span></span>
        <input class="input" type="number" data-position min="0" value="${escapeHtml(room?.position ?? 0)}">
      </label>
    </div>
    <p class="hint">Le thermostat mesure la température (appareil Atlantic ou capteur dédié), l'actionneur chauffe ou refroidit. Sur Wiser, la zone fait les deux.</p>
    ${room ? `
    <label class="field">
      <span class="label">Modèle de semaine</span>
      <div class="input-row">
        <select class="select" data-model style="flex:1">
          <option value="">— choisir un modèle —</option>
          ${modelOptionsSettings()}
        </select>
        <button class="btn btn-sm" type="button" data-apply-model>${icon("check", 14)} Appliquer</button>
      </div>
      <span class="hint">Remplace le planning de la pièce par le modèle choisi (semaines A et B).</span>
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
    const applyBtn = body.querySelector("[data-apply-model]");
    applyBtn?.addEventListener("click", async () => {
      const select = body.querySelector("[data-model]");
      const idx = Number(select?.value);
      if (!Number.isInteger(idx) || idx < 0) { toast("Choisis un modèle.", { type: "warn" }); return; }
      let saved = [];
      try { saved = JSON.parse(localStorage.getItem("mecasa.weekModels") || "[]"); } catch { saved = []; }
      const m = saved[idx];
      if (!m) { toast("Modèle introuvable.", { type: "warn" }); return; }
      const deviceId = room.actuator_device_id || room.thermostat_device_id;
      if (!deviceId) { toast("Aucun appareil associé à cette pièce.", { type: "warn" }); return; }
      if (!confirm(`Appliquer le modèle « ${m.name} » à ${room.name} ?`)) return;
      applyBtn.disabled = true;
      try {
        await put(`/api/programs/${deviceId}/A`, { days: cloneDaysSettings(m.days?.A) });
        await put(`/api/programs/${deviceId}/B`, { days: cloneDaysSettings(m.days?.B) });
        await refreshState({ silent: true });
        toast(`Modèle « ${m.name} » appliqué.`);
      } catch (err) {
        toastError(err);
      } finally {
        applyBtn.disabled = false;
      }
    });
  }

  const value = (selector) => body.querySelector(selector)?.value ?? "";

  footer.querySelector("[data-validate]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const payload = {
      name: value("[data-name]").trim(),
      thermostat_device_id: value("[data-thermostat]") || null,
      actuator_device_id: value("[data-actuator]") || null,
      position: Number(value("[data-position]") || 0),
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
  if (!atlanticHost || !wiserHost) return;
  const state = store.state;
  if (!state) {
    atlanticHost.innerHTML = `<div class="skeleton" style="height:60px"></div>`;
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
        const room = roomName(device.room_id);
        const ambient = device.current?.ambient ?? device.current?.wiser_ambient;
        return `<div class="settings-row">
          <div class="sr-main">
            <div class="sr-title">${escapeHtml(device.name)} <span class="pill ${isWiser ? "pill-accent" : "pill-ghost"}" style="margin-left:6px">${isWiser ? "Wiser" : "Atlantic"}</span></div>
            <div class="sr-sub">
              ${escapeHtml(room || "Sans pièce")} · ${fmtTemp(ambient)} °C
              ${Number(device.enabled) === 0 ? " · pilotage désactivé" : ""}
              ${device.current?.available === false ? " · hors ligne" : ""}
            </div>
          </div>
          <div class="sr-control">
            <button class="icon-btn" type="button" data-edit-device="${device.device_id}" aria-label="Modifier ${escapeHtml(device.name)}">${icon("edit", 18)}</button>
            <button class="icon-btn" type="button" data-delete-device="${device.device_id}" aria-label="Retirer ${escapeHtml(device.name)}">${icon("trash", 18)}</button>
          </div>
        </div>`;
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

  renderOne(atlanticHost, atlanticDevices, "Synchronise le compte Atlantic pour découvrir les appareils.");
  renderOne(wiserHost, wiserDevices, "Configure le Hub Wiser puis synchronise pour découvrir les zones.");
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
    </div>
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
      await put(`/api/devices/${device.device_id}`, {
        name: val("[data-name]").value.trim(),
        device_type: val("[data-device-type]").value,
        room_id: val("[data-room]").value || null,
        position: number("[data-position]"),
        wiser_room_id: number("[data-wiser-room]"),
        enabled: val("[data-enabled]").checked ? 1 : 0,
        temp_sensor_device_id: number("[data-temp-sensor]"),
        cap_mode: number("[data-cap-mode]"),
        cap_setpoint: number("[data-cap-setpoint]"),
        cap_setpoint_cool: number("[data-cap-setpoint-cool]"),
        cap_ambient: number("[data-cap-ambient]"),
        cap_prog: number("[data-cap-prog]"),
      });
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
    ${system.process_rss ? `<div class="row-between"><span class="tiny muted">Mémoire MeCasa</span><span class="tiny mono">${bytes(system.process_rss)}</span></div>` : ""}
    ${disk ? `<div class="row-between"><span class="tiny muted">Carte SD</span><span class="tiny mono">${bytes(disk.free)} libres · ${disk.percent} % utilisé</span></div>` : ""}
  `;
}

/* --------------------------------------------------------------- serveur */

async function exportData(event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const payload = await get("/api/export");
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `mecasa-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast("Sauvegarde téléchargée.");
  } catch (err) {
    toastError(err);
  } finally {
    button.disabled = false;
  }
}

async function importData(event) {
  const input = event.currentTarget;
  const file = input.files?.[0];
  if (!file) return;

  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    if (!confirm("Importer cette sauvegarde ? Les données actuelles seront remplacées.")) {
      input.value = "";
      return;
    }
    await post("/api/import", payload);
    await refreshState({ silent: true });
    await refreshConfig({ silent: true });
    renderRooms();
    renderDevices();
    renderStorage();
    toast("Sauvegarde importée.");
  } catch (err) {
    toastError(err);
  } finally {
    input.value = "";
  }
}

async function restartServer(button) {
  if (!confirm("Redémarrer MeCasa ? L'interface sera indisponible quelques secondes.")) return;
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
  if (!confirm("Arrêter le serveur MeCasa ? Tu devras le relancer à la main.")) return;
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
        toast("MeCasa est de retour.");
        return;
      }
    } catch {
      /* le serveur redémarre encore */
    }
  }
  toast("Le serveur ne répond pas. Vérifie le journal système.", { type: "error", timeout: 8000 });
}

/* --------------------------------------------------------------- tailscale */

function openTailscaleHelp() {
  const sheet = openSheet({
    title: "Configurer Tailscale",
    sub: "Guide local, étape par étape",
    footer: `<button class="btn btn-primary" type="button" data-help-refresh>${icon("refresh", 15)} Actualiser le diagnostic</button>`,
    body: `<div class="stack">
      <p class="hint">Objectif : que l'app Android ouvre <strong>https://mecasa.&lt;tailnet&gt;.ts.net/</strong> (port 443). Toutes les commandes se lancent <strong>sur la machine qui fait tourner MeCasa</strong>. Chaque étape se termine par une vérification : passe à la suivante seulement quand elle est verte.</p>

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
        <div><span class="help-os-label">Linux / Raspberry Pi</span><code class="help-code">curl -fsSL https://tailscale.com/install.sh | sh</code></div>
        <div class="help-check"><strong>À vérifier :</strong> <code>tailscale version</code> affiche un numéro de version. Sur Windows, MeCasa détecte automatiquement <code>C:\\Program Files\\Tailscale\\tailscale.exe</code> même s'il n'est pas dans le PATH.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">2</span>
          <div>
            <div class="help-step-title">Connecter la machine avec le nom « mecasa »</div>
            <div class="help-step-sub">C'est ce nom que l'app Android résout. Si un lien d'autorisation s'affiche, ouvre-le et valide la machine dans ton compte Tailscale.</div>
          </div>
        </div>
        <div><span class="help-os-label">Linux / macOS</span><code class="help-code">sudo tailscale up --hostname=mecasa</code></div>
        <div><span class="help-os-label">Windows</span><code class="help-code">tailscale up --hostname=mecasa</code></div>
        <div class="help-check"><strong>À vérifier :</strong> <code>tailscale status</code> montre une ligne avec <code>mecasa</code> et une adresse <code>100.x.y.z</code>. <code>tailscale ip -4</code> renvoie cette même adresse.</div>
        <div class="help-warn"><strong>Nom déjà pris :</strong> si le nœud s'appelle <code>mecasa-1</code>, le nom <code>mecasa</code> est réservé ailleurs (autre machine ou <strong>Service Tailscale</strong>). La commande réussit sans rien changer : libère le nom dans la console → Machines (supprimer l'ancien nœud) ou → Services (supprimer le service), puis relance-la.</div>
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
        <div class="help-check"><strong>À vérifier :</strong> dans MeCasa, Réglages → Tailscale, les lignes <strong>MagicDNS</strong> et <strong>HTTPS Certificates</strong> passent au vert après <strong>Actualiser</strong>. Sinon, attends 30 s (émission du certificat) et réessaie.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">4</span>
          <div>
            <div class="help-step-title">Publier MeCasa en HTTPS sur le port 443</div>
            <div class="help-step-sub">Expose la racine du tailnet vers MeCasa, qui écoute en local sur 8780.</div>
          </div>
        </div>
        <div><span class="help-os-label">Linux / macOS</span><code class="help-code">sudo tailscale serve --bg --set-path=/ http://127.0.0.1:8780</code></div>
        <div><span class="help-os-label">Windows</span><code class="help-code">tailscale serve --bg --set-path=/ http://127.0.0.1:8780</code></div>
        <div class="help-check"><strong>À vérifier :</strong> <code>tailscale serve status</code> affiche <code>https://mecasa.&lt;tailnet&gt;.ts.net (tailnet only)</code> puis <code>/ proxy http://127.0.0.1:8780</code>.</div>
        <div class="help-warn"><strong>Prérequis :</strong> MeCasa doit écouter sur <code>127.0.0.1</code> ou <code>0.0.0.0</code> (Réglages → Réseau), jamais sur une IP LAN précise.</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">5</span>
          <div>
            <div class="help-step-title">Tester depuis le téléphone</div>
            <div class="help-step-sub">Le téléphone doit avoir Tailscale installé et connecté au même compte.</div>
          </div>
        </div>
        <div class="help-faq-a">Ouvre <code>https://mecasa.&lt;tailnet&gt;.ts.net/</code> — sans <code>:8780</code>, sans <code>http://</code>.</div>
        <div class="help-check"><strong>À vérifier :</strong> la page MeCasa se charge et le cadenas HTTPS est valide. Si le nom ne résout pas, teste d'abord <code>https://100.x.y.z/</code> (adresse de l'étape 2).</div>
      </div>

      <div class="help-step">
        <div class="help-step-head">
          <span class="help-step-num">6</span>
          <div>
            <div class="help-step-title">Rendre la configuration durable</div>
            <div class="help-step-sub">Pour que tout revienne seul après un redémarrage.</div>
          </div>
        </div>
        <div class="help-faq-a">• <code>--bg</code> conserve déjà la publication après un redémarrage du service Tailscale.<br>• Dans MeCasa : active <strong>Activer Tailscale</strong> et <strong>Publier automatiquement</strong>, puis <strong>Enregistrer</strong>.<br>• Optionnel : génère une <a href="https://login.tailscale.com/admin/settings/keys" target="_blank" rel="noreferrer">clé d'authentification</a> et colle-la dans le champ prévu.</div>
        <div class="help-check"><strong>À vérifier :</strong> après redémarrage de la machine, <code>tailscale serve status</code> et <code>https://mecasa.&lt;tailnet&gt;.ts.net/</code> fonctionnent encore.</div>
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
          <div><div class="help-faq-q">La page répond 502 ou « connection refused »</div><div class="help-faq-a">MeCasa n'écoute pas sur l'adresse attendue, ou le port a changé. Vérifie <code>host</code>/<code>port</code> dans Réglages → Réseau, puis republie avec le bon port.</div></div>
          <div><div class="help-faq-q">La page répond 404 ou « Not Found »</div><div class="help-faq-a">La publication vise un sous-chemin. Republie avec <code>--set-path=/</code>.</div></div>
          <div><div class="help-faq-q">Le nom MagicDNS ne résout pas sur le téléphone</div><div class="help-faq-a">MagicDNS est désactivé sur le tailnet, ou le téléphone n'est pas connecté. En attendant, utilise l'adresse <code>100.x.y.z</code>.</div></div>
          <div><div class="help-faq-q">Le nœud n'a pas le bon nom</div><div class="help-faq-a">Relance <code>tailscale up --hostname=mecasa</code>, ou renomme la machine dans la console → Machines.</div></div>
          <div><div class="help-faq-q">Le nœud s'appelle « mecasa-1 »</div><div class="help-faq-a">Le nom <code>mecasa</code> est déjà pris : soit une ancienne machine, soit un <strong>Service Tailscale</strong> (visible dans le diagnostic, ligne « Service Tailscale »). Supprime-le dans <a href="https://login.tailscale.com/admin/services" target="_blank" rel="noreferrer">console → Services</a> (ou l'ancienne machine dans console → Machines), puis relance <code>tailscale up --hostname=mecasa</code>.</div></div>
          <div><div class="help-faq-q">Retirer l'accès distant</div><div class="help-faq-a"><code>sudo tailscale serve reset</code> (sans <code>sudo</code> sur Windows). Le nœud reste connecté.</div></div>
        </div>
      </div>

      <p class="hint mt-2">Liens utiles : <a href="https://login.tailscale.com/admin/dns" target="_blank" rel="noreferrer">DNS du tailnet</a> · <a href="https://login.tailscale.com/admin/settings/keys" target="_blank" rel="noreferrer">Clés d'authentification</a> · <a href="https://tailscale.com/download" target="_blank" rel="noreferrer">Installer Tailscale</a> · <a href="https://tailscale.com/kb/1242/tailscale-serve" target="_blank" rel="noreferrer">Documentation serve</a></p>
    </div>`,
  });
  sheet?.footer?.querySelector("[data-help-refresh]")?.addEventListener("click", () => {
    sheet.close();
    loadTailscale();
  });
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
      : `<p class="hint is-ok mt-1">Tout est prêt : l'app peut charger ${escapeHtml(url || "https://mecasa.<tailnet>.ts.net/")}.</p>`;
  const pageHost = String(location.hostname || "").toLowerCase();
  const nodeDns = String(data.dns_name || "").toLowerCase();
  const nodeHost = nodeDns ? nodeDns.split(".")[0] : "";
  const ipLike = /^(\d{1,3}\.){3}\d{1,3}$/.test(pageHost) || pageHost.includes(":");
  const localHost = pageHost === "localhost" || pageHost === "127.0.0.1" || pageHost === "::1";
  const wrongHost = Boolean(nodeHost && pageHost && !ipLike && !localHost && pageHost !== nodeHost);
  const hostWarning = wrongHost
    ? `<p class="hint is-bad mt-1">Cette page est ouverte via <strong>${escapeHtml(pageHost)}</strong>, alors que le nœud Tailscale est <strong>${escapeHtml(nodeDns)}</strong>. Les actions risquent d'échouer (erreur 501) : utilise ${url ? `<a href="${escapeHtml(url)}">${escapeHtml(url)}</a>` : "l'URL du nœud"}.</p>`
    : "";
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
    ${!data.installed ? '<p class="hint mt-1">Installe Tailscale sur le Raspberry Pi : <code>curl -fsSL https://tailscale.com/install.sh | sh</code></p>' : ""}
  `;
}

async function tailscaleAction(path) {
  const button = root?.querySelector(path === "up" ? "#tsUp" : path === "serve/enable" ? "#tsServeOn" : "#tsServeOff");
  if (button) button.disabled = true;
  let failure = null;
  try {
    const payload = await post(`/api/tailscale/${path}`);
    if (payload.warning) {
      toast(payload.warning, { type: "error", timeout: 10000 });
    } else if (path === "serve/disable") {
      toast("Publication retirée. Accès distant coupé : republie depuis le réseau local.", {
        type: "info",
        timeout: 7000,
      });
    } else {
      const message = payload.url
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
