// Point d'entrée : thème, navigation, routeur par hash, polling.

import { icon, weatherIconName } from "./icons.js";
import { bootstrap, refreshState, setPref, store, subscribe } from "./state.js";
import { $, fmtTime } from "./utils.js";
import { toast } from "./toast.js";

import home from "./pages/home.js";
import planning from "./pages/planning.js";
import models from "./pages/models.js";
import stats from "./pages/stats.js";
import settings from "./pages/settings.js";
import logs from "./pages/logs.js";

// Le journal n'est plus dans le menu principal : il s'ouvre depuis
// Réglages → Journal (route #/logs conservée).
const PAGES = { home, planning, models, stats, settings, logs };

const NAV = [
  { id: "home", label: "Accueil", icon: "home" },
  { id: "planning", label: "Planning", icon: "calendar" },
  { id: "models", label: "Modèles", icon: "sliders" },
  { id: "stats", label: "Stats", icon: "chart" },
  { id: "settings", label: "Réglages", icon: "settings" },
];

let activePage = null;
let activeId = null;
let currentTheme = "auto";
let mediaQuery = null;
let deferredInstall = null;

/* --------------------------------------------------------------- thème */

function resolveTheme(theme) {
  if (theme === "auto") {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return theme;
}

function applyTheme(theme) {
  currentTheme = theme;
  document.documentElement.dataset.theme = resolveTheme(theme);
  const button = $("#themeBtn");
  if (button) {
    const dark = resolveTheme(theme) === "dark";
    button.innerHTML = icon(dark ? "sun" : "moon", 20);
    button.setAttribute("aria-label", dark ? "Passer en thème clair" : "Passer en thème sombre");
    button.title = theme === "auto" ? "Thème automatique" : dark ? "Thème sombre" : "Thème clair";
  }
}

function cycleTheme() {
  const order = ["auto", "light", "dark"];
  const next = order[(order.indexOf(currentTheme) + 1) % order.length];
  setPref("theme", next);
  applyTheme(next);
  toast(
    next === "auto"
      ? "Thème automatique activé."
      : next === "dark"
        ? "Thème sombre activé."
        : "Thème clair activé.",
    { type: "info", timeout: 2200 }
  );
}

/* --------------------------------------------------------------- navigation */

function renderNav() {
  const sidebar = $("#sidebarNav");
  const tabbar = $("#tabbar");

  if (sidebar) {
    sidebar.innerHTML = NAV.map(
      (item) => `<a href="#/${item.id}" data-nav="${item.id}">
        ${icon(item.icon, 20)}<span>${item.label}</span>
        ${item.id === "settings" ? '<span class="nav-badges"><span class="nav-badge is-warn" id="navUpdateBadge" hidden>!</span><span class="nav-badge" id="navLogBadge" hidden>0</span></span>' : ""}
      </a>`
    ).join("");
    // Clic sur l'entree deja active : on remonte la page (utile pour revenir
    // a la liste des modeles pendant l'edition d'un modele).
    sidebar.querySelectorAll("[data-nav]").forEach((link) => {
      link.addEventListener("click", (event) => {
        if (link.dataset.nav !== activeId) return;
        event.preventDefault();
        activeId = null;
        mountRoute();
      });
    });
  }

  if (tabbar) {
    tabbar.innerHTML = `<nav>${NAV.map(
      (item) => `<a href="#/${item.id}" data-nav="${item.id}">
        ${icon(item.icon, 22)}<span>${item.label}</span>
      </a>`
    ).join("")}</nav>`;
    tabbar.querySelectorAll("[data-nav]").forEach((link) => {
      link.addEventListener("click", (event) => {
        if (link.dataset.nav !== activeId) return;
        event.preventDefault();
        activeId = null;
        mountRoute();
      });
    });
  }
}

function markActive(id) {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    const active = link.dataset.nav === id;
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, "").trim();
  const [id] = hash.split("/");
  return PAGES[id] ? id : "home";
}

async function mountRoute() {
  const id = parseRoute();
  if (id === activeId) return;

  if (activePage?.unmount) {
    try {
      activePage.unmount();
    } catch (err) {
      console.error(err);
    }
  }

  activePage = PAGES[id];
  activeId = id;
  markActive(id);

  const view = $("#view");
  if (!view) return;
  view.innerHTML = "";

  try {
    await activePage.mount(view);
  } catch (err) {
    console.error(err);
    view.innerHTML = `<div class="page"><div class="card card-pad">
      <h1 class="page-title">Impossible d'afficher cette page</h1>
      <p class="page-sub">${escapeText(err.message || err)}</p>
      <button class="btn btn-primary mt-2" type="button" id="retryPage">${icon("refresh", 17)} Réessayer</button>
    </div></div>`;
    $("#retryPage")?.addEventListener("click", () => {
      activeId = null;
      mountRoute();
    });
  }

  view.scrollTop = 0;
  window.scrollTo({ top: 0, behavior: "instant" });
  document.title = `${activePage.title || "eCasa"} · eCasa`;
}

function escapeText(value) {
  const span = document.createElement("span");
  span.textContent = String(value ?? "");
  return span.innerHTML;
}

/* --------------------------------------------------------------- topbar */

function updateChrome() {
  const clock = $("#clock");
  if (clock) clock.textContent = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

  const weekPill = $("#weekPill");
  const state = store.state;
  if (weekPill) {
    if (state?.week) {
      weekPill.textContent = `Semaine ${state.week}`;
      weekPill.title = `Semaine ${state.week} · semaine ISO ${state.iso_week}`;
    } else {
      weekPill.textContent = "Semaine —";
    }
  }

  const dot = $("#statusDot");
  const label = $("#statusLabel");
  if (dot && label) {
    dot.classList.remove("is-ok", "is-warn", "is-bad");
    if (!store.connected) {
      dot.classList.add("is-bad");
      label.textContent = "Hors ligne";
      dot.title = store.error || "Connexion perdue";
    } else if (state?.status?.mock) {
      dot.classList.add("is-warn");
      label.textContent = "Simulation";
      dot.title = "Mode simulation : aucune commande réelle n'est envoyée.";
    } else if (state?.status && !state.status.running) {
      dot.classList.add("is-warn");
      label.textContent = "Moteur arrêté";
      dot.title = "Le moteur de planning est arrêté.";
    } else {
      dot.classList.add("is-ok");
      label.textContent = "En ligne";
      dot.title = state?.status?.last_poll ? `Dernière lecture ${fmtTime(state.status.last_poll)}` : "Connecté";
    }
  }

  const badge = $("#navLogBadge");
  if (badge) {
    const errors = (state?.logs || []).filter((entry) => entry.level === "erreur").length;
    // La pastille ne s'affiche que pour les erreurs *nouvelles* depuis la
    // dernière consultation du journal (vu → disparition jusqu'au prochain).
    const seen = Number(localStorage.getItem("ecasa.seenErrorCount") || 0);
    const unseen = Math.max(0, errors - (Number.isFinite(seen) ? seen : 0));
    badge.hidden = unseen === 0;
    badge.textContent = String(unseen);
    badge.title = unseen ? `${unseen} nouvelle(s) erreur(s) dans le journal` : "Aucune nouvelle erreur";
  }

  const updateBadge = $("#navUpdateBadge");
  if (updateBadge) {
    const update = state?.update;
    const available = Boolean(update?.available);
    updateBadge.hidden = !available;
    if (available) {
      const behind = Number(update?.behind || 0);
      updateBadge.title = `Mise à jour disponible${behind ? ` : ${behind} commit${behind > 1 ? "s" : ""} en attente` : ""} — cliquer pour ouvrir Réglages → Mise à jour`;
    } else {
      updateBadge.title = "Aucune mise à jour disponible";
    }
  }

  const sidebarStatus = $("#sidebarStatus");
  if (sidebarStatus) {
    if (!store.connected) {
      sidebarStatus.innerHTML = `<div class="sc-row"><span>Serveur</span><strong style="color:var(--bad)">hors ligne</strong></div>`;
    } else {
      const info = state?.storage;
      sidebarStatus.innerHTML = `
        <div class="sc-row"><span>Moteur</span><strong>${state?.status?.running ? "actif" : "arrêté"}</strong></div>
        <div class="sc-row"><span>Mode</span><strong>${state?.status?.mock ? "simulation" : "réel"}</strong></div>
        <div class="sc-row"><span>Écritures état</span><strong>${info?.state_writes ?? "—"}</strong></div>
      `;
    }
  }
}

/* --------------------------------------------------------------- PWA */

function setupInstall() {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstall = event;
    const button = $("#installBtn");
    if (button) button.hidden = false;
  });

  $("#installBtn")?.addEventListener("click", async () => {
    if (!deferredInstall) {
      toast("Utilise le menu du navigateur → « Installer l'application ».", { type: "info", timeout: 5000 });
      return;
    }
    deferredInstall.prompt();
    try {
      await deferredInstall.userChoice;
    } finally {
      deferredInstall = null;
      const button = $("#installBtn");
      if (button) button.hidden = true;
    }
  });

  window.addEventListener("appinstalled", () => {
    deferredInstall = null;
    const button = $("#installBtn");
    if (button) button.hidden = true;
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("/sw.js?v=99", { updateViaCache: "none" })
        .catch((err) => {
          console.warn("[ecasa] service worker", err);
        });
    });
    // Un nouveau service worker prend la main : on recharge une seule fois
    // pour charger les fichiers à jour au lieu de l'ancienne coquille.
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloading) return;
      reloading = true;
      location.reload();
    });
  }
}

/* --------------------------------------------------------------- démarrage */

function addThemeButton() {
  const host = $("#topbarStatus");
  const install = $("#installBtn");
  if (!host || $("#themeBtn")) return;

  const button = document.createElement("button");
  button.id = "themeBtn";
  button.type = "button";
  button.className = "icon-btn";
  button.addEventListener("click", cycleTheme);
  host.insertBefore(button, install || null);
  applyTheme(currentTheme);
}

async function refreshWeather() {
  try {
    const data = await fetch("/api/weather").then((r) => r.json());
    if (data?.ok && data.temp !== null && data.temp !== undefined) {
      window.ecasaWeather = data;
    } else {
      window.ecasaWeather = null;
    }
  } catch (err) {
    console.warn("[ecasa] weather", err);
    window.ecasaWeather = null;
  }
}

async function boot() {
  renderNav();
  $("#navLogBadge")?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    location.hash = "#/logs";
  });
  $("#navUpdateBadge")?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (activeId === "settings") {
      // Déjà dans Réglages : mountRoute ne rejouerait pas le lien profond.
      window.dispatchEvent(new CustomEvent("ecasa:jump", { detail: "update" }));
      if (location.hash !== "#/settings/update") {
        history.replaceState(null, "", "#/settings/update");
      }
    } else {
      location.hash = "#/settings/update";
    }
  });
  setupInstall();

  mediaQuery = window.matchMedia?.("(prefers-color-scheme: dark)");
  mediaQuery?.addEventListener?.("change", () => {
    if (currentTheme === "auto") applyTheme("auto");
  });

  applyTheme(store.prefs.theme || "auto");
  window.addEventListener("ecasa:theme", (event) => applyTheme(event.detail || "auto"));
  updateChrome();
  setInterval(updateChrome, 1000);

  window.addEventListener("hashchange", mountRoute);
  if (!location.hash) location.replace("#/home");

  const view = $("#view");
  if (view) {
    view.innerHTML = `<div class="boot"><span class="spinner" aria-hidden="true"></span><p>Connexion à la maison…</p></div>`;
  }

  // Listeners enregistrés tôt : la reprise réseau (polling 15 s, événement
  // « online ») peut rétablir l'application même si le premier bootstrap échoue.
  subscribe((_, reason) => {
    updateChrome();
    if (reason === "state") {
      activePage?.onState?.(store.state);
      // Premier succès après un échec au démarrage : on monte la route.
      if (store.state && activeId === null) mountRoute();
    }
  });
  window.addEventListener("online", () => refreshState({ silent: true }));

  try {
    await bootstrap();
  } catch (err) {
    console.error(err);
  }

  if (!store.state) {
    if (view) {
      view.innerHTML = `<div class="page"><div class="card card-pad">
        <h1 class="page-title">Serveur injoignable</h1>
        <p class="page-sub">eCasa n'arrive pas à joindre son serveur local. Vérifie qu'il est démarré, puis réessaie.</p>
        <button class="btn btn-primary mt-2" type="button" id="bootRetry">${icon("refresh", 17)} Réessayer</button>
      </div></div>`;
      $("#bootRetry")?.addEventListener("click", async () => {
        await refreshState({ silent: true });
        if (store.state && activeId === null) mountRoute();
        else if (!store.state) toast("Toujours injoignable.", { type: "error" });
      });
    }
    return;
  }

  if (activeId === null) mountRoute();
  window.refreshWeather = refreshWeather;
  refreshWeather();
  setInterval(refreshWeather, 30 * 60 * 1000);
}

boot();
