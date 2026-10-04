import React from 'react';

export const SEVERITIES = ['critical', 'high', 'medium', 'low'];

// Mirrors the backend graph; the API does not stream per-stage progress, so stages are shown as a whole.
const STAGES = [
  ['⇣', 'Fetch PR'], ['±', 'Analyse diff'], ['⌕', 'Context'],
  ['?', 'Hypotheses'], ['⚙', 'Verify'], ['✓', 'Report'],
];

export function parsePullRequestLink(value) {
  const match = value.trim().match(/^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\/pull\/(\d+)/i)
    || value.trim().match(/^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)#(\d+)$/);
  return match ? { repository: `${match[1]}/${match[2]}`, number: match[3] } : null;
}

function formatElapsed(seconds) {
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}m ${String(seconds % 60).padStart(2, '0')}s` : `${seconds}s`;
}

export function Pipeline({ state, elapsed }) {
  const caption = {
    running: `Running on the server · ${formatElapsed(elapsed ?? 0)} elapsed`,
    reviewed: 'Review complete',
    failed: 'Run stopped before completing — resume it to continue',
    pending: 'Saved run in progress on the server',
  }[state] || 'Saved run in progress on the server';
  return <div className={`pipeline is-${state}`}>
    <ol aria-label="Review stages">
      {STAGES.map(([icon, label], index) => <li key={label} style={{ '--i': index }}>
        <span className="stage-icon" aria-hidden="true">{icon}</span><small>{label}</small>
      </li>)}
    </ol>
    <p className="pipeline-caption">{caption}</p>
    {state === 'running' && <p className="pipeline-hint">Stages run server-side; live per-stage progress isn't streamed yet.</p>}
  </div>;
}

export function verdictFor(review) {
  if (review.status !== 'reviewed') return { tone: 'pending', label: 'Verdict pending', detail: 'The review has not finished.' };
  const counts = countBySeverity(review.findings);
  if (counts.critical || counts.high) return { tone: 'danger', label: 'Request changes', detail: `${counts.critical + counts.high} high-impact finding${counts.critical + counts.high === 1 ? '' : 's'} to address before merging.` };
  if (counts.medium) return { tone: 'warn', label: 'Changes suggested', detail: `${counts.medium} medium finding${counts.medium === 1 ? '' : 's'} worth a look.` };
  if (counts.low) return { tone: 'note', label: 'Minor notes', detail: 'Only low-severity findings were confirmed.' };
  return { tone: 'ok', label: 'No blocking findings', detail: 'Nothing was confirmed. A human review is still recommended.' };
}

export function countBySeverity(findings) {
  return Object.fromEntries(SEVERITIES.map((s) => [s, findings.filter((f) => f.severity === s).length]));
}

export function Scorecard({ review, filter, onFilter }) {
  const counts = countBySeverity(review.findings);
  const verdict = verdictFor(review);
  return <div className="scorecard">
    <div className="score-tiles">
      {SEVERITIES.map((severity) => <button type="button" key={severity} className={`score-tile ${severity}`}
        aria-pressed={filter === severity} disabled={!counts[severity]}
        onClick={() => onFilter(filter === severity ? null : severity)}>
        <span className="score-count">{counts[severity]}</span><span className="score-label">{severity}</span>
      </button>)}
    </div>
    <div className={`verdict ${verdict.tone}`}>
      <span className="verdict-kicker">Suggested verdict</span>
      <strong>{verdict.label}</strong><span>{verdict.detail}</span>
    </div>
  </div>;
}

export function SamplePreview() {
  return <div className="sample" aria-label="Example review, not a live result">
    <div className="sample-head"><span className="panel-label">EXAMPLE</span><code>auth/login.py</code></div>
    <pre className="sample-diff"><span className="ctx">def authenticate(username, password):</span>
<span className="del">-    query = f"SELECT * FROM users WHERE name='{'{username}'}'"</span>
<span className="add">+    query = "SELECT * FROM users WHERE name = %s"</span>
<span className="add">+    cursor.execute(query, (username,))</span>
<span className="ctx">def get_token(user):</span>
<span className="del">-    return jwt.encode({'{'}"user": user{'}'}, "hardcoded_secret")</span>
<span className="add">+    return jwt.encode({'{'}"user": user{'}'}, os.environ["JWT_SECRET"])</span></pre>
    <div className="sample-finding"><span className="severity medium">medium</span>
      <div><strong>Missing secret raises at request time</strong>
        <p><code>os.environ["JWT_SECRET"]</code> throws <code>KeyError</code> on every login when unset. Verified by reading the settings loader.</p></div></div>
  </div>;
}
