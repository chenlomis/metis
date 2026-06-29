import * as React from 'react';
import { Section, Text } from '@react-email/components';
import {
  FONT, C_MUTED, C_HEADING, C_BG_PRIMARY, C_BORDER, C_BORDER_LIGHT,
  C_STAT_TOTAL_BG, C_STAT_TOTAL_NUM, C_STAT_TOTAL_LBL,
  C_STAT_APPLY_BG, C_STAT_APPLY_NUM, C_STAT_APPLY_LBL,
  C_STAT_CONSIDER_BG, C_STAT_CONSIDER_NUM, C_STAT_CONSIDER_LBL,
  LEGEND_DOTS,
} from '../../utils/colors';

interface Props {
  date: string;
  totalEvaluated: number;
  applyCount: number;
  considerCount: number;
  candidateName: string;
  greeting: string;
  greetingSub?: string;
}

function StatTile({
  number, label, bg, numColor, lblColor,
}: {
  number: number; label: string; bg: string; numColor: string; lblColor: string;
}) {
  return (
    <td
      width="33%"
      style={{ background: bg, padding: '12px 14px', borderRadius: '6px', verticalAlign: 'top' }}
    >
      <p style={{ fontSize: '32px', fontWeight: 600, color: numColor, margin: '0 0 3px 0', fontFamily: FONT, lineHeight: '1' }}>
        {number}
      </p>
      <p style={{ fontSize: '10px', color: lblColor, textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0, fontFamily: FONT }}>
        {label}
      </p>
    </td>
  );
}

export default function DigestHeader({ date, totalEvaluated, applyCount, considerCount, greeting, greetingSub }: Props) {
  return (
    <Section style={{ background: C_BG_PRIMARY, border: `1px solid ${C_BORDER}`, borderRadius: '8px', marginBottom: '12px' }}>

      {/* Wordmark bar */}
      <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ borderBottom: `1px solid ${C_BORDER_LIGHT}`, borderCollapse: 'collapse' }}>
        <tbody>
          <tr>
            <td style={{ padding: '12px 20px', verticalAlign: 'middle' }}>
              <table cellPadding={0} cellSpacing={0} border={0} style={{ borderCollapse: 'collapse' }}>
                <tbody>
                  <tr>
                    <td
                      width={8}
                      height={8}
                      style={{ background: C_HEADING, borderRadius: '2px', fontSize: '0', lineHeight: '0', verticalAlign: 'middle' }}
                    >&nbsp;</td>
                    <td style={{ paddingLeft: '7px', fontSize: '12px', fontWeight: 500, color: C_HEADING, fontFamily: FONT, letterSpacing: '-0.01em', verticalAlign: 'middle' }}>
                      Metis
                    </td>
                  </tr>
                </tbody>
              </table>
            </td>
            <td style={{ padding: '12px 20px', textAlign: 'right', fontSize: '11px', color: C_MUTED, fontFamily: FONT, whiteSpace: 'nowrap', verticalAlign: 'middle' }}>
              {date}
            </td>
          </tr>
        </tbody>
      </table>

      {/* Greeting — salutation on line 1, evaluation summary on line 2 */}
      {greeting ? (
        <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ borderCollapse: 'collapse' }}>
          <tbody>
            <tr>
              <td style={{ padding: '14px 20px 0' }}>
                <Text style={{ fontSize: '18px', fontWeight: 600, color: C_HEADING, margin: '0 0 4px 0', fontFamily: FONT, lineHeight: '1.3' }}>
                  {greeting}
                </Text>
                {greetingSub ? (
                  <Text style={{ fontSize: '13px', color: C_MUTED, margin: '0', fontFamily: FONT, lineHeight: '1.5' }}>
                    {greetingSub}
                  </Text>
                ) : null}
              </td>
            </tr>
          </tbody>
        </table>
      ) : null}

      {/* Stat tiles */}
      <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ borderCollapse: 'collapse' }}>
        <tbody>
          <tr>
            <td style={{ padding: '14px 20px 16px' }}>
              <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ borderCollapse: 'collapse' }}>
                <tbody>
                  <tr>
                    <StatTile
                      number={totalEvaluated}
                      label="Evaluated"
                      bg={C_STAT_TOTAL_BG}
                      numColor={C_STAT_TOTAL_NUM}
                      lblColor={C_STAT_TOTAL_LBL}
                    />
                    <td width={8}>&nbsp;</td>
                    <StatTile
                      number={applyCount}
                      label="Solid Match"
                      bg={C_STAT_APPLY_BG}
                      numColor={C_STAT_APPLY_NUM}
                      lblColor={C_STAT_APPLY_LBL}
                    />
                    <td width={8}>&nbsp;</td>
                    <StatTile
                      number={considerCount}
                      label="Moderate Match"
                      bg={C_STAT_CONSIDER_BG}
                      numColor={C_STAT_CONSIDER_NUM}
                      lblColor={C_STAT_CONSIDER_LBL}
                    />
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>
        </tbody>
      </table>

      {/* Legend */}
      <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ borderTop: `1px solid ${C_BORDER_LIGHT}`, borderCollapse: 'collapse' }}>
        <tbody>
          <tr>
            <td style={{ padding: '10px 20px' }}>
              {LEGEND_DOTS.map((item) => (
                <span
                  key={item.label}
                  style={{ display: 'inline-block', marginRight: '14px', verticalAlign: 'middle', whiteSpace: 'nowrap' }}
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
