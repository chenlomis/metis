import * as React from 'react';
import { Link } from '@react-email/components';
import { FONT, C_BORDER, C_BTN, C_SUBTLE } from '../../utils/colors';
import type { Tier } from '../../types';

const BTN_COLOR: Record<Tier, string> = {
  apply:    '#2d5a2d',
  consider: '#854f0b',
  skipped:  C_BTN,
};

interface Props {
  postingUrl: string;
  alumniCount?: number;
  tier?: Tier;
}

export default function CardFooter({ postingUrl, alumniCount, tier = 'skipped' }: Props) {
  const btnBg = BTN_COLOR[tier];
  return (
    <table
      cellPadding={0}
      cellSpacing={0}
      border={0}
      style={{
        width: '100%',
        borderCollapse: 'collapse',
        borderTop: `1px solid ${C_BORDER}`,
      }}
    >
      <tbody>
        <tr>
          <td
            style={{
              fontSize: '11px',
              color: C_SUBTLE,
              fontFamily: FONT,
              verticalAlign: 'middle',
              paddingTop: '12px',
            }}
          >
            {alumniCount ? `${alumniCount} company alumni` : ''}
          </td>
          <td style={{ textAlign: 'right', verticalAlign: 'middle', paddingTop: '12px' }}>
            <Link
              href={postingUrl}
              style={{
                fontSize: '12px',
                fontWeight: 500,
                color: '#ffffff',
                textDecoration: 'none',
                background: btnBg,
                padding: '5px 12px',
                borderRadius: '4px',
                fontFamily: FONT,
                display: 'inline-block',
              }}
            >
              View posting &#8594;
            </Link>
          </td>
        </tr>
      </tbody>
    </table>
  );
}
