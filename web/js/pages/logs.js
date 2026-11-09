// Journal : les derniers événements du moteur, filtrables et exportables.

import { icon } from "../icons.js";
import { refreshState, store } from "../state.js";
import { toast, toastError } from "../toast.js";
import { deviceName, escapeHtml, fmtDate, fmtTime } from "../utils.js";

const FILTERS = [
  ["all", "Tout"],
  ["info", "Infos"],
  ["erreur", "Erreurs"],
];

const LEVEL_ICON = {
  erreur: "alert",
  action: "check",
  info: "info",
};

let root = null;
let filter = "all";
let search = "";

export default {
  title: "Journal",

  async mount(view) {
    root = view;

    view.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div>
            <h1 class="page-title">Journal</h1>
            <p class="page-sub" id="logsSub">Les derniers événements du moteur.</p>
          </div>
          <div class="page-actions">
            <button class="btn" type="button" id="logsDownload">${icon("download", 17)} Exporter</button>
            <button class="btn btn-primary" type="button" id="logsRefresh">${icon("refresh", 17)} Actualiser</button>
          </div>
        </div>

        <div class="card card-pad">
          <div class="row-between wrap" style="gap:10px">
            <div class="segmented" id="logsFilter">
              ${FILTERS.map(
                ([key, label]) =>
                  `<button type="button" data-filter="${key}" class="${key === filter ? "is-active" : ""}">${label}</button>`
              ).join("")}
            </div>
            <label class="field" style="min-width:min(260px,70vw)">
              <span class="sr-only">Rechercher dans le journal</span>
              <input class="input" type="search" id="logsSearch" placeholder="Rechercher un mot, une pièce…" value="${escapeHtml(search)}">
            </label>
          </div>
        </div>

        <div class="kpi-grid" id="logsStats"></div>
        <div id="logsError"></div>

        <div class="card mt-2">
          <div class="card-head">
            <div class="card-title">${icon("list", 18)} Événements</div>
            <span class="tiny muted" id="logsCount"></span>
          </div>
          <div class="log-list" id="logsList"></div>
        </div>

        <p class="hint mt-2">
          Le journal garde les 200 derniers événements en mémoire et les inclut dans le snapshot.
          Les erreurs sont aussi visibles sur l'accueil.
        </p>
      </div>
    `;

    view.querySelectorAll("[data-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        filter = button.dataset.filter;
        view.querySelectorAll("[data-filter]").forEach((item) => {
          item.classList.toggle("is-active", item.dataset.filter === filter);
        });
        render();
      });
    });

    const searchInput = view.querySelector("#logsSearch");
    searchInput?.addEventListener("input", () => {
      search = searchInput.value.trim();
      render();
    });

    view.querySelector("#logsRefresh")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await refreshState({ silent: false });
        render();
      } finally {
        button.disabled = false;
      }
    });

    view.querySelector("#logsDownload")?.addEventListener("click", download);

    render();
  },

  unmount() {
    root = null;
  },

  onState() {
    render();
  },
};

function entries() {
  return Array.isArray(store.state?.logs) ? store.state.logs : [];
}

function filtered(list) {
  const needle = search.toLocaleLowerCase("fr");
  return list.filter((entry) => {
    if (filter !== "all" && entry.level !== filter) return false;
    if (!needle) return true;
    const device = entry.device_id ? deviceName(store.state, entry.device_id) : "";
    return `${entry.message || ""} ${device}`.toLocaleLowerCase("fr").includes(needle);
  });
}

function render() {
  if (!root) return;
  const all = entries();
  const list = filtered(all);

  const errors = all.filter((entry) => entry.level === "erreur").length;
  const warnings = all.filter((entry) => entry.level === "avertissement").length;

  const stats = root.querySelector("#logsStats");
  if (stats) {
    stats.innerHTML = `
      <div class="kpi">
        <div class="k-label">${icon("list", 14)} Événements</div>
        <div class="k-value">${all.length}</div>
        <div class="k-sub">en mémoire</div>
      </div>
      <div class="kpi">
        <div class="k-label">${icon("alert", 14)} Erreurs</div>
        <div class="k-value"${errors ? ' style="color:var(--bad)"' : ""}>${errors}</div>
        <div class="k-sub">${errors ? "à vérifier" : "aucune"}</div>
      </div>
      <div class="kpi">
        <div class="k-label">${icon("info", 14)} Avertissements</div>
        <div class="k-value">${warnings}</div>
        <div class="k-sub">non bloquants</div>
      </div>
      <div class="kpi">
        <div class="k-label">${icon("clock", 14)} Dernier</div>
        <div class="k-value">${all[0]?.ts ? fmtTime(all[0].ts) : "—"}</div>
        <div class="k-sub">${all[0]?.ts ? fmtDate(all[0].ts) : "aucun événement"}</div>
      </div>
    `;
  }

  const errorHost = root.querySelector("#logsError");
  if (errorHost) {
    const lastError = store.state?.status?.last_error;
    errorHost.innerHTML = lastError
      ? `<div class="card card-pad mt-2" style="border-color:color-mix(in srgb, var(--bad) 32%, var(--line));background:var(--bad-soft)">
          <div class="row" style="gap:9px;align-items:flex-start">
            <span style="color:var(--bad)">${icon("alert", 18)}</span>
            <div><strong style="font-size:13px">Dernière erreur moteur</strong><p class="hint mt-1">${escapeHtml(lastError)}</p></div>
          </div>
        </div>`
      : "";
  }

  const count = root.querySelector("#logsCount");
  if (count) {
    count.textContent = `${list.length} affiché${list.length > 1 ? "s" : ""} sur ${all.length}`;
  }

  const sub = root.querySelector("#logsSub");
  if (sub) {
    sub.textContent = errors
      ? `${errors} erreur${errors > 1 ? "s" : ""} dans le journal récent.`
      : "Aucune erreur récente. Le moteur tourne tranquillement.";
  }

  const host = root.querySelector("#logsList");
  if (!host) return;

  if (!list.length) {
    host.innerHTML = `<div class="empty" style="padding:34px 16px">
      <span class="empty-icon">${icon(all.length ? "search" : "list", 24)}</span>
      <strong>${all.length ? "Aucun événement ne correspond" : "Le journal est vide"}</strong>
      <p>${all.length ? "Essaie un autre filtre ou un autre mot-clé." : "Les actions du moteur apparaîtront ici au fil de l'eau."}</p>
    </div>`;
    return;
  }

  let currentDay = "";
  const rows = [];

  for (const entry of list) {
    const day = String(entry.ts || "").slice(0, 10);
    if (day && day !== currentDay) {
      currentDay = day;
      rows.push(
        `<div class="subhead" style="padding:12px var(--pad) 6px;font-size:11px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:var(--faint)">${escapeHtml(
          fmtDate(entry.ts, { weekday: "long", day: "numeric", month: "long" })
        )}</div>`
      );
    }

    const level = entry.level || "info";
    const glyph = LEVEL_ICON[level] || "info";
    const device = entry.device_id ? deviceName(store.state, entry.device_id) : "";

    rows.push(`<div class="log-item" data-level="${escapeHtml(level)}">
      <span class="log-time">${entry.ts ? fmtTime(entry.ts, true) : "—"}</span>
      <span class="log-icon">${icon(glyph, 14)}</span>
      <span class="log-msg">
        ${escapeHtml(entry.message || "")}
        ${device ? `<span class="log-device">${escapeHtml(device)}</span>` : ""}
      </span>
    </div>`);
  }

  host.innerHTML = rows.join("");
}

function download() {
  const list = entries();
  if (!list.length) {
    toast("Le journal est vide.", { type: "info" });
    return;
  }
  try {
    const blob = new Blob([JSON.stringify(list, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `mecasa-journal-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast("Journal exporté.");
  } catch (err) {
    toastError(err);
  }
}
