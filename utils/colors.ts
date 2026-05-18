export const FONT = "-apple-system, 'Helvetica Neue', Arial, sans-serif";

// Text
export const C_HEADING       = '#222222';
export const C_BODY          = '#5F5E5A';
export const C_MUTED         = '#888780';
export const C_SUBTLE        = '#aaaaaa';
export const C_SKIPPED_TITLE = '#2C2C2A';
export const C_LINK          = '#185FA5';

// Backgrounds
export const C_BG_PRIMARY   = '#ffffff';
export const C_BG_SECONDARY = '#f5f5f3';
export const C_BG_CONSIDER  = '#fafafa';

// Borders
export const C_BORDER         = '#e5e5e5';
export const C_BORDER_LIGHT   = '#dddddd';
export const C_BORDER_SECTION = '#eeece5';

// Score tier pills
export const SCORE_COLORS = {
  apply:    { background: '#EAF3DE', color: '#3B6D11' },
  consider: { background: '#FAEEDA', color: '#854F0B' },
  skipped:  { background: '#F1EFE8', color: '#5F5E5A' },
} as const;

// Tag sentiment pills
export const TAG_COLORS = {
  green: { background: '#EAF3DE', color: '#3B6D11' },
  amber: { background: '#FAEEDA', color: '#854F0B' },
  red:   { background: '#FCEBEB', color: '#A32D2D' },
} as const;

// Section accent bars + label colors
export const SECTION_ACCENT = {
  apply:    { bar: '#639922', label: '#3B6D11' },
  consider: { bar: '#BA7517', label: '#854F0B' },
  skipped:  { bar: '#888780', label: '#888780' },
} as const;

// Leverage / friction arrow colors
export const C_ARROW_UP   = '#3B6D11';
export const C_ARROW_DOWN = '#854F0B';

// Legend dot definitions (order is display order)
export const LEGEND_DOTS = [
  { color: '#639922', label: 'Strength match' },
  { color: '#BA7517', label: 'Caution / domain gap' },
  { color: '#A32D2D', label: 'Hard blocker' },
] as const;
