// Santé : état du serveur eCasa, matériel, alimentation, services et stockage.

import { get } from "../api.js";
import { chartLegend, lineChart } from "../charts.js";
import { icon } from "../icons.js";
import {
  bytes,
  escapeHtml,
  fmtDateTime,
  fmtNumber,
  fmtPercent,
  fmtTime,
  uptime,
} from "../utils.js";

let root = null;
let requestId = 0;

export default {
  title: "Santé",

  async mount(view) {
    root = view;
    view.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div>
            <h1 class="page-title">Santé</h1>
            <p class="page-sub">État du serveur, du matériel et des services.</p>
          </div>
          <div class="page-actions">
            <button class="btn btn-sm" type="button" id="healthRefresh">${icon("refresh", 15)} Actualiser</button>
          </div>
        </div>
        <div id="healthContent">
          <div class="boot" style="min-height:240px"><span class="spinner"></span><p>Chargement de la santé du serveur…</p></div>
        </div>
      </div>
    `;

    view.querySelector("#healthRefresh")?.addEventListener("click", load);

    await load();
  },

  unmount() {
    requestId += 1;
    root = null;
  },
};

async function load() {
  if (!root) return;
  const host = root.querySelector("#healthContent");
  if (!host) return;

  const id = ++requestId;
  host.innerHTML = `<div class="boot" style="min-height:240px"><span class="spinner"></span><p>Chargement de la santé du serveur…</p></div>`;

  try {
    const health = await get("/api/system/health");
    if (id !== requestId) return;
    // Métriques CPU (60 points) et Tailscale sont facultatives : un échec
    // sur l'un d'eux ne doit pas masquer le reste de la page.
    const [state, tailscale] = await Promise.all([
      get("/api/state").catch(() => null),
      get("/api/tailscale/status").catch(() => null),
    ]);
    if (id !== requestId) return;
    paint(health, state?.cpu, tailscale);
  } catch (err) {
    if (id !== requestId) return;
    host.innerHTML = `<div class="card card-pad">
      <div class="hint is-bad">${escapeHtml(err.message)}</div>
      <button class="btn btn-sm mt-2" type="button" id="healthRetry">${icon("refresh", 15)} Réessayer</button>
    </div>`;
    root.querySelector("#healthRetry")?.addEventListener("click", load);
  }
}

function paint(health, cpu, tailscale) {
  const host = root?.querySelector("#healthContent");
  if (!host || !health) return;

  const app = health.app || {};
  const system = health.system || {};
  const power = health.power || {};
  const services = health.services || {};
  const engine = services.engine || {};
  const pronote = services.pronote || {};
  const tokens = services.tokens || {};
  const atlantic = tokens.atlantic || {};
  const storage = health.storage || {};
  const update = health.update || {};

  host.innerHTML = `
    ${cardApp(app)}
    ${cardSystem(system, cpu)}
    ${cardPower(power)}
    ${cardServices(engine, pronote, atlantic)}
    ${cardStorage(storage)}
    ${cardUpdate(update)}
    ${tailscale ? cardTailscale(tailscale) : ""}
  `;
}

function kpi(iconName, label, value, sub = "") {
  return `<div class="kpi">
    <div class="k-label">${icon(iconName, 14)} ${escapeHtml(label)}</div>
    <div class="k-value">${escapeHtml(value)}</div>
    ${sub ? `<div class="k-sub">${escapeHtml(sub)}</div>` : ""}
  </div>`;
}

function cardShell(iconName, title, sub, pill, body) {
  return `<div class="card mt-2">
    <div class="card-head">
      <div>
        <div class="card-title">${icon(iconName, 18)} ${escapeHtml(title)}</div>
        ${sub ? `<div class="card-sub">${escapeHtml(sub)}</div>` : ""}
      </div>
      ${pill || ""}
    </div>
    <div class="card-body">${body}</div>
  </div>`;
}

function cardApp(app) {
  const mock = Boolean(app.mock);
  return cardShell(
    "server",
    "Application",
    `PID ${app.pid ?? "—"} · démarrée le ${fmtDateTime(app.started_at)}`,
    `<span class="pill ${mock ? "pill-warn" : "pill-ok"}">${mock ? "Simulation" : "Réel"}</span>`,
    `<div class="kpi-grid">
      ${kpi("tag", "Version", app.version ?? "—")}
      ${kpi("history", "Uptime", app.uptime != null ? uptime(app.uptime) : "—", `${fmtNumber(app.uptime ?? 0)} s`)}
      ${kpi("clock", "Démarrée à", fmtDateTime(app.started_at))}
      ${kpi("server", "PID", app.pid ?? "—")}
    </div>`
  );
}

function cardSystem(system, cpu) {
  const memory = system.memory;
  const disk = system.disk;
  const ts = Array.isArray(cpu?.ts) ? cpu.ts : [];
  const load = Array.isArray(cpu?.load) ? cpu.load : [];
  const temp = Array.isArray(cpu?.temp) ? cpu.temp : [];
  const series = [
    { key: "load", label: "Charge CPU (%)", color: "var(--primary)", values: load, area: true },
    { key: "temp", label: "Température (°C)", color: "var(--warm)", values: temp, y2: true },
  ];
  return cardShell(
    "gauge",
    "Système",
    `Uptime hôte ${system.uptime != null ? uptime(system.uptime) : "—"}`,
    "",
    `<div style="margin-bottom:12px">
      ${lineChart({ series, labels: ts.map((t) => fmtTime(t)), height: 220, unit: "%" })}
      ${chartLegend(series)}
    </div>
    <div class="kpi-grid">
      ${kpi("history", "Uptime hôte", system.uptime != null ? uptime(system.uptime) : "—")}
      ${kpi(
        "database",
        "Mémoire",
        memory ? `${bytes(memory.used)} / ${bytes(memory.total)}` : "—",
        memory ? fmtPercent(memory.percent) : "donnée indisponible"
      )}
      ${kpi(
        "save",
        "Disque",
        disk ? `${bytes(disk.used)} / ${bytes(disk.total)}` : "—",
        disk ? `${fmtPercent(disk.percent)} · ${disk.path || ""}` : "donnée indisponible"
      )}
      ${kpi("thermometer", "Température CPU", system.cpu_temp != null ? `${fmtNumber(system.cpu_temp, 1)} °C` : "—")}
    </div>`
  );
}

function cardPower(power) {
  if (!power.supported) {
    return cardShell(
      "zap",
      "Alimentation",
      "",
      '<span class="pill pill-ghost">Non supporté</span>',
      `<p class="hint">La mesure d'alimentation (vcgencmd) n'est pas disponible sur ce serveur.</p>`
    );
  }
  const incidents = Array.isArray(power.incidents) ? power.incidents : [];
  const alert = Boolean(power.undervoltage_history);
  return cardShell(
    "zap",
    "Alimentation",
    `Lecture brute : ${power.raw ?? "—"}`,
    `<span class="pill ${power.ok && !alert ? "pill-ok" : "pill-warn"}">${power.ok && !alert ? "Normal" : "Attention"}</span>`,
    `${alert ? `<p class="hint is-bad">Historique de sous-tension détecté : vérifie l'alimentation (chargeur/câble) du serveur.</p>` : ""}
    ${incidents.length
      ? `<ul class="hint mt-1">${incidents.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : `<p class="hint is-ok">${alert ? "Aucun incident actif, mais un historique existe." : "Aucun incident signalé."}</p>`}`
  );
}

function cardServices(engine, pronote, atlantic) {
  const running = Boolean(engine.running);
  const expires = atlantic.expires_in_secondes;
  return cardShell(
    "hub",
    "Services",
    "",
    `<span class="pill ${running ? "pill-ok" : "pill-warn"}">${running ? "Moteur actif" : "Moteur arrêté"}${engine.mock ? " · simulation" : ""}</span>`,
    `<div class="kpi-grid">
      ${kpi("activity", "Dernière lecture", engine.last_poll ? fmtDateTime(engine.last_poll) : "—")}
      ${kpi("school", "PRONOTE", pronote.last_sync_day ? `${pronote.last_sync_day}${pronote.is_today ? " (aujourd'hui)" : ""}` : "jamais synchronisé",
        pronote.pronote_enabled ? `activé${pronote.pronote_auto_enabled ? ` · auto toutes les ${pronote.pronote_sync_minutes ?? "?"} min` : " · manuel"}` : "désactivé")}
      ${kpi("key", "Jeton Atlantic", expires != null ? `expire dans ${uptime(expires)}` : "—", `${fmtNumber(expires ?? 0)} s restantes`)}
    </div>`
  );
}

function cardStorage(storage) {
  return cardShell(
    "database",
    "Stockage",
    `${escapeHtml(String(storage.mode || "mémoire + snapshots"))} · ${escapeHtml(String(storage.state_path || ""))}`,
    "",
    `<div class="kpi-grid">
      ${kpi("save", "État", bytes(storage.state_size), `${fmtNumber(storage.state_writes ?? 0)} écritures`)}
      ${kpi("history", "Historique", bytes(storage.history_size), `${fmtNumber(storage.history_files ?? 0)} fichier(s) · ${fmtNumber(storage.history_buckets ?? 0)} seaux`)}
      ${kpi("clock", "En attente", fmtNumber(storage.pending_history ?? 0), "écritures en file")}
      ${kpi("list", "Écritures historique", fmtNumber(storage.history_writes ?? 0))}
    </div>`
  );
}

function cardUpdate(update) {
  const available = Boolean(update.available);
  const body = available
    ? `<div class="kpi-grid">
        ${kpi("alert", "Mise à jour disponible", `${fmtNumber(update.behind ?? 0)} commit(s) en attente`)}
        ${kpi("tag", "Version actuelle", update.current || "—")}
        ${kpi("git", "Version distante", update.remote || "—", update.branch ? `branche ${update.branch}` : "")}
        ${kpi("clock", "Vérifiée le", fmtDateTime(update.checked_at))}
      </div>`
    : `<p class="hint is-ok">${update.error ? `Vérification en échec : ${escapeHtml(String(update.error))}` : "Aucune mise à jour disponible."}${update.checked_at ? ` (vérifié le ${fmtDateTime(update.checked_at)})` : ""}</p>`;
  return cardShell(
    "download",
    "Mise à jour",
    "",
    `<span class="pill ${available ? "pill-warn" : "pill-ok"}">${available ? "Disponible" : "À jour"}</span>`,
    body
  );
}

function cardTailscale(ts) {
  const stateLabel = !ts.installed
    ? "Non installé"
    : ts.running
      ? "Connecté"
      : ts.backend_state || "Arrêté";
  const url = ts.https_url || ts.serve_url || "";
  return cardShell(
    "wifi",
    "Tailscale",
    "",
    `<span class="pill ${ts.running ? "pill-ok" : "pill-ghost"}">${escapeHtml(stateLabel)}</span>`,
    `<div class="kpi-grid">
      ${kpi("wifi", "Adresse", ts.ip || "—", ts.dns_name ? String(ts.dns_name) : "")}
      ${kpi("link", "URL", url || "—")}
      ${kpi("shield", "Serve HTTPS", ts.https_ready ? "prêt" : ts.serve_active ? "actif" : "inactif", ts.hostname ? `hôte ${ts.hostname}` : "")}
      ${kpi("info", "État", stateLabel, ts.enabled === false ? "désactivé dans la configuration" : "")}
    </div>`
  );
}
