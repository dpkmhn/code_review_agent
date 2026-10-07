"""ToolNode adapters: repository identity and revisions come from graph state."""

import json
import re
from typing import Annotated, Literal

import httpx
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState, ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError

from app.github_client import GitHubClient
from app.schemas.hypothesis import VerificationEvidence
from app.services.sandbox_service import SandboxService
from app.tools.executor import execute_repository_tool
from app.tools.tree import tree_evidence


MAX_EVIDENCE_CHARS = 12_000


def bounded_content(content: str) -> str:
    if len(content) <= MAX_EVIDENCE_CHARS:
        return content
    return content[:MAX_EVIDENCE_CHARS] + "\n[TRUNCATED: remaining content was not inspected]"


def build_verification_tools(github_client: GitHubClient):
    sandbox = SandboxService(github_client)

    async def execute_sandbox(source, path, revision, state):
        pr = state["pull_request"]
        ref = pr.head_sha if revision == "head" else pr.base_sha
        try:
            result = await sandbox.execute(pr, source, path, revision)
            evidence = VerificationEvidence(
                source=source, path=path, ref=ref, content=bounded_content(result.output),
                succeeded=result.completed, exit_code=result.exit_code, timed_out=result.timed_out,
            )
        except httpx.HTTPError:
            evidence = VerificationEvidence(source=source, path=path, ref=ref,
                content="Snapshot download failed; execution did not run.", succeeded=False)
        return evidence.model_dump_json(), evidence
    async def execute(name, arguments, state, revision="head"):
        if name in {"search_code", "find_references"}:
            query = arguments.get("query", arguments.get("symbol", ""))
            if not query.strip() or re.search(r"\b(?:repo|org|user)\s*:", query, re.IGNORECASE):
                raise ValueError("Use nonempty search text without repository scope qualifiers.")
        pr = state["pull_request"]
        ref = pr.head_sha if revision == "head" else pr.base_sha
        path = arguments.get("path", "")
        try:
            result = await execute_repository_tool(
                github_client=github_client, tool_name=name, arguments=arguments,
                owner=pr.owner, repository=pr.repository, ref=ref,
            )
            if name == "read_file":
                evidence = VerificationEvidence(
                    source=name, path=path, ref=ref,
                    content=bounded_content(result), succeeded=True,
                )
                return evidence.model_dump_json(), evidence
            if name == "list_tree":
                evidence = tree_evidence(result, ref, path)
                return evidence.content, evidence
            # Search hits locate candidates; they are not inspected code at the PR SHA.
            return json.dumps({"candidate_paths_default_branch": result,
                               "note": "Read candidate files at head or base before citing code."}), None
        except (httpx.HTTPError, UnicodeDecodeError, KeyError, ValueError):
            evidence = VerificationEvidence(
                source=name, path=path or ".", ref=ref,
                content="File inspection failed; no contents inspected.", succeeded=False,
            ) if name in {"read_file", "list_tree"} else None
            return json.dumps({"error": "Repository tool failed; no contents inspected."}), evidence

    @tool(response_format="content_and_artifact")
    async def read_file(path: str, revision: Literal["head", "base"],
                        state: Annotated[dict, InjectedState]):
        """Inspect a repository-relative file at the PR head or base revision."""
        if not path or path.startswith(("/", "\\")) or ":" in path or ".." in path.replace("\\", "/").split("/"):
            raise ValueError("Use a repository-relative file path without traversal.")
        return await execute("read_file", {"path": path}, state, revision)

    @tool(response_format="content_and_artifact")
    async def list_tree(revision: Literal["head", "base"], state: Annotated[dict, InjectedState],
                        directory: str = ""):
        """List repository file paths at the PR head or base, optionally under one directory.
        A successful listing that lacks a path is valid evidence the path does not exist."""
        if directory and (directory.startswith(("/", "\\")) or ":" in directory
                          or ".." in directory.replace("\\", "/").split("/")):
            raise ValueError("Use a repository-relative directory without traversal.")
        return await execute("list_tree", {"path": directory}, state, revision)

    @tool(response_format="content_and_artifact")
    async def search_code(query: str, state: Annotated[dict, InjectedState]):
        """Find candidate file paths on the repository default branch."""
        return await execute("search_code", {"query": query}, state)

    @tool(response_format="content_and_artifact")
    async def find_references(symbol: str, state: Annotated[dict, InjectedState]):
        """Find candidate paths referencing a symbol on the default branch."""
        return await execute("find_references", {"symbol": symbol}, state)

    @tool(response_format="content_and_artifact")
    async def run_test(path: str, revision: Literal["head", "base"],
                       state: Annotated[dict, InjectedState]):
        """Run one Python test file with pytest in an offline disposable sandbox."""
        return await execute_sandbox("run_test", path, revision, state)

    @tool(response_format="content_and_artifact")
    async def run_static_analysis(path: str, revision: Literal["head", "base"],
                                  state: Annotated[dict, InjectedState]):
        """Run Ruff on one file in an offline disposable sandbox with isolated config."""
        return await execute_sandbox("run_static_analysis", path, revision, state)

    return [read_file, list_tree, search_code, find_references, run_test, run_static_analysis]


def build_tool_node(tools):
    # Validation failures and unknown tools become messages the reviewer can inspect.
    return ToolNode(tools, handle_tool_errors=(ToolInvocationError, ValueError, TypeError))
