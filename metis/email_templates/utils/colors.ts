export const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif";

export const C_HEADING       = '#1f2118';
export const C_BODY          = '#1f2118';
export const C_MUTED         = '#72716d';
export const C_SUBTLE        = '#aaaaaa';
export const C_SKIPPED_TITLE = '#1f2118';
export const C_LINK          = '#185FA5';
export const C_BTN           = '#888780';

export const C_BG_PRIMARY   = '#ffffff';
export const C_BG_SECONDARY = '#f5f5f3';
export const C_BG_CONSIDER  = '#fafafa';

export const C_BORDER         = '#e5e5e5';
export const C_BORDER_LIGHT   = '#dddddd';
export const C_BORDER_SECTION = '#eeece5';

export const C_STAT_TOTAL_BG  = '#f5f5f3';
export const C_STAT_TOTAL_NUM = '#1f2118';
export const C_STAT_TOTAL_LBL = '#888780';

export const C_STAT_APPLY_BG  = '#eef2ee';
export const C_STAT_APPLY_NUM = '#2d5a2d';
export const C_STAT_APPLY_LBL = '#2d5a2d';

export const C_STAT_CONSIDER_BG  = '#faeeda';
export const C_STAT_CONSIDER_NUM = '#854f0b';
export const C_STAT_CONSIDER_LBL = '#854f0b';

export const SCORE_COLORS = {
  apply:    { background: '#eef2ee', color: '#2d5a2d' },
  consider: { background: '#f4f0e8', color: '#7a5c1e' },
  skipped:  { background: '#f0f0ef', color: '#52514e' },
} as const;

export const TAG_COLORS = {
  green:   { background: '#eef2ee', color: '#2d5a2d' },
  amber:   { background: '#f4f0e8', color: '#7a5c1e' },
  red:     { background: '#f2eeee', color: '#8b2e2e' },
  neutral: { background: '#f0f0ef', color: '#52514e' },
} as const;

export const SECTION_ACCENT = {
  apply:    { bar: '#2d5a2d', label: '#2d5a2d' },
  consider: { bar: '#7a5c1e', label: '#7a5c1e' },
  skipped:  { bar: '#888780', label: '#888780' },
} as const;

export const C_ARROW_UP   = '#2d5a2d';
export const C_ARROW_DOWN = '#7a5c1e';

export const LEGEND_DOTS = [
  { color: '#2d5a2d', label: 'Strengths' },
  { color: '#7a5c1e', label: 'Caution' },
  { color: '#8b2e2e', label: 'Blockers' },
] as const;
