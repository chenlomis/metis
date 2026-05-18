export type Tier = 'apply' | 'consider' | 'skipped';
export type TagSentiment = 'green' | 'amber' | 'red' | 'orange';

export interface Tag {
  text: string;
  sentiment: TagSentiment;
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
  alumniCount?: number;
  postingUrl: string;
}

export interface DigestPayload {
  date: string;
  totalEvaluated: number;
  jobs: Job[];
}
