import * as React from 'react';
import { FONT, C_BORDER_LIGHT } from '../../utils/colors';

export interface Dimension {
  name: string;
  score: number;    // 0–100
  weight: number;   // 0.0–1.0
  rationale: string;
}

interface Props {
  dimensions: Dimension[];
}

// Colors matching the up/down arrow system used in PointRow
const C_STRONG = '#2d5a2d';  // green
const C_GAP    = '#854F0B';  // amber

function formatName(raw: string): string {
  return raw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function ScoreBreakdown({ dimensions }: Props) {
  if (!dimensions || dimensions.length === 0) return null;

  // Sort by weighted contribution; split into strengths vs gaps
  const sorted = [...dimensions].sort((a, b) => (b.score * b.weight) - (a.score * a.weight));
  const strong = sorted.filter((d) => d.score >= 68);
  const gaps   = sorted.filter((d) => d.score < 68).sort((a, b) => (a.score * a.weight) - (b.score * b.weight));

  const renderGroup = (dims: Dimension[], arrow: string, color: string) => {
    if (dims.length === 0) return null;
    const items = dims.map((d, i) => (
      `${i > 0 ? ' · ' : ''}${formatName(d.name)} ${d.score}`
    )).join('');
    return (
      <p style={{ fontSize: '11px', margin: '0 0 2px 0', fontFamily: FONT, lineHeight: '1.5', color }}>
        <span style={{ fontWeight: 600, marginRight: '5px' }}>{arrow}</span>
        {dims.map((d, i) => (
          <span key={i} style={{ color }}>
            {i > 0 && <span style={{ color: '#ccc', margin: '0 5px' }}>·</span>}
            <span style={{ fontWeight: 500 }}>{formatName(d.name)}</span>
            <span style={{ opacity: 0.75, marginLeft: '3px', fontWeight: 400 }}>{d.score}</span>
          </span>
        ))}
      </p>
    );
  };

  return (
    <table width="100%" cellPadding={0} cellSpacing={0} border={0}
      style={{ borderCollapse: 'collapse', borderTop: `1px solid ${C_BORDER_LIGHT}`, margin: '4px 0 8px 0' }}
    >
      <tbody><tr><td style={{ paddingTop: '6px' }}>
        {renderGroup(strong, '↑', C_STRONG)}
        {renderGroup(gaps,   '↓', C_GAP)}
      </td></tr></tbody>
    </table>
  );
}
