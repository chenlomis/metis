import * as React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { render } from '@react-email/render';
import JobAlertDigest from './emails/JobAlertDigest';
import type { DigestPayload } from './types';

const payloadPath = process.argv[2];
if (!payloadPath) {
  console.error('Usage: ts-node render.ts <path/to/payload.json>');
  process.exit(1);
}

let raw: string;
try {
  raw = fs.readFileSync(path.resolve(payloadPath), 'utf8');
} catch (e) {
  console.error(`Cannot read payload file: ${payloadPath}`);
  process.exit(1);
}

let payload: DigestPayload;
try {
  payload = JSON.parse(raw) as DigestPayload;
} catch (e) {
  console.error('payload.json is not valid JSON');
  process.exit(1);
}

// Validate required fields
if (!payload.date)                      throw new Error('payload.date is required');
if (typeof payload.totalEvaluated !== 'number') throw new Error('payload.totalEvaluated must be a number');
if (!Array.isArray(payload.jobs))       throw new Error('payload.jobs must be an array');

if (process.env.DEBUG_PAYLOAD) {
  console.error('=== payload field audit ===');
  payload.jobs.slice(0, 5).forEach((j, i) => {
    const sentiments = j.tags.map((t) => `${t.text}:${t.sentiment}`).join(', ');
    console.error(`[${i}] title="${j.title}" company="${j.company}" location="${j.location}" alumniCount=${j.alumniCount}`);
    console.error(`     tags: ${sentiments || '(none)'}`);
  });
  console.error('===========================');
}

const element = React.createElement(JobAlertDigest, { payload });
const result = render(element, { pretty: false });

// render() may return string or Promise<string> depending on version
Promise.resolve(result).then((html) => {
  process.stdout.write(html);
}).catch((err) => {
  console.error('Render failed:', err);
  process.exit(1);
});
