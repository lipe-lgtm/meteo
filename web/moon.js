// Fáze Měsíce – počítá se v prohlížeči, bez internetu.
// Zjednodušené polohy Slunce a Měsíce (Meeus, hlavní členy): délka Měsíce ±0,3°,
// časy fází tedy zhruba ±40 minut, osvětlení ±1 %.

const RAD = Math.PI / 180;
const SYNODIC = 29.530588853;
const norm = (deg) => ((deg % 360) + 360) % 360;

function daysJ2000(ms) {
  return ms / 86400000 + 2440587.5 - 2451545.0;
}

/** Elongace Měsíce od Slunce ve stupních (0 = nov, 90 = první čtvrť, 180 = úplněk). */
export function elongation(ms) {
  const d = daysJ2000(ms);
  const Lm = 218.316 + 13.176396 * d;           // střední délka Měsíce
  const Mm = (134.963 + 13.064993 * d) * RAD;   // střední anomálie Měsíce
  const Ms = (357.529 + 0.98560028 * d) * RAD;  // střední anomálie Slunce
  const D = (297.850 + 12.190749 * d) * RAD;    // střední elongace
  const F = (93.272 + 13.229350 * d) * RAD;     // argument šířky
  const moon = Lm + 6.289 * Math.sin(Mm) + 1.274 * Math.sin(2 * D - Mm) + 0.658 * Math.sin(2 * D)
    + 0.214 * Math.sin(2 * Mm) - 0.186 * Math.sin(Ms) - 0.114 * Math.sin(2 * F)
    - 0.059 * Math.sin(2 * D - 2 * Mm) - 0.057 * Math.sin(2 * D - Ms - Mm) + 0.053 * Math.sin(2 * D + Mm)
    + 0.046 * Math.sin(2 * D - Ms) + 0.041 * Math.sin(Ms - Mm) - 0.035 * Math.sin(D)
    - 0.031 * Math.sin(Ms + Mm);
  const sun = 280.460 + 0.9856474 * d + 1.915 * Math.sin(Ms) + 0.020 * Math.sin(2 * Ms);
  return norm(moon - sun);
}

const NAMES = [
  [12, 'Nov'], [78, 'Dorůstající srpek'], [102, 'První čtvrť'], [168, 'Dorůstající měsíc'],
  [192, 'Úplněk'], [258, 'Ubývající měsíc'], [282, 'Poslední čtvrť'], [348, 'Ubývající srpek'], [361, 'Nov'],
];
const EVENTS = [[0, 'Nov'], [90, 'První čtvrť'], [180, 'Úplněk'], [270, 'Poslední čtvrť']];

/** Kdy elongace příště dosáhne target (hledá dopředu po 6 h, pak půlením na minutu). */
function nextCrossing(fromMs, target) {
  const off = (ms) => norm(elongation(ms) - target);   // roste 0 → 360, v cíli skočí na 0
  const step = 6 * 3600000;
  let a = fromMs;
  let prev = off(a);
  for (let i = 0; i < 130; i += 1) {
    const b = a + step;
    const cur = off(b);
    if (cur < prev) {                                    // přeskok přes cíl
      let lo = a;
      let hi = b;
      while (hi - lo > 60000) {
        const mid = (lo + hi) / 2;
        if (off(mid) < off(lo)) hi = mid; else lo = mid;
      }
      return hi;
    }
    a = b;
    prev = cur;
  }
  return null;
}

/** Stav Měsíce k danému času. */
export function moonInfo(ms = Date.now()) {
  const e = elongation(ms);
  const lit = (1 - Math.cos(e * RAD)) / 2;
  const name = NAMES.find(([limit]) => e < limit)[1];
  const upcoming = EVENTS.map(([deg, label]) => ({ label, t: nextCrossing(ms, deg) }))
    .filter((x) => x.t)
    .sort((x, y) => x.t - y.t);
  return {
    elongation: e,
    lit,                                // 0–1
    waxing: e < 180,
    age: (e / 360) * SYNODIC,           // dny od novu (přibližně)
    name,
    upcoming,                           // další nov, čtvrti, úplněk – seřazené
  };
}
