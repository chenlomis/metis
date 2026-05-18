import * as React from 'react';
import { Section } from '@react-email/components';
import Tag from './Tag';
import PointRow from './PointRow';
import CardFooter from './CardFooter';
import { FONT, C_MUTED, C_BORDER, C_BG_PRIMARY, C_BG_CONSIDER, C_HEADING, SCORE_COLORS } from '../../utils/colors';
import { capLeverage, capFriction, capTags, shortenTag, cleanPoints } from '../../utils/format';
import type { Job, Tier } from '../../types';

interface Props {
  job: Job;
  tier: Tier;
}

export default function JobCard({ job, tier }: Props) {
  const pillColors = SCORE_COLORS[tier];
  const cardBg     = tier === 'consider' ? C_BG_CONSIDER : C_BG_PRIMARY;
  const leverage   = cleanPoints(capLeverage(job.leveragePoints));
  const friction   = cleanPoints(capFriction(job.frictionPoints));
  const tags       = capTags(job.tags);

  return (
    <Section
      style={{ background: cardBg, border: `1px solid ${C_BORDER}`, borderRadius: '8px', padding: '14px 16px', marginBottom: '0' }}
    >
      {/* Title + score pill */}
      <table cellPadding={0} cellSpacing={0} border={0} style={{ width: '100%', marginBottom: '3px', borderCollapse: 'collapse' }}>
        <tbody>
          <tr>
            <td style={{ fontSize: '15px', fontWeight: 500, color: C_HEADING, fontFamily: FONT, verticalAlign: 'top' }}>
              {job.title}
            </td>
            <td style={{ width: '1px', whiteSpace: 'nowrap', paddingLeft: '8px', verticalAlign: 'top' }}>
              <span style={{ background: pillColors.background, color: pillColors.color, fontSize: '12px', fontWeight: 500, padding: '3px 10px', borderRadius: '20px', fontFamily: FONT, whiteSpace: 'nowrap', display: 'inline-block' }}>
                {job.score}%
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      {/* Company · location */}
      <p style={{ fontSize: '13px', color: C_MUTED, margin: '0 0 10px 0', fontFamily: FONT }}>
        {job.company} · {job.location}
      </p>

      {/* Leverage (max 2) + friction (max 1) */}
      <div style={{ marginBottom: '10px' }}>
        {leverage.map((pt, i) => <PointRow key={`l${i}`} direction="up" text={pt} />)}
        {friction.map((pt, i) => <PointRow key={`f${i}`} direction="down" text={pt} />)}
      </div>

      {/* Tags (max 5) */}
      <div style={{ marginBottom: '10px' }}>
        {tags.map((tag, i) => <Tag key={i} text={shortenTag(tag.text)} sentiment={tag.sentiment} />)}
      </div>

      <CardFooter postingUrl={job.postingUrl} alumniCount={job.alumniCount} />
    </Section>
  );
}
