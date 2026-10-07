import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import DiffView, { parsePatch } from './DiffView';
import type { Finding, PullRequestFile } from './types';

afterEach(cleanup);

const patch = '@@ -1,3 +1,3 @@\n def pay(amount):\n-    return charge(amount)\n+    return charge(int(amount))\n     # done';

describe('DiffView', () => {
  it('numbers old and new lines from hunk headers', () => {
    const rows = parsePatch('@@ -10,2 +20,3 @@\n a\n-b\n+c\n+d');
    expect(rows.slice(1)).toEqual([
      { type: 'ctx', text: 'a', oldLine: 10, newLine: 20 },
      { type: 'del', text: 'b', oldLine: 11 },
      { type: 'add', text: 'c', newLine: 21 },
      { type: 'add', text: 'd', newLine: 22 },
    ]);
  });

  it('pins findings to their line and lists ones outside the patch', () => {
    const files: PullRequestFile[] = [{ filename: 'pay.py', status: 'modified', additions: 1, deletions: 1, changes: 2, patch },
      { filename: 'logo.png', status: 'added', additions: 0, deletions: 0, changes: 0, patch: null }];
    const findings: Finding[] = [
      { file: 'pay.py', line: 2, severity: 'high', title: 'Truncates cents', explanation: 'int() drops decimals.' },
      { file: 'pay.py', line: 90, severity: 'low', title: 'Elsewhere', explanation: 'Outside the hunk.' },
    ];
    render(<DiffView files={files} findings={findings} />);
    const note = screen.getByText('Truncates cents').closest('tr');
    expect(note?.previousElementSibling?.textContent).toContain('return charge(int(amount))');
    expect(screen.getByText('Elsewhere').closest('tr')).toBeNull();
    expect(screen.getByText(/Diff not available/)).toBeTruthy();
  });
});
