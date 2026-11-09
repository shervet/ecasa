// Client HTTP minimal avec messages d'erreur exploitables.

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export async function api(method, path, body, { timeout = 25000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  const options = {
    method,
    headers: { Accept: "application/json" },
    signal: controller.signal,
  };

  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    clearTimeout(timer);
    if (err.name === "AbortError") {
      throw new ApiError("Le serveur met trop de temps à répondre.", 0);
    }
    throw new ApiError("Connexion au serveur impossible. Vérifie le réseau.", 0);
  }
  clearTimeout(timer);

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const message =
      (payload && typeof payload === "object" && (payload.error || payload.message)) ||
      `Erreur ${response.status}`;
    throw new ApiError(String(message), response.status, payload);
  }

  return payload;
}

export const get = (path, options) => api("GET", path, undefined, options);
export const post = (path, body, options) => api("POST", path, body ?? {}, options);
export const put = (path, body, options) => api("PUT", path, body ?? {}, options);
export const del = (path, options) => api("DELETE", path, undefined, options);

export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, value);
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
