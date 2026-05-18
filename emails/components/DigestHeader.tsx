import * as React from 'react';
import { Section, Text } from '@react-email/components';
import {
  FONT, C_MUTED, C_BODY, C_BG_PRIMARY, C_BG_SECONDARY, C_BORDER,
  SCORE_COLORS, LEGEND_DOTS, C_HEADING,
} from '../../utils/colors';

interface Props {
  date: string;
  totalEvaluated: number;
  applyCount: number;
  considerCount: number;
}

function StatCell({ number, label, color }: { number: number; label: string; color: string }) {
  return (
    <td
      width="33%"
      style={{ background: C_BG_SECONDARY, padding: '12px', borderRadius: '4px', verticalAlign: 'top' }}
    >
      <p style={{ fontSize: '22px', fontWeight: 500, color, margin: '0 0 2px 0', fontFamily: FONT, lineHeight: '1' }}>
        {number}
      </p>
      <p style={{ fontSize: '11px', color: C_MUTED, textTransform: 'uppercase', letterSpacing: '0.04em', margin: 0, fontFamily: FONT }}>
        {label}
      </p>
    </td>
  );
}

export default function DigestHeader({ date, totalEvaluated, applyCount, considerCount }: Props) {
  return (
    <Section
      style={{ background: C_BG_PRIMARY, border: `1px solid ${C_BORDER}`, borderRadius: '8px', padding: '16px 20px', marginBottom: '12px' }}
    >
      <Text style={{ fontSize: '18px', fontWeight: 500, color: C_HEADING, margin: '0 0 2px 0', fontFamily: FONT }}>
        Personalized Job Alert Digest
      </Text>
      <Text style={{ fontSize: '13px', color: C_MUTED, margin: '0 0 14px 0', fontFamily: FONT }}>
        {date}
      </Text>

      {/* Stat row — three equal-width columns */}
      <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ marginBottom: '14px', borderCollapse: 'collapse' }}>
        <tbody>
          <tr>
            <StatCell number={totalEvaluated} label="Roles evaluated" color={C_BODY} />
            <td width="6">&nbsp;</td>
            <StatCell number={applyCount} label="Apply now" color={SCORE_COLORS.apply.color} />
            <td width="6">&nbsp;</td>
            <StatCell number={considerCount} label="Consider" color={SCORE_COLORS.consider.color} />
          </tr>
        </tbody>
      </table>

      {/* Legend — inline spans, single row */}
      <table width="100%" cellPadding={0} cellSpacing={0} border={0}>
        <tbody>
          <tr>
            <td>
              {LEGEND_DOTS.map((item) => (
                <span
                  key={item.label}
                  style={{ display: 'inline-block', marginRight: '12px', verticalAlign: 'middle', whiteSpace: 'nowrap' }}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: '7px',
                      height: '7px',
                      background: item.color,
                      borderRadius: '50%',
                      marginRight: '4px',
                      verticalAlign: 'middle',
                    }}
                  />
                  <span style={{ fontSize: '11px', color: C_MUTED, fontFamily: FONT, verticalAlign: 'middle' }}>
                    {item.label}
                  </span>
                </span>
              ))}
            </td>
          </tr>
        </tbody>
      </table>
    </Section>
  );
}
