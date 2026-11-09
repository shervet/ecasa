// Feuille modale mobile / boîte de dialogue desktop, accessible et sans dépendance.

let current = null;

export function openSheet({
  title = "",
  sub = "",
  body = "",
  footer = "",
  onMount = null,
  onClose = null,
  dismissible = true,
} = {}) {
  closeSheet(true);

  const root = document.getElementById("sheetRoot");
  if (!root) return null;

  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";

  const sheet = document.createElement("section");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", title || "Fenêtre");
  sheet.innerHTML = `
    <div class="sheet-grip" aria-hidden="true"></div>
    <header class="sheet-head">
      <div>
        <h2 class="sheet-title"></h2>
        <p class="sheet-sub"></p>
      </div>
      <button class="icon-btn" type="button" data-sheet-close aria-label="Fermer">${closeIcon()}</button>
    </header>
    <div class="sheet-body"></div>
    ${footer ? '<footer class="sheet-foot"></footer>' : ""}
  `;

  sheet.querySelector(".sheet-title").textContent = title;
  const subNode = sheet.querySelector(".sheet-sub");
  if (sub) subNode.textContent = sub;
  else subNode.remove();

  const bodyNode = sheet.querySelector(".sheet-body");
  if (typeof body === "string") bodyNode.innerHTML = body;
  else if (body) bodyNode.append(body);

  const footNode = sheet.querySelector(".sheet-foot");
  if (footNode) {
    if (typeof footer === "string") footNode.innerHTML = footer;
    else footNode.append(footer);
  }

  const previousFocus = document.activeElement;
  const controller = { close: () => closeSheet(false), sheet, body: bodyNode, footer: footNode };

  root.append(backdrop, sheet);
  document.body.style.overflow = "hidden";

  requestAnimationFrame(() => {
    backdrop.classList.add("is-open");
    sheet.classList.add("is-open");
    const focusable = sheet.querySelector(
      "input:not([type=hidden]), select, textarea, button:not([data-sheet-close]), a[href]"
    );
    (focusable || sheet.querySelector("[data-sheet-close]"))?.focus({ preventScroll: true });
  });

  const close = () => closeSheet(false);
  if (dismissible) {
    backdrop.addEventListener("click", close);
    sheet.querySelector("[data-sheet-close]")?.addEventListener("click", close);
  }

  const onKey = (event) => {
    if (event.key === "Escape" && dismissible) {
      event.preventDefault();
      close();
    }
    if (event.key === "Tab") trapFocus(event, sheet);
  };
  document.addEventListener("keydown", onKey);

  current = {
    backdrop,
    sheet,
    previousFocus,
    onKey,
    onClose,
    dismissible,
  };

  if (typeof onMount === "function") onMount(controller);

  return controller;
}

export function closeSheet(immediate = false) {
  if (!current) return;
  const { backdrop, sheet, previousFocus, onKey, onClose } = current;
  current = null;

  document.removeEventListener("keydown", onKey);
  document.body.style.overflow = "";

  const finish = () => {
    backdrop.remove();
    sheet.remove();
    if (previousFocus && typeof previousFocus.focus === "function") {
      previousFocus.focus({ preventScroll: true });
    }
    if (typeof onClose === "function") onClose();
  };

  if (immediate) {
    finish();
    return;
  }

  backdrop.classList.remove("is-open");
  sheet.classList.remove("is-open");
  setTimeout(finish, 260);
}

function trapFocus(event, container) {
  const nodes = [
    ...container.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    ),
  ].filter((node) => node.offsetParent !== null || node === document.activeElement);

  if (!nodes.length) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];

  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function closeIcon() {
  return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
}
