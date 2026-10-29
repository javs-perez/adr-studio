export interface Anchor {
  quote: string;
  start: number;
  end: number;
  prefix: string;
  suffix: string;
  sha: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  status: 'complete' | 'streaming' | 'failed' | 'cancelled';
  job_id: string | null;
  created_at: string;
}

export interface Thread {
  id: string;
  anchor: Anchor;
  resolved: boolean;
  messages: Message[];
}

export interface Source {
  url: string;
  title: string;
  excerpt: string;
  retrieved_at: string;
}

export interface Provocation {
  id: string;
  job_id: string;
  title: string;
  category: 'precedent' | 'standard' | 'tradeoff' | 'question';
  quote: string;
  evidence: string;
  question: string;
  sources: Source[];
  status: 'open' | 'saved' | 'dismissed';
  rank: number;
  score: number | null;
  ranking: Record<RankingCriterion, { score: number; reason: string }> | null;
}

export type RankingCriterion = 'decision_impact' | 'evidence_quality' | 'novelty' | 'actionability';

export interface FeedbackItem {
  id: string;
  thread_id: string;
  title: string;
  body: string;
  suggested_title: string;
  suggested_body: string;
  included: boolean;
  anchor: Anchor;
}

export interface Job {
  id: string;
  kind: 'chat' | 'research' | 'feedback';
  thread_id: string | null;
  status: 'queued' | 'running' | 'complete' | 'failed' | 'cancelled';
  activity: string;
  answer: string;
  error: string | null;
  created_at: string;
}

export interface DocumentSummary {
  id: string;
  title: string;
  repo: string;
  path: string;
  sha: string;
}

export interface Document extends DocumentSummary {
  body: string;
  url: string;
  pr_url: string;
  created_at: string;
}

export interface Review {
  document: Document;
  threads: Thread[];
  provocations: Provocation[];
  jobs: Job[];
  archived_research_count: number;
  ranking_policy: {
    weights: Record<RankingCriterion, number>;
    scale_max: number;
    minimum: number;
  };
  feedback_items: FeedbackItem[];
  feedback_review: string;
  slack_enabled: boolean;
}
