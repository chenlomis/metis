import type { Tag } from '../types';

const PLACEHOLDERS = /^(none|n\/a|none material|no material|na|-)$/i;
export function cleanPoints(points: string[]): string[] {
  return points.filter((p) => p.trim() && !PLACEHOLDERS.test(p.trim()));
}

export function capLeverage(points: string[]): string[] {
  return points.slice(0, 2);
}

export function capFriction(points: string[]): string[] {
  return points.slice(0, 1);
}

export function capTags(tags: Tag[]): Tag[] {
  return tags.slice(0, 4);
}

const ABBREVS: [RegExp, string][] = [
  [/\bplatform\b/gi,   'plat'],
  [/\bdirect fit\b/gi, 'fit'],
  [/\balumni\b/gi,     'alum'],
  [/\bexperience\b/gi, 'exp'],
];

export function shortenTag(text: string): string {
  return ABBREVS.reduce((s, [re, sub]) => s.replace(re, sub), text);
}
