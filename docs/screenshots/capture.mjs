#!/usr/bin/env node
// Régénère les captures du README avec Edge/Chrome en mode headless.
//
// Prérequis : eCasa lancé en simulation (python -m ecasa.main) et Node 22+
// (WebSocket natif). Aucun paquet npm n'est nécessaire.
//
// Usage :
//   node docs/screenshots/capture.mjs
//   node docs/screenshots/capture.mjs --url http://127.0.0.1:8780
//
// Les images sont écrites dans ce dossier, puis recadrées à la taille de
// l'élément capturé (aucune donnée personnelle : instance de simulation).

import { spawn } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const argValue = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const BASE_URL = argValue("--url", "http://127.0.0.1:8780").replace(/\/$/, "");
const CDP_PORT = Number(argValue("--port", "9333"));

const EDGE_CANDIDATES = [
  process.env.EDGE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "/usr/bin/microsoft-edge",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
];

const browserPath = EDGE_CANDIDATES.find((item) => item && existsSync(item));
if (!browserPath) {
  console.error("Navigateur introuvable : définis EDGE_PATH vers msedge ou chrome.");
  process.exit(1);
}

const profile = join(tmpdir(), `ecasa-shots-${Date.now()}`);
const browser = spawn(browserPath, [
  "--headless=new",
  `--remote-debugging-port=${CDP_PORT}`,
  `--user-data-dir=${profile}`,
  "--window-size=1440,900",
  "--hide-scrollbars",
  "--no-first-run",
  "--no-default-browser-check",
  "--disable-extensions",
  "about:blank",
], { stdio: "ignore" });

const stop = () => {
  try { browser.kill(); } catch { /* déjà arrêté */ }
};
process.on("exit", stop);
process.on("SIGINT", () => { stop(); process.exit(1); });

async function target() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const pages = await response.json();
      const page = pages.find((item) => item.type === "page");
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch { /* navigateur pas encore prêt */ }
    await sleep(250);
  }
  throw new Error("Impossible de joindre le navigateur headless (CDP).");
}

const socket = new WebSocket(await target());
await new Promise((resolve, reject) => {
  socket.onopen = resolve;
  socket.onerror = () => reject(new Error("WebSocket CDP refusé"));
});
socket.onclose = () => {};

let sequence = 0;
const pending = new Map();
socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  const entry = pending.get(message.id);
  if (!entry) return;
  pending.delete(message.id);
  if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
  else entry.resolve(message.result);
};

const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});

await send("Page.enable");
await send("Runtime.enable");
await send("Page.addScriptToEvaluateOnNewDocument", {
  source: `try { localStorage.setItem("ecasa.theme", '"light"'); } catch {}`,
});

const metrics = (width, height, mobile = false) => send("Emulation.setDeviceMetricsOverride", {
  width, height, deviceScaleFactor: 1, mobile,
});

async function goto(hash, { width = 1440, height = 900, mobile = false, wait = 3000 } = {}) {
  await metrics(width, height, mobile);
  await send("Page.navigate", { url: `${BASE_URL}/${hash}` });
  await sleep(wait);
}

const evaluate = async (expression) => {
  const { result } = await send("Runtime.evaluate", { expression, returnByValue: true });
  return result?.value;
};

function save(name, base64) {
  const path = join(HERE, name);
  writeFileSync(path, Buffer.from(base64, "base64"));
  console.log(`écrit ${name}`);
}

async function viewportShot(name) {
  const { data } = await send("Page.captureScreenshot", { format: "png", fromSurface: true });
  save(name, data);
}

async function elementShot(name, selector, { width, height, mobile = false } = {}) {
  await metrics(width, height, mobile);
  await sleep(300);
  await evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return null;
    window.scrollTo(0, Math.max(0, el.getBoundingClientRect().top + window.scrollY - 76));
    return true;
  })()`);
  await sleep(500);
  const rect = await evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  })()`);
  if (!rect || !rect.width || !rect.height) {
    throw new Error(`Élément introuvable ou vide : ${selector}`);
  }
  const clip = {
    x: Math.max(0, Math.round(rect.x)),
    y: Math.max(0, Math.round(rect.y)),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    scale: 1,
  };
  const { data } = await send("Page.captureScreenshot", {
    format: "png", fromSurface: true, clip,
  });
  save(name, data);
}

// Accueil, planning, statistiques : captures pleine fenêtre 1440 × 900.
await goto("#/home");
await viewportShot("home.png");

await goto("#/planning", { wait: 2500 });
await viewportShot("planning.png");

await goto("#/stats", { wait: 4000 });
await viewportShot("stats.png");

// Carte « Consommation électrique » seule.
await elementShot("stats-consommation.png", "#energyContent .card", { width: 1440, height: 1400 });

// Section Pronote complète (Réglages → Pronote).
await goto("#/settings/pronote", { wait: 2500 });
await elementShot("settings-pronote.png", "#sec-pronote", { width: 1240, height: 1600 });

// Vue mobile (PWA).
await goto("#/home", { width: 480, height: 900, mobile: true, wait: 3000 });
await viewportShot("mobile-home.png");

socket.close();
stop();
