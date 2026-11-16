// Jeu d'icônes SVG minimaliste, sans dépendance. Traits 24×24.

const PATHS = {
  home: '<path d="M3 10.5 12 3l9 7.5V21a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V10.5Z"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/>',
  "calendar-days": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 17h.01M12 17h.01"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  activity: '<path d="M3 12h4l2.5-7 5 14 2.5-7h4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 8.9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="m20 6-11 11-5-5"/>',
  trash: '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/>',
  rotate: '<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/>',
  power: '<path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  play: '<path d="m6 3 15 9-15 9V3Z"/>',
  pause: '<path d="M7 4h3v16H7zM14 4h3v16h-3z"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
  upload: '<path d="M12 21V9M7 14l5-5 5 5M5 3h14"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
  wifi: '<path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 20h.01M2 9a15 15 0 0 1 20 0"/>',
  cloud: '<path d="M17.5 19a4.5 4.5 0 0 0 .4-9A6.5 6.5 0 0 0 5.3 9.6 4 4 0 0 0 6 19h11.5Z"/>',
  thermometer: '<path d="M14 14.76V5a2 2 0 0 0-4 0v9.76a4 4 0 1 0 4 0Z"/><path d="M12 9v6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
  droplet: '<path d="M12 2.7 6.5 8.2a7.5 7.5 0 1 0 11 0L12 2.7Z"/>',
  wind: '<path d="M3 8h11a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h7a2.5 2.5 0 1 1-2.5 2.5"/>',
  snowflake: '<path d="M12 2v20M4.2 6.5l15.6 11M19.8 6.5 4.2 17.5M12 6l-3 3M12 6l3 3M12 18l-3-3M12 18l3-3"/>',
  "cloud-sun": '<path d="M12 2v2M4.9 4.9l1.4 1.4M2 12h2"/><circle cx="11" cy="10" r="3.2"/><path d="M17.5 21a4 4 0 0 0 .4-8 6 6 0 0 0-11.4 1.4A3.5 3.5 0 0 0 7 21h10.5Z"/>',
  rain: '<path d="M17.5 16a4.5 4.5 0 0 0 .4-9A6.5 6.5 0 0 0 5.3 6.6 4 4 0 0 0 6 16h11.5Z"/><path d="M8 19v2M12 19v2M16 19v2"/>',
  drizzle: '<path d="M17.5 16a4.5 4.5 0 0 0 .4-9A6.5 6.5 0 0 0 5.3 6.6 4 4 0 0 0 6 16h11.5Z"/><path d="M8 19h.01M12 19h.01M16 19h.01M10 21.5h.01M14 21.5h.01"/>',
  storm: '<path d="M17.5 15a4.5 4.5 0 0 0 .4-9A6.5 6.5 0 0 0 5.3 5.6 4 4 0 0 0 6 15h11.5Z"/><path d="m13 12-3 5h4l-3 5"/>',
  fog: '<path d="M17.5 13a4.5 4.5 0 0 0 .4-9A6.5 6.5 0 0 0 5.3 3.6 4 4 0 0 0 6 13h11.5Z"/><path d="M5 17h14M7 20h10"/>',
  flame: '<path d="M12 22c4 0 7-2.7 7-6.5 0-3-2-5.4-3.7-7.2C13.9 6.8 13 4.8 13 2c-2.4 1.6-4 3.9-4 6 0 1.4.4 2.3 1 3-1.7-.2-3-1.5-3-3.2C5.5 9.5 5 11.6 5 14c0 4.4 3 8 7 8Z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/><path d="M12 7v5l3 2"/>',
  "chevron-left": '<path d="m15 18-6-6 6-6"/>',
  "chevron-right": '<path d="m9 18 6-6-6-6"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  "arrow-left": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
  "arrow-right": '<path d="M5 12h14M12 5l7 7-7 7"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
  shield: '<path d="M12 22s8-3.5 8-10V5l-8-3-8 3v7c0 6.5 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01"/>',
  gauge: '<path d="M12 14 17 9"/><path d="M3.5 18a9 9 0 1 1 17 0"/><circle cx="12" cy="14" r="1.5"/>',
  sparkles: '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/>',
  school: '<path d="M3 21h18M5 21V9l7-5 7 5v12M9 21v-5h6v5"/><path d="M9 11h.01M15 11h.01"/>',
  hub: '<circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="m6.5 6.5 3 3M17.5 6.5l-3 3M6.5 17.5l3-3M17.5 17.5l-3-3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  install: '<path d="M12 3v11M8 10l4 4 4-4M5 21h14"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  zap: '<path d="M13 2 3 14h8l-1 8 10-12h-8l1-8Z"/>',
  leaf: '<path d="M11 20A7 7 0 0 1 4 13c0-5 4-9 16-9 0 12-4 16-9 16Z"/><path d="M4 21c2-5 6-9 12-12"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.5 12.5 8-8M16 3l4 4M14 5l3 3"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  "eye-off": '<path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A10.5 10.5 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3 3.9M6.2 6.2C3.5 8 2 12 2 12s3.5 7 10 7c1.7 0 3.2-.4 4.5-1"/>',
  laptop: '<rect x="4" y="4" width="16" height="12" rx="2"/><path d="M2 20h20"/>',
  smartphone: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
  "trending-up": '<path d="m3 17 6-6 4 4 8-8M15 7h6v6"/>',
  "trending-down": '<path d="m3 7 6 6 4-4 8 8M15 17h6v-6"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>',
  house: '<path d="M3 10.5 12 3l9 7.5V21a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V10.5Z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.07 0l1.93-1.93a5 5 0 0 0-7.07-7.07l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.07 0L5 12.93a5 5 0 0 0 7.07 7.07l1.1-1.1"/>',
  unlink: '<path d="m18.84 12.25 1.72-1.71a5 5 0 0 0-7.07-7.07l-1.7 1.7"/><path d="m5.17 11.75-1.72 1.71a5 5 0 0 0 7.07 7.07l1.7-1.7"/><path d="m8 2 5 5"/><path d="m16 22-5-5"/>',
};

export function icon(name, size = 20, extraClass = "") {
  const body = PATHS[name] || PATHS.info;
  return `<svg class="${extraClass}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

/** Code météo WMO (Open-Meteo) → nom d'icône. */
export function weatherIconName(code) {
  if (code === null || code === undefined) return "cloud";
  if (code === 0) return "sun";
  if (code === 1 || code === 2) return "cloud-sun";
  if (code === 3) return "cloud";
  if (code === 45 || code === 48) return "fog";
  if ([51, 53, 55, 56, 57].includes(code)) return "drizzle";
  if ([61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return "rain";
  if ([71, 73, 75, 77, 85, 86].includes(code)) return "snowflake";
  if ([95, 96, 99].includes(code)) return "storm";
  return "cloud";
}

export const MODE_ICONS = {
  off: "power",
  auto: "sparkles",
  cool: "snowflake",
  heat: "flame",
  dry: "droplet",
  fan: "wind",
};

export function modeIcon(mode, size = 20) {
  return icon(MODE_ICONS[mode] || "thermometer", size);
}
