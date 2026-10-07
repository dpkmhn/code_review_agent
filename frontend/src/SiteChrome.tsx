import { useEffect, useState } from 'react';
import type { Review } from './types';

type Health = 'checking' | 'online' | 'degraded' | 'offline';

const HEALTH_INTERVAL_MS = 30000;

// Polls the backend health endpoint so the header reflects whether reviews can run.
function useApiHealth() {
  const [health, setHealth] = useState<Health>('checking');
  useEffect(() => {
    let active = true;
    async function check() {
      try {
        const response = await fetch('/health');
        const data = await response.json();
        if (active) setHealth(response.ok && data.status === 'ok' ? 'online' : 'degraded');
      } catch {
        if (active) setHealth('offline');
      }
    }
    check();
    const timer = setInterval(check, HEALTH_INTERVAL_MS);
    return () => { active = false; clearInterval(timer); };
  }, []);
  return health;
}

const HEALTH_LABEL: Record<Health, string> = { checking: 'Checking API…', online: 'API online', degraded: 'API degraded', offline: 'API offline' };

function ApiStatus({ health }: { health: Health }) {
  return <span className={`api-status ${health}`} aria-live="polite" title={health === 'offline' ? 'Start the backend on port 8000' : undefined}>
    <i aria-hidden="true" />{HEALTH_LABEL[health]}
  </span>;
}

function LogoMark() {
  return <span className="logo-mark" aria-hidden="true">
    <svg viewBox="0 0 24 24"><circle cx="6" cy="6" r="2.4" /><circle cx="6" cy="18" r="2.4" /><circle cx="18" cy="12" r="2.4" />
      <path d="M6 8.4v7.2M8.2 6.8c5 1 7.5 2.6 7.8 3.6" /></svg>
  </span>;
}

export function SiteHeader({ busy, review }: { busy: boolean; review: Review | null }) {
  const health = useApiHealth();
  const activity = busy ? 'Review running' : review ? `${review.pull_request.owner}/${review.pull_request.repository} #${review.pull_request.number}` : null;
  return <header className="site-header">
    <a className="brand" href="/"><LogoMark /><span className="brand-text"><strong>Code Review Agent</strong><small>Agentic PR review</small></span></a>
    <div className="header-meta">
      {activity && <span className={`header-activity${busy ? ' busy' : ''}`}>{activity}</span>}
      <ApiStatus health={health} />
      <a className="header-cta" href="#review-console">New review <span aria-hidden="true">→</span></a>
    </div>
  </header>;
}

export function SiteFooter() {
  return <footer className="site-footer">
    <div className="footer-grid">
      <div className="footer-brand">
        <a className="brand" href="/"><LogoMark /><span className="brand-text"><strong>Code Review Agent</strong><small>Agentic PR review</small></span></a>
        <p>Investigates pull requests with repository context and verifies each claim before it becomes a finding.</p>
      </div>
      <div><h2>Product</h2><ul>
        <li><a href="#review-console">Start a review</a></li>
        <li><a href="#how-it-works">How it works</a></li>
      </ul></div>
      <div><h2>Built with</h2><ul className="footer-stack">
        {['FastAPI', 'LangGraph', 'GitHub REST API', 'React + Vite'].map((item) => <li key={item}>{item}</li>)}
      </ul></div>
    </div>
    <div className="footer-bottom">
      <span>Findings are suggestions. A human approves every merge.</span>
      <span className="footer-mono">human judgment · repository evidence</span>
    </div>
  </footer>;
}
