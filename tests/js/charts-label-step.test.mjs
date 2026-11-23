import assert from "node:assert/strict";
import { computeLabelStep } from "../../web/js/charts.js";

const drawn = (count, step) => Math.ceil(count / step);

const TOUCH_FONTS = { primaryFont: 30.5, secondaryFont: 27.6 };
const DESKTOP_FONTS = { primaryFont: 10.5, secondaryFont: 9.5 };

// 1. Tactile, 12 jours : avant, step=1 dessinait les 12 labels chevauchants.
const touchDay = computeLabelStep({
  count: 12,
  labels: Array.from({ length: 12 }, (_, i) => `${String(i + 13).padStart(2, "0")}/11 || Lun`),
  innerW: 670,
  maxLabels: 12,
  ...TOUCH_FONTS,
});
assert.equal(touchDay, 3);
assert.ok(drawn(12, touchDay) >= 3);
console.log(`tactile 12 jours : step=${touchDay}, labels affiches=${drawn(12, touchDay)}`);

// 2. Bureau, 12 jours : les 12 labels tiennent, step=1 conserve.
const desktopDay = computeLabelStep({
  count: 12,
  labels: Array.from({ length: 12 }, (_, i) => `${String(i + 13).padStart(2, "0")}/11 || Lun`),
  innerW: 704,
  maxLabels: 12,
  ...DESKTOP_FONTS,
});
assert.equal(desktopDay, 1);
assert.equal(drawn(12, desktopDay), 12);
console.log(`bureau 12 jours : step=${desktopDay}, labels affiches=${drawn(12, desktopDay)}`);

// 3. Tactile, 12 semaines : les etiquettes courtes restent filtrees par la largeur.
const touchWeek = computeLabelStep({
  count: 12,
  labels: Array.from({ length: 12 }, (_, i) => `S${40 + i}`),
  innerW: 670,
  maxLabels: 12,
  ...TOUCH_FONTS,
});
assert.equal(touchWeek, 3);
assert.ok(drawn(12, touchWeek) >= 3);
console.log(`tactile 12 semaines : step=${touchWeek}, labels affiches=${drawn(12, touchWeek)}`);

// 4. Plancher absolu : jamais moins de 3 labels, meme avec une largeur ridicule.
const floor = computeLabelStep({
  count: 60,
  labels: ["TresLongLabelTresLongLabel"],
  innerW: 10,
  maxLabels: 12,
  ...TOUCH_FONTS,
});
assert.equal(floor, 20);
assert.equal(drawn(60, floor), 3);
console.log(`plancher 3 labels : step=${floor}, labels affiches=${drawn(60, floor)}`);

console.log("4 cas OK");
