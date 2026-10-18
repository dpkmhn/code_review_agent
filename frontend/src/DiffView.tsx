import { Fragment } from 'react';
import type { Finding, PullRequestFile } from './types';

export type DiffRow =
  | { type: 'hunk' | 'meta'; text: string; oldLine?: undefined; newLine?: undefined }
  | { type: 'add'; text: string; newLine: number; oldLine?: undefined }
  | { type: 'del'; text: string; oldLine: number; newLine?: undefined }
  | { type: 'ctx'; text: string; oldLine: number; newLine: number };

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/;

// Turn a GitHub unified patch into rows with old/new line numbers.
export function parsePatch(patch: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldLine = 0;
  let newLine = 0;
  for (const text of patch.split('\n')) {
    const hunk = text.match(HUNK);
    if (hunk) {
      oldLine = Number(hunk[1]); newLine = Number(hunk[2]);
      rows.push({ type: 'hunk', text });
    } else if (text.startsWith('+')) {
      rows.push({ type: 'add', text: text.slice(1), newLine: newLine++ });
    } else if (text.startsWith('-')) {
      rows.push({ type: 'del', text: text.slice(1), oldLine: oldLine++ });
    } else if (text.startsWith('\\')) {
      rows.push({ type: 'meta', text });
    } else {
      rows.push({ type: 'ctx', text: text.slice(1), oldLine: oldLine++, newLine: newLine++ });
    }
  }
  return rows;
}

function FileDiff({ file, findings, open }: { file: PullRequestFile; findings: Finding[]; open: boolean }) {
  const rows = file.patch ? parsePatch(file.patch) : [];
  // Findings point at head-revision lines; anything not visible in the patch is listed above the diff.
  const shownLines = new Set(rows.filter((row) => row.newLine != null).map((row) => row.newLine));
  const unplaced = findings.filter((finding) => finding.line == null || !shownLines.has(finding.line));
  return <details className="diff-file" open={open}>
    <summary>
      <code>{file.filename}</code>
      <span className="diff-stats">{findings.length > 0 && <span className="diff-flag">{findings.length} finding{findings.length === 1 ? '' : 's'}</span>}
        <span className={`diff-status ${file.status}`}>{file.status}</span>
        <b className="additions">+{file.additions}</b> <b className="deletions">−{file.deletions}</b></span>
    </summary>
    {unplaced.map((finding, index) => <Annotation key={`u${index}`} finding={finding} />)}
    {file.patch ? <div className="diff-scroll"><table className="diff-table"><tbody>
      {rows.map((row, index) => <Fragment key={index}>
        <tr className={`diff-row ${row.type}`}>
          {row.type === 'hunk' || row.type === 'meta'
            ? <td colSpan={3} className="diff-hunk">{row.text}</td>
            : <><td className="ln">{row.oldLine ?? ''}</td><td className="ln">{row.newLine ?? ''}</td>
              <td className="code"><span className="sign" aria-hidden="true">{row.type === 'add' ? '+' : row.type === 'del' ? '−' : ' '}</span>{row.text}</td></>}
        </tr>
        {row.newLine != null && findings.filter((finding) => finding.line === row.newLine)
          .map((finding, n) => <tr key={`f${n}`} className="diff-note-row"><td colSpan={3}><Annotation finding={finding} /></td></tr>)}
      </Fragment>)}
    </tbody></table></div>
      : <p className="muted diff-missing">Diff not available — the file is binary or too large for the GitHub API.</p>}
  </details>;
}

function Annotation({ finding }: { finding: Finding }) {
  return <div className={`diff-note sev-${finding.severity}`}>
    <span className="finding-tags"><span className={`severity ${finding.severity}`}>{finding.severity}</span>{finding.category && <span className="category">{finding.category}</span>}</span>
    <div><strong>{finding.title}</strong>{finding.line == null && <small> · no line given</small>}<p>{finding.explanation}</p></div>
  </div>;
}

export default function DiffView({ files, findings }: { files: PullRequestFile[]; findings: Finding[] }) {
  const byFile = (name: string) => findings.filter((finding) => finding.file === name);
  const additions = files.reduce((sum, file) => sum + file.additions, 0);
  const deletions = files.reduce((sum, file) => sum + file.deletions, 0);
  return <section className="diff-view" aria-labelledby="diff-heading">
    <h3 id="diff-heading">Diff <span className="count">{files.length} files</span>
      <span className="diff-total"><b className="additions">+{additions}</b> <b className="deletions">−{deletions}</b></span></h3>
    {files.length === 0 ? <p className="muted">No changed files.</p>
      : files.map((file, index) => <FileDiff key={file.filename} file={file} findings={byFile(file.filename)}
        open={byFile(file.filename).length > 0 || (index < 3 && files.length <= 10)} />)}
  </section>;
}
