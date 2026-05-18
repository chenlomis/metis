import type { Tier, TagSentiment } from '../types';
import { SCORE_COLORS, TAG_COLORS } from './colors';

export const APPLY_THRESHOLD    = 75;
export const CONSIDER_THRESHOLD = 60;

export function getTierFromScore(score: number): Tier {
  if (score >= APPLY_THRESHOLD)    return 'apply';
  if (score >= CONSIDER_THRESHOLD) return 'consider';
  return 'skipped';
}

export function getScoreColors(tier: Tier): { background: string; color: string } {
  return SCORE_COLORS[tier];
}

export function getTagColors(sentiment: TagSentiment): { background: string; color: string } {
  return TAG_COLORS[sentiment];
}
