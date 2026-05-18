import * as React from 'react';
import { FONT, C_BODY, C_ARROW_UP, C_ARROW_DOWN } from '../../utils/colors';

interface Props {
  direction: 'up' | 'down';
  text: string;
}

export default function PointRow({ direction, text }: Props) {
  const isUp       = direction === 'up';
  const arrowColor = isUp ? C_ARROW_UP : C_ARROW_DOWN;
  const textColor  = isUp ? C_BODY     : C_ARROW_DOWN;
  return (
    <table
      cellPadding={0}
      cellSpacing={0}
      border={0}
      style={{ width: '100%', marginBottom: '4px', borderCollapse: 'collapse' }}
    >
      <tbody>
        <tr>
          <td
            style={{
              width: '16px',
              fontSize: '12px',
              fontWeight: 500,
              color: arrowColor,
              verticalAlign: 'top',
              paddingTop: '1px',
              fontFamily: FONT,
            }}
          >
            {isUp ? '↑' : '↓'}
          </td>
          <td style={{ fontSize: '13px', color: textColor, lineHeight: '1.55', fontFamily: FONT }}>
            {text}
          </td>
        </tr>
      </tbody>
    </table>
  );
}
