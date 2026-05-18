import * as React from 'react';
import { Link } from '@react-email/components';
import { FONT, C_BORDER, C_BORDER_LIGHT, C_LINK, C_SUBTLE } from '../../utils/colors';

interface Props {
  postingUrl: string;
  alumniCount?: number;
}

export default function CardFooter({ postingUrl, alumniCount }: Props) {
  return (
    <table
      cellPadding={0}
      cellSpacing={0}
      border={0}
      style={{
        width: '100%',
        borderCollapse: 'collapse',
        borderTop: `1px solid ${C_BORDER}`,
        paddingTop: '8px',
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
            }}
          >
            {alumniCount ? `${alumniCount} company alumni` : ''}
          </td>
          <td style={{ textAlign: 'right', verticalAlign: 'middle' }}>
            <Link
              href={postingUrl}
              style={{
                fontSize: '12px',
                fontWeight: 500,
                color: C_LINK,
                textDecoration: 'none',
                border: `1px solid ${C_BORDER_LIGHT}`,
                padding: '5px 10px',
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
