import * as React from 'react';
import { Section } from '@react-email/components';
import JobCard from './JobCard';
import { FONT, C_MUTED, C_BORDER_SECTION, SECTION_ACCENT } from '../../utils/colors';
import type { Job, Tier } from '../../types';

interface Props {
  tier: Tier;
  jobs: Job[];
}

function scoreRange(jobs: Job[]): string {
  if (!jobs.length) return '';
  const scores = jobs.map((j) => j.score);
  const lo = Math.min(...scores);
  const hi = Math.max(...scores);
  const n  = jobs.length;
  return `${lo}–${hi}% match · ${n} role${n !== 1 ? 's' : ''}`;
}

export default function TierSection({ tier, jobs }: Props) {
  if (!jobs.length) return null;
  const { bar, label } = SECTION_ACCENT[tier];
  const TIER_LABELS: Record<Tier, string> = { apply: 'Solid Match', consider: 'Moderate Match', skipped: 'Limited Match' };
  const tierLabel = TIER_LABELS[tier] ?? (tier.charAt(0).toUpperCase() + tier.slice(1));

  return (
    <Section style={{ marginBottom: '14px' }}>
      {/* Section header */}
      <table cellPadding={0} cellSpacing={0} border={0} style={{ width: '100%', borderCollapse: 'collapse', borderBottom: `1px solid ${C_BORDER_SECTION}`, marginBottom: '8px' }}>
        <tbody>
          <tr>
            <td style={{ width: '3px', background: bar, borderRadius: '2px', fontSize: '0', lineHeight: '0', paddingTop: '8px', paddingBottom: '8px' }}>&nbsp;</td>
            <td style={{ width: '8px' }}>&nbsp;</td>
            <td style={{ fontSize: '14px', fontWeight: 600, color: label, fontFamily: FONT, paddingTop: '8px', paddingBottom: '8px' }}>
              {tierLabel}
            </td>
            <td style={{ fontSize: '12px', color: C_MUTED, textAlign: 'right', fontFamily: FONT, paddingTop: '8px', paddingBottom: '8px' }}>
              {scoreRange(jobs)}
            </td>
          </tr>
        </tbody>
      </table>

      {/* Job cards with 8px spacers */}
      {jobs.map((job, i) => (
        <React.Fragment key={job.postingUrl || i}>
          <JobCard job={job} tier={tier} />
          {i < jobs.length - 1 && (
            <table cellPadding={0} cellSpacing={0} border={0} style={{ width: '100%' }}>
              <tbody>
                <tr>
                  <td height={8} style={{ fontSize: '0', lineHeight: '0' }}>&nbsp;</td>
                </tr>
              </tbody>
            </table>
          )}
        </React.Fragment>
      ))}
    </Section>
  );
}
