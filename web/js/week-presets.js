// Modèles de semaine prédéfinis, partagés par la page Planning et l'éditeur
// de modèles. Un modèle = 7 jours de créneaux { time, mode, temp }.
//
// Les presets décrivent une présence : on chauffe avant l'arrivée, on coupe
// pendant l'absence, on rechauffe avant le retour.

function slots(...items) {
  return items.map(([time, mode, temp]) => ({ time, mode, temp }));
}

/** Duplique les créneaux jours de semaine / samedi / dimanche sur 7 jours. */
function week(weekday, saturday, sunday) {
  return Array.from({ length: 7 }, (_, index) => {
    const source = index < 5 ? weekday : index === 5 ? saturday : sunday;
    return source.map((slot) => ({ ...slot }));
  });
}

const WEEKDAY_OFF = slots(["08:00", "off", 16], ["18:00", "heat", 19]);
const WEEKEND_HOME = slots(["08:00", "heat", 20], ["23:00", "off", 17]);

export const WEEK_PRESETS = {
  // Présence toute la journée (vacances) : confort le matin et l'après-midi.
  vacances: week(
    slots(["07:00", "heat", 19], ["09:00", "off", 17], ["16:00", "heat", 20], ["23:00", "off", 17]),
    WEEKEND_HOME,
    WEEKEND_HOME
  ),
  // Semaine de travail « classique » (départ 9h, retour 17h).
  travail: week(
    slots(["06:30", "heat", 20], ["09:00", "off", 17], ["17:00", "heat", 20], ["23:00", "off", 17]),
    WEEKEND_HOME,
    WEEKEND_HOME
  ),
  // Absence 9h → 18h.
  travail_9_18: week(
    slots(["08:00", "heat", 20], ["09:00", "off", 17], ["18:00", "heat", 20], ["23:00", "off", 17]),
    WEEKEND_HOME,
    WEEKEND_HOME
  ),
  // Absence 8h → 20h (journée longue).
  travail_8_20: week(
    slots(["07:00", "heat", 20], ["08:00", "off", 17], ["20:00", "heat", 20], ["23:00", "off", 17]),
    WEEKEND_HOME,
    WEEKEND_HOME
  ),
  // Absence 7h → 19h (tôt le matin).
  travail_7_19: week(
    slots(["06:00", "heat", 20], ["07:00", "off", 17], ["19:00", "heat", 20], ["23:00", "off", 17]),
    WEEKEND_HOME,
    WEEKEND_HOME
  ),
  // Absence 9h → 17h.
  travail_9_17: week(
    slots(["08:00", "heat", 20], ["09:00", "off", 17], ["17:00", "heat", 20], ["23:00", "off", 17]),
    WEEKEND_HOME,
    WEEKEND_HOME
  ),
  // Week-end prolongé : maison seulement samedi/dimanche.
  weekend: week(
    slots(["08:00", "off", 16]),
    WEEKEND_HOME,
    WEEKEND_HOME
  ),
  // Absence complète : hors gel, court confort en fin de journée.
  absence: Array.from({ length: 7 }, () =>
    WEEKDAY_OFF.map((slot) => ({ ...slot }))
  ),
};

/** Libellés affichés dans les menus, dans l'ordre voulu. */
export const WEEK_PRESET_OPTIONS = [
  ["vacances", "Vacances"],
  ["travail", "Semaine de travail"],
  ["travail_9_18", "Travail 9h – 18h"],
  ["travail_8_20", "Travail 8h – 20h"],
  ["travail_7_19", "Travail 7h – 19h"],
  ["travail_9_17", "Travail 9h – 17h"],
  ["weekend", "Week-end prolongé"],
  ["absence", "Absence"],
];

export function weekPresetLabel(key) {
  const found = WEEK_PRESET_OPTIONS.find(([id]) => id === key);
  return found ? found[1] : String(key || "");
}

/** Copie profonde d'une semaine de créneaux (7 jours). */
export function cloneWeekDays(days) {
  return Array.from({ length: 7 }, (_, index) => {
    const list = Array.isArray(days?.[index]) ? days[index] : [];
    return list.map((slot) => ({ ...slot }));
  });
}
