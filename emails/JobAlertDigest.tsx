import * as React from 'react';
import { Html, Head, Body, Container, Section } from '@react-email/components';
import DigestHeader from './components/DigestHeader';
import TierSection from './components/TierSection';
import SkippedGrid from './components/SkippedGrid';
import { FONT, C_BG_SECONDARY, C_BORDER, C_SUBTLE } from '../utils/colors';
import type { DigestPayload } from '../types';

interface Props {
  payload: DigestPayload;
}

export default function JobAlertDigest({ payload }: Props) {
  const applyJobs    = payload.jobs.filter((j) => j.verdict === 'apply');
  const considerJobs = payload.jobs.filter((j) => j.verdict === 'consider');
  const skippedJobs  = payload.jobs.filter((j) => j.verdict === 'skipped');

  return (
    <Html lang="en">
      <Head />
      <Body style={{ margin: 0, padding: 0, backgroundColor: C_BG_SECONDARY, fontFamily: FONT }}>
        {/* Preheader — hidden text shown as inbox preview snippet before email is opened */}
        <div style={{ display: 'none', overflow: 'hidden', lineHeight: '1px', opacity: 0, maxHeight: 0, maxWidth: 0 }}>
          {`ScoreRole — ${applyJobs.length} to apply · ${considerJobs.length} to consider — see your ${payload.date} breakdown`}
        </div>
        <Container style={{ maxWidth: '600px', margin: '0 auto', padding: '16px 12px' }}>
          <DigestHeader
            date={payload.date}
            totalEvaluated={payload.totalEvaluated}
            applyCount={applyJobs.length}
            considerCount={considerJobs.length}
            candidateName={payload.candidateName}
            greeting={payload.greeting}
            greetingSub={payload.greetingSub}
          />

          {applyJobs.length > 0 && <TierSection tier="apply" jobs={applyJobs} />}
          {considerJobs.length > 0 && <TierSection tier="consider" jobs={considerJobs} />}
          {skippedJobs.length > 0 && <SkippedGrid jobs={skippedJobs} />}

          {/* Footer */}
          <Section>
            <table cellPadding={0} cellSpacing={0} border={0} style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                <tr>
                  <td height={1} style={{ background: C_BORDER, fontSize: '0', lineHeight: '0' }}>&nbsp;</td>
                </tr>
                <tr>
                  <td style={{ paddingTop: '12px', fontSize: '11px', color: C_SUBTLE, textAlign: 'center', fontFamily: FONT }}>
                    ScoreRole &middot; powered by Claude &middot;{' '}
                    {payload.totalEvaluated} roles evaluated
                  </td>
                </tr>
              </tbody>
            </table>
          </Section>
        </Container>
      </Body>
    </Html>
  );
}
