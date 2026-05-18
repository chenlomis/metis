import * as React from 'react';
import { FONT, TAG_COLORS } from '../../utils/colors';
import type { TagSentiment } from '../../types';

interface Props {
  text: string;
  sentiment: TagSentiment;
  fontSize?: number;
}

// Claude occasionally emits legacy keys — map them to current sentiments rather than crashing.
const SENTIMENT_ALIASES: Record<string, keyof typeof TAG_COLORS> = {
  yellow: 'amber',
  orange: 'amber',  // orange collapsed into amber
  gap:    'amber',  // legacy "gap" sentiment
};

export default function Tag({ text, sentiment, fontSize = 11 }: Props) {
  const resolved = SENTIMENT_ALIASES[sentiment as string] ?? sentiment;
  const colors = TAG_COLORS[resolved as keyof typeof TAG_COLORS] ?? TAG_COLORS.amber;
  const { background, color } = colors;
  return (
    <span
      style={{
        background,
        color,
        fontSize,
        fontFamily: FONT,
        padding: '3px 9px',
        borderRadius: '20px',
        fontWeight: 500,
        display: 'inline-block',
        marginRight: '4px',
        marginBottom: '4px',
        whiteSpace: 'nowrap' as const,
      }}
    >
      {text}
    </span>
  );
}
