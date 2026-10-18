// Mirrors the backend response models in app/schemas.

export type Severity = 'low' | 'medium' | 'high' | 'critical';
export type ReviewStatus = 'pending' | 'running' | 'reviewed' | 'failed';
export type Specialist = 'correctness' | 'security' | 'performance';

export interface PullRequestFile {
  filename: string;
  status: string;
  additions: number;
  deletions: number;
  changes: number;
  patch?: string | null;
}

export interface PullRequest {
  owner: string;
  repository: string;
  number: number;
  title: string;
  body?: string | null;
  base_branch: string;
  head_branch: string;
  base_sha?: string;
  head_sha: string;
  files: PullRequestFile[];
}

export interface Finding {
  file: string;
  line: number | null;
  severity: Severity;
  title: string;
  explanation: string;
  category?: Specialist | null;
}

export interface VerificationEvidence {
  source?: 'diff' | 'read_file' | 'list_tree' | 'run_test' | 'run_static_analysis';
  path: string;
  ref: string;
  content: string;
  succeeded: boolean;
  exit_code: number | null;
  timed_out?: boolean;
}

export interface ToolCall {
  call_id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: 'pending' | 'completed';
  result?: string | null;
  evidence?: VerificationEvidence | null;
  // Null for shared context retrieval that runs before the specialists.
  specialist?: Specialist | null;
}

export interface Review {
  status: ReviewStatus;
  pull_request: PullRequest;
  findings: Finding[];
  tool_calls?: ToolCall[];
  specialists?: Specialist[];
}
