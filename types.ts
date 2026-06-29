export type Tier = 'apply' | 'consider' | 'skipped';
export type TagSentiment = 'green' | 'amber' | 'red' | 'neutral';

export interface Tag {
  text: string;
  sentiment: TagSentiment;
}

export interface ScoreDimension {
  name: string;
  score: number;
  weight: number;
  rationale: string;
}

export interface Job {
  title: string;
  company: string;
  location: string;
  score: number;
  verdict: Tier;
  leveragePoints: string[];
  frictionPoints: string[];
  tags: Tag[];
  dimensions?: ScoreDimension[];
  alumniCount?: number;
  postingUrl: string;
}

export interface DigestPayload {
  date: string;
  totalEvaluated: number;
  candidateName: string;
  greeting: string;       // salutation line: "Good morning, Alex 👋"
  greetingSub?: string;   // body line: "We evaluated N roles today…"
  jobs: Job[];
}
