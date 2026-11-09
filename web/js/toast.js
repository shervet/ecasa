import { icon } from "./icons.js";

const stack = () => document.getElementById("toastStack");

export function toast(message, { type = "ok", timeout = 4200 } = {}) {
  const host = stack();
  if (!host) return () => {};

  const node = document.createElement("div");
  node.className = `toast${type === "error" ? " is-error" : ""}`;
  node.setAttribute("role", type === "error" ? "alert" : "status");

  const glyph = type === "error" ? "alert" : type === "info" ? "info" : "check";
  node.innerHTML = `${icon(glyph, 18)}<span>${escapeText(message)}</span>`;
  host.append(node);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    node.classList.add("is-leaving");
    setTimeout(() => node.remove(), 220);
  };

  const timer = setTimeout(close, Math.max(1200, timeout));
  node.addEventListener("click", () => {
    clearTimeout(timer);
    close();
  });

  return close;
}

function escapeText(value) {
  const span = document.createElement("span");
  span.textContent = String(value ?? "");
  return span.innerHTML;
}

export function toastError(err) {
  const message = err?.message || String(err || "Erreur inconnue");
  return toast(message, { type: "error", timeout: 7000 });
}

export function toastInfo(message) {
  return toast(message, { type: "info" });
}
