import * as React from 'react';
import { Section } from '@react-email/components';
import {
  FONT, C_MUTED, C_BODY, C_SUBTLE, C_BORDER, C_BORDER_LIGHT,
  C_BG_SECONDARY, C_BORDER_SECTION, C_SKIPPED_TITLE, SECTION_ACCENT,
} from '../../utils/colors';
import type { Job } from '../../types';

function firstSentence(text: string): string {
  const dot = text.indexOf('.');
  return dot === -1 ? text : text.slice(0, dot + 1);
}

export default function SkippedGrid({ jobs }: { jobs: Job[] }) {
  const { bar, label } = SECTION_ACCENT.skipped;

  return (
    <Section style={{ marginBottom: '14px' }}>
      {/* Section header — accent bar */}
      <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ borderCollapse: 'collapse', borderBottom: `1px solid ${C_BORDER_SECTION}`, marginBottom: '8px' }}>
        <tbody>
          <tr>
            <td style={{ width: '3px', background: bar, borderRadius: '2px', fontSize: '0', lineHeight: '0', paddingTop: '8px', paddingBottom: '8px' }}>&nbsp;</td>
            <td style={{ width: '8px' }}>&nbsp;</td>
            <td style={{ fontSize: '13px', fontWeight: 500, color: label, fontFamily: FONT, paddingTop: '8px', paddingBottom: '8px' }}>Skipped</td>
            <td style={{ fontSize: '12px', color: C_MUTED, textAlign: 'right', fontFamily: FONT, paddingTop: '8px', paddingBottom: '8px' }}>
              {jobs.length} roles · domain or title mismatch
            </td>
          </tr>
        </tbody>
      </table>

      {/* Two-column table list.
          tableLayout:fixed + explicit width on every <td> prevents email clients
          (Outlook, Gmail) from breaking a row across two visual lines when the
          reason text is long — fixes the Gemini-reported layout fracture. */}
      <table width="100%" cellPadding={0} cellSpacing={0} border={0} style={{ borderCollapse: 'collapse', tableLayout: 'fixed' as const, background: C_BG_SECONDARY, borderRadius: '4px' }}>
        <colgroup>
          <col style={{ width: '50%' }} />
          <col style={{ width: '50%' }} />
        </colgroup>
        <tbody>
          {/* Header row */}
          <tr>
            <td
              style={{ width: '50%', padding: '8px 14px', borderBottom: `1px solid ${C_BORDER_LIGHT}`, fontSize: '10px', fontWeight: 500, textTransform: 'uppercase' as const, letterSpacing: '0.05em', color: C_SUBTLE, fontFamily: FONT }}
            >
              Role · Company
            </td>
            <td
              style={{ width: '50%', padding: '8px 14px', borderBottom: `1px solid ${C_BORDER_LIGHT}`, fontSize: '10px', fontWeight: 500, textTransform: 'uppercase' as const, letterSpacing: '0.05em', color: C_SUBTLE, fontFamily: FONT }}
            >
              Why skipped
            </td>
          </tr>

          {/* Data rows — each row is a single <tr> so company + reason stay bound */}
          {jobs.map((job, i) => {
            const isLast  = i === jobs.length - 1;
            const border  = isLast ? undefined : `1px solid ${C_BORDER}`;
            const reason  = firstSentence(job.frictionPoints[0] ?? '');
            return (
              <tr key={i}>
                <td style={{ width: '50%', padding: '8px 14px', borderBottom: border, verticalAlign: 'top' }}>
                  <p style={{ fontSize: '12px', fontWeight: 'bold' as const, color: C_SKIPPED_TITLE, margin: '0 0 2px 0', fontFamily: FONT }}>
                    {job.title}
                  </p>
                  <p style={{ fontSize: '11px', color: C_MUTED, margin: 0, fontFamily: FONT }}>
                    {job.company} · {job.location}
                  </p>
                </td>
                <td style={{ width: '50%', padding: '8px 14px', borderBottom: border, fontSize: '11px', color: C_BODY, lineHeight: '1.6', verticalAlign: 'top', fontFamily: FONT }}>
                  {reason}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Section>
  );
}
