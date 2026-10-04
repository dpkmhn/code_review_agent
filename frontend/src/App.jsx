import React, { useEffect, useRef, useState } from 'react';
import { createReview, getReview, isReviewId, resumeReview } from './api.js';
import ReviewJourney from './ReviewJourney.jsx';
import { Pipeline, SamplePreview, Scorecard, parsePullRequestLink } from './ReviewPanels.jsx';

function githubPath(pr) {
  return `https://github.com/${encodeURIComponent(pr.owner)}/${encodeURIComponent(pr.repository)}`;
}

function ReviewResult({ review, busy, onRefresh, onResume }) {
  const pr = review.pull_request;
  const saved = Boolean(review.review_id);
  const tools = review.tool_calls;
  const [chosen, setFilter] = useState(null);
  // Drop a filter whose severity no longer appears (e.g. after a resume).
  const filter = review.findings.some((finding) => finding.severity === chosen) ? chosen : null;
  const shown = filter ? review.findings.filter((finding) => finding.severity === filter) : review.findings;
  return <section className="results" aria-labelledby="review-heading">
    <div className="result-heading">
      <div><p className="eyebrow">{pr.owner}/{pr.repository} · PR #{pr.number}</p><h2 id="review-heading">{pr.title}</h2></div>
      <span className={`status ${review.status}`}>{review.status}</span>
    </div>
    <div className="pr-meta"><span>{pr.head_branch} → {pr.base_branch}</span><span>{pr.files.length} changed files</span>
      <a href={`${githubPath(pr)}/pull/${pr.number}`} target="_blank" rel="noreferrer">Open pull request ↗</a></div>
    <Pipeline state={review.status} />
    <Scorecard review={review} filter={filter} onFilter={setFilter} />
    <p className="revision">Reviewed revision <code>{pr.head_sha.slice(0, 12)}</code></p>
    {saved ? <div className="saved-review"><div><span>Review ID</span><code>{review.review_id}</code></div>
      <div className="actions"><button className="secondary" disabled={busy} onClick={onRefresh}>Refresh status & evidence</button>
        {['failed', 'pending', 'running'].includes(review.status) && <button disabled={busy} onClick={onResume}>Resume review</button>}</div>
      {review.status === 'running' && <p>Resume recovers an interrupted run. An active worker returns “already running”.</p>}
    </div> : <p className="muted">This review is transient. Enable backend persistence to save and resume reviews.</p>}
    {review.error && <p className="error" role="alert">{review.error}</p>}
    <h3>Findings <span className="count">{filter ? `${shown.length} of ${review.findings.length}` : review.findings.length}</span>{filter && <button type="button" className="link-button" onClick={() => setFilter(null)}>Show all</button>}</h3>
    {review.findings.length === 0 ? <div className="empty"><strong>{review.status === 'reviewed' ? 'No verified findings' : 'No findings available yet'}</strong>
      <p>{review.status === 'reviewed' ? 'The review completed without confirming a defect. This does not guarantee the change is bug-free.' : 'Refresh the saved review to check its latest status.'}</p></div>
      : <div className="findings">{shown.map((finding, index) => <article className={`finding sev-${finding.severity}`} key={`${finding.file}:${finding.line}:${index}`}>
        <div className="finding-top"><span className={`severity ${finding.severity}`}>{finding.severity}</span><span className="finding-number">{String(index + 1).padStart(2, '0')}</span></div>
        <h4>{finding.title}</h4><a className="file-link" href={`${githubPath(pr)}/blob/${encodeURIComponent(pr.head_sha)}/${finding.file.split('/').map(encodeURIComponent).join('/')}${finding.line ? `#L${finding.line}` : ''}`} target="_blank" rel="noreferrer">{finding.file}{finding.line ? `:${finding.line}` : ''} ↗</a>
        <p>{finding.explanation}</p>
      </article>)}</div>}
    <details className="files"><summary>Changed files ({pr.files.length})</summary>
      <ul>{pr.files.map((file) => <li key={file.filename}><code>{file.filename}</code><span>{file.status} · <b className="additions">+{file.additions}</b> <b className="deletions">−{file.deletions}</b></span></li>)}</ul>
    </details>
    {tools ? <details className="tools"><summary>Tool evidence ({tools.length} calls)</summary>
      {tools.length === 0 && <p className="muted">No checkpointed tool calls for this review.</p>}
      {tools.map((tool) => <details className="tool" key={tool.call_id}><summary>{tool.name} · {tool.status}</summary>
        <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
        {tool.evidence && <p>{tool.evidence.path} @ {tool.evidence.ref} · {tool.evidence.succeeded ? 'Completed evidence' : 'Incomplete evidence'}{tool.evidence.exit_code != null ? ` · exit ${tool.evidence.exit_code}` : ''}{tool.evidence.timed_out ? ' · timed out' : ''}</p>}
        <pre>{tool.evidence?.content || tool.result || 'Awaiting tool result.'}</pre></details>)}
    </details> : saved && <p className="muted">Refresh status & evidence to load the saved tool records.</p>}
  </section>;
}

export default function App() {
  const [repository, setRepository] = useState('');
  const [number, setNumber] = useState('');
  const [reviewId, setReviewId] = useState('');
  const [review, setReview] = useState(null);
  const [error, setError] = useState('');
  const [recoveryId, setRecoveryId] = useState(null);
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

  function pasteLink(value) {
    setLink(value);
    const parsed = parsePullRequestLink(value);
    if (parsed) { setRepository(parsed.repository); setNumber(parsed.number); }
  }

  async function perform(label, action) {
    if (locked.current) return;
    locked.current = true;
    setOperation(label); setError(''); setRecoveryId(null);
    try {
      const result = await action();
      setReview(result);
      if (result.review_id) setReviewId(result.review_id);
    } catch (failure) {
      setError(failure.message);
      if (failure.reviewId) { setRecoveryId(failure.reviewId); setReviewId(failure.reviewId); }
    } finally { locked.current = false; setOperation(''); }
  }

  function submit(event) {
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

  function lookup(event) {
    event.preventDefault();
    const id = reviewId.trim();
    if (!isReviewId(id)) { setError('Enter a valid review UUID.'); return; }
    perform('Loading saved review…', () => getReview(id));
  }

  return <div className="product-layout"><nav className="app-nav" aria-label="Workspace navigation"><a className="nav-monogram" href="/" aria-label="Code Review Agent home">cr<span>.</span></a><a className="nav-item" href="#review-console" aria-label="New review"><span aria-hidden="true">⊞</span><small>Review</small></a><a className="nav-item" href="#saved-review" aria-label="Saved review lookup"><span aria-hidden="true">↺</span><small>Recover</small></a><span className="nav-bottom" aria-hidden="true">&lt;/&gt;</span></nav><div className="app-shell">
    <header><span className="brand">Code Review Agent <span className="breadcrumb">/ Workspace</span></span><span className="header-note">GITHUB PULL REQUESTS</span></header>
    <main><div className="intro"><div className="hero-copy"><p className="eyebrow"><span aria-hidden="true">✦</span> AGENTIC CODE REVIEW</p><h1>A sharper review.<br /><span>A clearer merge.</span></h1><p>Go beyond the diff. Investigate potential bugs<br />with code context and evidence you can inspect.</p><div className="hero-capabilities"><span>Context-aware</span><span>Verification-first</span><span>Human-reviewed</span></div></div><ReviewJourney /></div>
      <div className="workspace-heading" id="review-console"><span className="eyebrow"><i />REVIEW CONSOLE</span><span>{busy ? 'Request in progress' : review ? 'Review loaded' : 'Awaiting a pull request'}</span></div>
      <div className="workspace"><aside><section className="panel"><div className="panel-label">01 / INPUT</div><h2>New review</h2><p className="muted">Choose the change you want to investigate.</p>
        <form onSubmit={submit}><label htmlFor="pr-link">Paste a PR link <span className="optional">optional</span></label><input id="pr-link" placeholder="https://github.com/owner/repo/pull/123" value={link} onChange={(e) => pasteLink(e.target.value)} disabled={busy} autoCapitalize="none" spellCheck="false" />
          {link && !parsePullRequestLink(link) && <p className="field-hint">Not a GitHub pull request link. Fill in the fields below instead.</p>}
          <div className="or-divider" aria-hidden="true"><span>or enter it manually</span></div><label htmlFor="repository">GitHub repository</label><input id="repository" placeholder="owner/repository" value={repository} onChange={(e) => setRepository(e.target.value)} disabled={busy} required autoCapitalize="none" spellCheck="false" />
          <label htmlFor="pr-number">Pull request number</label><input id="pr-number" type="number" min="1" step="1" placeholder="123" value={number} onChange={(e) => setNumber(e.target.value)} disabled={busy} required />
          <button disabled={busy} type="submit">Start review <span aria-hidden="true">→</span></button></form>
        <p className="note">Reviews can take several minutes. Keep this page open while the review runs.</p></section>
        <section className="panel lookup" id="saved-review"><div className="panel-label">RECOVERY</div><h2>Open a saved review</h2><p className="muted">Requires PostgreSQL persistence on the backend.</p><form onSubmit={lookup}><label htmlFor="review-id">Review ID</label><input id="review-id" placeholder="Review UUID" value={reviewId} onChange={(e) => setReviewId(e.target.value)} disabled={busy} required spellCheck="false" /><button className="secondary" disabled={busy}>Load review</button></form></section>
      </aside><div className="review-area" aria-busy={busy}>
        {busy && <div className="notice" role="status"><div className="notice-line"><span className="spinner" aria-hidden="true" />{operation}</div>{operation.startsWith('Reviewing') || operation.startsWith('Resuming') ? <Pipeline state="running" elapsed={elapsed} /> : null}</div>}
        {error && <div className="error" role="alert"><strong>Request could not complete</strong><p>{error}</p>{recoveryId && <><p>Saved review ID: <code>{recoveryId}</code></p><button className="secondary" disabled={busy} onClick={() => perform('Loading saved review…', () => getReview(recoveryId))}>Load interrupted review</button></>}</div>}
        {review ? <ReviewResult review={review} busy={busy} onRefresh={() => perform('Loading saved review…', () => getReview(review.review_id))} onResume={() => perform('Resuming review…', () => resumeReview(review.review_id))} />
          : !busy && <section className="welcome"><div className="output-heading"><span className="panel-label">02 / INVESTIGATION</span><span className="output-state">No review loaded</span></div><div className="welcome-copy"><p className="eyebrow">THE SPACE BETWEEN A CLAIM AND A BUG</p><h2>Evidence makes the difference.</h2><p>Your findings will appear here. Start with a pull request;<br />the agent investigates the code behind the change.</p></div><div className="investigation-steps"><div><span className="step-icon" aria-hidden="true">±</span><div><h3>Read the change</h3><p>Inspect the diff and retrieve relevant repository context.</p></div><span className="step-number">01</span></div><div><span className="step-icon" aria-hidden="true">?</span><div><h3>Challenge the hypothesis</h3><p>Investigate potential defects and collect supporting evidence.</p></div><span className="step-number">02</span></div><div><span className="step-icon" aria-hidden="true">↗</span><div><h3>Follow the finding</h3><p>Review confirmed issues with links to the exact code revision.</p></div><span className="step-number">03</span></div></div><SamplePreview /><div className="welcome-footnote"><span aria-hidden="true">◇</span> Findings are earned through verification.</div></section>}
      </div></div>
    </main><footer>Code Review Agent <span>Human judgment, supported by repository evidence.</span></footer>
  </div></div>;
}
