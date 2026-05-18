import * as React from 'react';
import { FONT, TAG_COLORS } from '../../utils/colors';
import type { TagSentiment } from '../../types';

interface Props {
  text: string;
  sentiment: TagSentiment;
  fontSize?: number;
}

// Claude occasionally emits legacy keys ("yellow" → amber, "gap" → orange).
// Map them here rather than crashing or silently rendering green.
const SENTIMENT_ALIASES: Record<string, keyof typeof TAG_COLORS> = {
  yellow: 'amber',
  gap:    'orange',
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
        padding: '2px 8px',
        borderRadius: '20px',
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
