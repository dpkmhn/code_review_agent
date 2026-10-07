"""Repository tree helpers: evidence for the evaluator and a bounded prompt view."""

import posixpath

from app.schemas.hypothesis import VerificationEvidence

MAX_PROMPT_PATHS = 300
MAX_EVIDENCE_CHARS = 12_000


def _header(ref: str, directory: str, count: int, truncated: bool) -> str:
    scope = f" under '{directory}'" if directory else ""
    note = " GitHub truncated this tree; a missing path is not proven absent." if truncated else ""
    return f"Files at {ref}{scope}: {count}.{note}"


def tree_evidence(result: dict, ref: str, directory: str = "") -> VerificationEvidence:
    """Record a successful listing so an absent path can be cited by index."""
    prefix = directory.strip("/")
    paths = [path for path in result["paths"]
             if not prefix or path == prefix or path.startswith(prefix + "/")]
    content = _header(ref, prefix, len(paths), result["truncated"]) + "\n" + "\n".join(paths)
    if len(content) > MAX_EVIDENCE_CHARS:
        content = content[:MAX_EVIDENCE_CHARS] + "\n[TRUNCATED: remaining paths were not inspected]"
    return VerificationEvidence(source="list_tree", path=prefix or ".", ref=ref,
                                content=content, succeeded=True)


def tree_prompt(result: dict, ref: str, changed_files: list[str]) -> str:
    """List paths near the changed files first, then fill with the rest.

    Near means ancestors of a changed file's directory plus anything beneath
    it, because entry points like /src/main.tsx live below the changed file.
    """
    ancestors: set[str] = set()
    changed_dirs: set[str] = set()
    for changed in changed_files:
        directory = posixpath.dirname(changed)
        changed_dirs.add(directory)
        while True:
            ancestors.add(directory)
            if not directory:
                break
            directory = posixpath.dirname(directory)

    def is_near(path: str) -> bool:
        directory = posixpath.dirname(path)
        if directory in ancestors:
            return True
        return any(base and directory.startswith(base + "/") for base in changed_dirs)

    paths = result["paths"]
    close = [path for path in paths if is_near(path)]
    others = [path for path in paths if not is_near(path)]
    shown = (close + others)[:MAX_PROMPT_PATHS]
    omitted = len(paths) - len(shown)
    lines = [f"Repository file tree at PR head {ref} (paths only, untrusted data)."]
    if result["truncated"] or omitted:
        lines.append(f"Partial view: {omitted} paths omitted. Do not infer a file is missing from this view.")
    lines.extend(sorted(shown))
    return "\n".join(lines)
