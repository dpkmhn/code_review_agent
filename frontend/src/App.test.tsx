import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import App from './App';
import type { Review } from './types';

const pr = { owner: 'acme', repository: 'service', number: 42, title: 'Fix payments',
  base_branch: 'main', head_branch: 'fix', head_sha: 'a'.repeat(40), files: [] };
const result: Review = { status: 'reviewed', pull_request: pr, findings: [] };
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
// The header polls /health; answer it separately so review mocks keep their call order.
const withHealth = (fetch: (url: string, options?: RequestInit) => unknown) => (url: string, options?: RequestInit) => (url === '/health' ? Promise.resolve(reply({ status: 'ok' })) : fetch(url, options));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function submit(user: UserEvent) {
  await user.type(screen.getByLabelText('GitHub repository'), 'acme/service');
  await user.type(screen.getByLabelText('Pull request number'), '42');
  await user.click(screen.getByRole('button', { name: /Start review/ }));
}

describe('Review workspace', () => {
  it('submits the API contract once while pending and renders the result', async () => {
    let finish: (value: unknown) => void = () => {};
    const fetch = vi.fn((_url: string, _options?: RequestInit) => new Promise((resolve) => { finish = resolve; }));
    vi.stubGlobal('fetch', withHealth(fetch));
    const user = userEvent.setup(); render(<App />);
    await submit(user);
    expect(screen.getByRole('status').textContent).toContain('Reviewing');
    expect((screen.getByRole('button', { name: /Start review/ }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole('button', { name: /Start review/ }));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({ repository: 'acme/service', pull_request_number: 42 });
    finish(reply(result));
    await screen.findByText('No verified findings');
  });

  it('rejects malformed repositories before contacting the backend', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', withHealth(fetch));
    const user = userEvent.setup(); render(<App />);
    await user.type(screen.getByLabelText('GitHub repository'), 'https://github.com/acme/service');
    await user.type(screen.getByLabelText('Pull request number'), '42');
    await user.click(screen.getByRole('button', { name: /Start review/ }));
    expect(screen.getByRole('alert').textContent).toContain('owner/repository');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('renders findings and tool evidence returned with the review', async () => {
    const reviewed = { ...result, tool_calls: [
      { call_id: 'call-1', name: 'read_file', status: 'completed', arguments: { path: 'pay.py' },
        evidence: { path: 'pay.py', ref: pr.head_sha, succeeded: true, content: '<script>untrusted()</script>', exit_code: null } },
    ], findings: [{ severity: 'high', title: 'Missing validation', file: 'pay.py', line: 8, explanation: 'A missing value reaches the payment handler.' }] };
    vi.stubGlobal('fetch', withHealth(vi.fn().mockResolvedValueOnce(reply(reviewed))));
    const user = userEvent.setup(); render(<App />); await submit(user);
    await screen.findByText('Missing validation');
    expect((screen.getByRole('link', { name: 'pay.py:8 ↗' }) as HTMLAnchorElement).href).toContain(`/blob/${pr.head_sha}/pay.py#L8`);
    await user.click(screen.getByText('Tool evidence (1 calls)'));
    await user.click(screen.getByText('read_file · completed'));
    expect(screen.getByText('<script>untrusted()</script>')).toBeTruthy();
    expect(document.querySelector('script')).toBeNull();
  });

  it('shows which specialists ran, filters by agent and groups tool evidence', async () => {
    const reviewed: Review = { ...result, specialists: ['correctness', 'security'], tool_calls: [
      { call_id: 'context-1', name: 'search_code', status: 'completed', arguments: { query: 'pay' } },
      { call_id: 'security-verification-1', name: 'read_file', status: 'completed', arguments: { path: 'auth.py' }, specialist: 'security' },
    ], findings: [
      { severity: 'high', title: 'Missing validation', file: 'pay.py', line: 8, explanation: 'A missing value reaches the payment handler.', category: 'correctness' },
      { severity: 'critical', title: 'SQL injection', file: 'auth.py', line: 3, explanation: 'Username is interpolated into the query.', category: 'security' },
    ] };
    vi.stubGlobal('fetch', withHealth(vi.fn().mockResolvedValueOnce(reply(reviewed))));
    const user = userEvent.setup(); render(<App />); await submit(user);
    await screen.findByText('Supervisor dispatched 2 of 3 · one shared evidence judge');
    expect(screen.getByRole('button', { name: /performance.*skipped/i })).toHaveProperty('disabled', true);
    await user.click(screen.getByRole('button', { name: /security.*1 finding/i }));
    expect(screen.queryByText('Missing validation')).toBeNull();
    expect(screen.getByText('SQL injection')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Show all' }));
    expect(screen.getByText('Missing validation')).toBeTruthy();
    await user.click(screen.getByText('Tool evidence (2 calls)'));
    expect(screen.getByText('Shared context')).toBeTruthy();
    expect(screen.getByText('security agent')).toBeTruthy();
  });

  it('surfaces API and network errors without a false completed review', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(reply({ detail: 'GitHub API request failed' }, 502))
      .mockRejectedValueOnce(new TypeError('network'));
    vi.stubGlobal('fetch', withHealth(fetch));
    const user = userEvent.setup(); render(<App />);
    await submit(user);
    await screen.findByText('GitHub API request failed');
    await user.click(screen.getByRole('button', { name: /Start review/ }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Cannot reach the API'));
    expect(screen.queryByText('No verified findings')).toBeNull();
  });
});
