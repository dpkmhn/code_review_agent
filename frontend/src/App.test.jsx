import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App.jsx';

const id = 'cabc2095-3d3c-4455-97a3-017a4d9b71ef';
const pr = { owner: 'acme', repository: 'service', number: 42, title: 'Fix payments',
  base_branch: 'main', head_branch: 'fix', head_sha: 'a'.repeat(40), files: [] };
const result = { status: 'reviewed', review_id: null, pull_request: pr, findings: [] };
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function submit(user) {
  await user.type(screen.getByLabelText('GitHub repository'), 'acme/service');
  await user.type(screen.getByLabelText('Pull request number'), '42');
  await user.click(screen.getByRole('button', { name: /Start review/ }));
}

describe('Review workspace', () => {
  it('submits the API contract once while pending and renders a transient result', async () => {
    let finish;
    const fetch = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup(); render(<App />);
    await submit(user);
    expect(screen.getByRole('status').textContent).toContain('Reviewing');
    expect(screen.getByRole('button', { name: /Start review/ }).disabled).toBe(true);
    await user.click(screen.getByRole('button', { name: /Start review/ }));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ repository: 'acme/service', pull_request_number: 42 });
    finish(reply(result));
    await screen.findByText('No verified findings');
    expect(screen.getByText(/This review is transient/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Resume review' })).toBeNull();
  });

  it('rejects malformed repositories before contacting the backend', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup(); render(<App />);
    await user.type(screen.getByLabelText('GitHub repository'), 'https://github.com/acme/service');
    await user.type(screen.getByLabelText('Pull request number'), '42');
    await user.click(screen.getByRole('button', { name: /Start review/ }));
    expect(screen.getByRole('alert').textContent).toContain('owner/repository');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('retains the failed run ID, loads evidence, and resumes the original review', async () => {
    const stored = { ...result, review_id: id, status: 'failed', error: 'Review execution failed', tool_calls: [
      { call_id: 'call-1', name: 'read_file', status: 'completed', arguments: { path: 'pay.py' },
        evidence: { path: 'pay.py', ref: pr.head_sha, succeeded: true, content: '<script>untrusted()</script>', exit_code: null } },
    ] };
    const fetch = vi.fn().mockResolvedValueOnce(reply({ detail: { message: 'Review failed; retry the resume endpoint.', review_id: id } }, 502))
      .mockResolvedValueOnce(reply(stored)).mockResolvedValueOnce(reply({ ...stored, status: 'reviewed', error: null,
        findings: [{ severity: 'high', title: 'Missing validation', file: 'pay.py', line: 8, explanation: 'A missing value reaches the payment handler.' }] }));
    vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup(); render(<App />); await submit(user);
    await user.click(await screen.findByRole('button', { name: 'Load interrupted review' }));
    await screen.findByRole('button', { name: 'Resume review' });
    expect(fetch.mock.calls[1][0]).toBe(`/api/v1/reviews/${id}`);
    await user.click(screen.getByText('Tool evidence (1 calls)'));
    await user.click(screen.getByText('read_file · completed'));
    expect(screen.getByText('<script>untrusted()</script>')).toBeTruthy();
    expect(document.querySelector('script')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Resume review' }));
    await screen.findByText('Missing validation');
    expect(fetch.mock.calls[2]).toEqual([`/api/v1/reviews/${id}/resume`, { method: 'POST' }]);
    expect(screen.getByRole('link', { name: 'pay.py:8 ↗' }).href).toContain(`/blob/${pr.head_sha}/pay.py#L8`);
  });

  it('surfaces persistence-disabled and network errors without a false completed review', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(reply({ detail: 'Persistence requires database_url configuration' }, 503))
      .mockRejectedValueOnce(new TypeError('network'));
    vi.stubGlobal('fetch', fetch);
    const user = userEvent.setup(); render(<App />);
    await user.type(screen.getByLabelText('Review ID'), id);
    await user.click(screen.getByRole('button', { name: 'Load review' }));
    await screen.findByText('Persistence requires database_url configuration');
    await submit(user);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Cannot reach the API'));
    expect(screen.queryByText('No verified findings')).toBeNull();
  });
});
