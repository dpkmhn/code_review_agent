import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createReview } from './api';
import ReviewJourney from './ReviewJourney';
import DiffView from './DiffView';
import { SiteFooter, SiteHeader } from './SiteChrome';
import { Pipeline, SPECIALISTS, SamplePreview, Scorecard, SpecialistPanel, parsePullRequestLink } from './ReviewPanels';
import type { PullRequest, Review, Severity, Specialist, ToolCall } from './types';

function githubPath(pr: PullRequest) {
  return `https://github.com/${encodeURIComponent(pr.owner)}/${encodeURIComponent(pr.repository)}`;
}

// Shared context retrieval first, then each specialist's calls in fan-out order.
function groupTools(tools: ToolCall[]) {
  return ([null, ...SPECIALISTS] as (Specialist | null)[])
    .map((specialist) => ({ specialist, calls: tools.filter((tool) => (tool.specialist ?? null) === specialist) }))
    .filter((group) => group.calls.length > 0);
}

function ReviewResult({ review }: { review: Review }) {
  const pr = review.pull_request;
  const tools = review.tool_calls;
  const [chosen, setFilter] = useState<Severity | null>(null);
  const [chosenAgent, setAgentFilter] = useState<Specialist | null>(null);
  // Drop filters whose severity or specialist no longer appears in this review.
  const filter = review.findings.some((finding) => finding.severity === chosen) ? chosen : null;
  const agentFilter = review.findings.some((finding) => finding.category === chosenAgent) ? chosenAgent : null;
  const shown = review.findings.filter((finding) => (!filter || finding.severity === filter)
    && (!agentFilter || finding.category === agentFilter));
  const filtered = Boolean(filter || agentFilter);
  return <section className="results" aria-labelledby="review-heading">
    <div className="result-heading">
      <div><p className="eyebrow">{pr.owner}/{pr.repository} · PR #{pr.number}</p><h2 id="review-heading">{pr.title}</h2></div>
      <span className={`status ${review.status}`}>{review.status}</span>
    </div>
    <div className="pr-meta"><span>{pr.head_branch} → {pr.base_branch}</span><span>{pr.files.length} changed files</span>
      <a href={`${githubPath(pr)}/pull/${pr.number}`} target="_blank" rel="noreferrer">Open pull request ↗</a></div>
    <Pipeline state={review.status} />
    <Scorecard review={review} filter={filter} onFilter={setFilter} />
    {review.specialists && review.specialists.length > 0 && <SpecialistPanel review={review} filter={agentFilter} onFilter={setAgentFilter} />}
    <p className="revision">Reviewed revision <code>{pr.head_sha.slice(0, 12)}</code></p>
    <h3>Findings <span className="count">{filtered ? `${shown.length} of ${review.findings.length}` : review.findings.length}</span>{filtered && <button type="button" className="link-button" onClick={() => { setFilter(null); setAgentFilter(null); }}>Show all</button>}</h3>
    {review.findings.length === 0 ? <div className="empty"><strong>{review.status === 'reviewed' ? 'No verified findings' : 'No findings available yet'}</strong>
      <p>{review.status === 'reviewed' ? 'The review completed without confirming a defect. This does not guarantee the change is bug-free.' : 'The review did not finish.'}</p></div>
      : <div className="findings">{shown.map((finding, index) => <article className={`finding sev-${finding.severity}`} key={`${finding.file}:${finding.line}:${index}`}>
        <div className="finding-top"><span className="finding-tags"><span className={`severity ${finding.severity}`}>{finding.severity}</span>{finding.category && <span className="category">{finding.category}</span>}</span><span className="finding-number">{String(index + 1).padStart(2, '0')}</span></div>
        <h4>{finding.title}</h4><a className="file-link" href={`${githubPath(pr)}/blob/${encodeURIComponent(pr.head_sha)}/${finding.file.split('/').map(encodeURIComponent).join('/')}${finding.line ? `#L${finding.line}` : ''}`} target="_blank" rel="noreferrer">{finding.file}{finding.line ? `:${finding.line}` : ''} ↗</a>
        <p>{finding.explanation}</p>
      </article>)}</div>}
    <DiffView files={pr.files} findings={review.findings} />
    {tools ? <details className="tools"><summary>Tool evidence ({tools.length} calls)</summary>
      {tools.length === 0 && <p className="muted">No tool calls were needed for this review.</p>}
      {groupTools(tools).map(({ specialist, calls }) => <div className="tool-group" key={specialist ?? 'context'}>
        <h4>{specialist ? `${specialist} agent` : 'Shared context'} <span className="count">{calls.length}</span></h4>
        {calls.map((tool) => <details className="tool" key={tool.call_id}><summary>{tool.name} · {tool.status}</summary>
          <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
          {tool.evidence && <p>{tool.evidence.path} @ {tool.evidence.ref} · {tool.evidence.succeeded ? 'Completed evidence' : 'Incomplete evidence'}{tool.evidence.exit_code != null ? ` · exit ${tool.evidence.exit_code}` : ''}{tool.evidence.timed_out ? ' · timed out' : ''}</p>}
          <pre>{tool.evidence?.content || tool.result || 'Awaiting tool result.'}</pre></details>)}
      </div>)}
    </details> : null}
  </section>;
}

export default function App() {
  const [repository, setRepository] = useState('');
  const [number, setNumber] = useState('');
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState('');
  const [operation, setOperation] = useState('');
  const [link, setLink] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const locked = useRef(false);
  const busy = Boolean(operation);

  useEffect(() => {
    if (!busy) return undefined;
    setElapsed(0);
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  function pasteLink(value: string) {
    setLink(value);
    const parsed = parsePullRequestLink(value);
    if (parsed) { setRepository(parsed.repository); setNumber(parsed.number); }
  }

  async function perform(label: string, action: () => Promise<Review>) {
    if (locked.current) return;
    locked.current = true;
    setOperation(label); setError('');
    try {
      const result = await action();
      setReview(result);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally { locked.current = false; setOperation(''); }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const repo = repository.trim();
    const prNumber = Number(number);
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo) || !Number.isSafeInteger(prNumber) || prNumber <= 0) {
      setError('Enter a repository as owner/repository and a positive whole PR number.'); return;
    }
    // Do not display a previous PR as the result of this submission.
    setReview(null);
    perform('Reviewing pull request…', () => createReview(repo, prNumber));
  }

  return <div className="product-layout"><nav className="app-nav" aria-label="Workspace navigation"><a className="nav-monogram" href="/" aria-label="Code Review Agent home">cr<span>.</span></a><a className="nav-item" href="#review-console" aria-label="New review"><span aria-hidden="true">⊞</span><small>Review</small></a><span className="nav-bottom" aria-hidden="true">&lt;/&gt;</span></nav><div className="app-shell">
    <SiteHeader busy={busy} review={review} />
    <main><div className="intro" id="how-it-works"><div className="hero-copy"><p className="eyebrow"><span aria-hidden="true">✦</span> AGENTIC CODE REVIEW</p><h1>A sharper review.<br /><span>A clearer merge.</span></h1><p>Go beyond the diff. Investigate potential bugs<br />with code context and evidence you can inspect.</p><div className="hero-capabilities"><span>Multi-agent</span><span>Context-aware</span><span>Verification-first</span><span>Human-reviewed</span></div></div><ReviewJourney /></div>
      <div className="workspace-heading" id="review-console"><span className="eyebrow"><i />REVIEW CONSOLE</span><span>{busy ? 'Request in progress' : review ? 'Review loaded' : 'Awaiting a pull request'}</span></div>
      <div className="workspace"><aside><section className="panel"><div className="panel-label">01 / INPUT</div><h2>New review</h2><p className="muted">Choose the change you want to investigate.</p>
        <form onSubmit={submit}><label htmlFor="pr-link">Paste a PR link <span className="optional">optional</span></label><input id="pr-link" placeholder="https://github.com/owner/repo/pull/123" value={link} onChange={(e) => pasteLink(e.target.value)} disabled={busy} autoCapitalize="none" spellCheck="false" />
          {link && !parsePullRequestLink(link) && <p className="field-hint">Not a GitHub pull request link. Fill in the fields below instead.</p>}
          <div className="or-divider" aria-hidden="true"><span>or enter it manually</span></div><label htmlFor="repository">GitHub repository</label><input id="repository" placeholder="owner/repository" value={repository} onChange={(e) => setRepository(e.target.value)} disabled={busy} required autoCapitalize="none" spellCheck="false" />
          <label htmlFor="pr-number">Pull request number</label><input id="pr-number" type="number" min="1" step="1" placeholder="123" value={number} onChange={(e) => setNumber(e.target.value)} disabled={busy} required />
          <button disabled={busy} type="submit">Start review <span aria-hidden="true">→</span></button></form>
        <p className="note">Reviews can take several minutes. Keep this page open while the review runs.</p></section>
      </aside><div className="review-area" aria-busy={busy}>
        {busy && <div className="notice" role="status"><div className="notice-line"><span className="spinner" aria-hidden="true" />{operation}</div>{operation.startsWith('Reviewing') ? <Pipeline state="running" elapsed={elapsed} /> : null}</div>}
        {error && <div className="error" role="alert"><strong>Request could not complete</strong><p>{error}</p></div>}
        {review ? <ReviewResult review={review} />
          : !busy && <section className="welcome"><div className="output-heading"><span className="panel-label">02 / INVESTIGATION</span><span className="output-state">No review loaded</span></div><div className="welcome-copy"><p className="eyebrow">THE SPACE BETWEEN A CLAIM AND A BUG</p><h2>Evidence makes the difference.</h2><p>Your findings will appear here. Start with a pull request;<br />the agent investigates the code behind the change.</p></div><div className="investigation-steps"><div><span className="step-icon" aria-hidden="true">±</span><div><h3>Read the change</h3><p>Inspect the diff and retrieve relevant repository context.</p></div><span className="step-number">01</span></div><div><span className="step-icon" aria-hidden="true">?</span><div><h3>Dispatch the specialists</h3><p>Correctness, security and performance agents investigate in parallel; a shared judge checks their evidence.</p></div><span className="step-number">02</span></div><div><span className="step-icon" aria-hidden="true">↗</span><div><h3>Follow the finding</h3><p>Review confirmed issues with links to the exact code revision.</p></div><span className="step-number">03</span></div></div><SamplePreview /><div className="welcome-footnote"><span aria-hidden="true">◇</span> Findings are earned through verification.</div></section>}
      </div></div>
    </main><SiteFooter />
  </div></div>;
}
