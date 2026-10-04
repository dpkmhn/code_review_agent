import io
import asyncio
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.graph.state import ReviewState
from app.services.sandbox_service import (
    ExecutionResult, SandboxService, extract_snapshot, relative_path,
    docker_command, OUTPUT_LIMIT,
)
from app.tools.verification_tools import build_tool_node, build_verification_tools


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name, content in entries:
            zipped.writestr(name, content)
    return stream.getvalue()


class SnapshotTests(unittest.TestCase):
    def test_valid_snapshot_strips_github_root(self):
        with tempfile.TemporaryDirectory() as folder:
            extract_snapshot(archive([("repo-sha/tests/test_a.py", "assert True")]), Path(folder))
            self.assertEqual((Path(folder) / "tests/test_a.py").read_text(), "assert True")

    def test_unsafe_paths(self):
        for path in ("../secret", "/secret", "C:/secret", "-k", "a\\b", "a//b", "a/./b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                relative_path(path)

    def test_invalid_archives_write_nothing(self):
        for entries in ([('root/../secret', 'bad')], [('root/CON', 'bad')],
                        [('root/a', 'a'), ('other/b', 'b')],
                        [('root/a.py', 'a'), ('root/A.py', 'b')]):
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    extract_snapshot(archive(entries), Path(folder))
                self.assertEqual(list(Path(folder).rglob("*")), [])

    def test_symlink_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            link = zipfile.ZipInfo("root/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipped.writestr(link, "/etc/passwd")
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            extract_snapshot(stream.getvalue(), Path(folder))

    def test_extraction_size_limit(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "app.services.sandbox_runtime.ARCHIVE_LIMIT", 1
        ), self.assertRaises(ValueError):
            extract_snapshot(archive([("root/a", "too large")]), Path(folder))


class SandboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.github = SimpleNamespace(download_archive=AsyncMock(
            return_value=archive([("root/tests/test_a.py", "assert False")])
        ))
        self.pr = SimpleNamespace(owner="owner", repository="repo", head_sha="head", base_sha="base")
        self.service = SandboxService(self.github)

    async def test_disabled_does_not_fetch_or_execute(self):
        with patch("app.services.sandbox_service.settings.sandbox_enabled", False), patch(
            "app.services.sandbox_service.docker_command", AsyncMock()
        ) as docker:
            result = await self.service.execute(self.pr, "run_test", "tests/test_a.py", "head")
        self.assertFalse(result.completed)
        self.github.download_archive.assert_not_awaited()
        docker.assert_not_awaited()

    async def execute(self, responses, source="run_test", revision="base"):
        with patch("app.services.sandbox_service.settings.sandbox_enabled", True), patch(
            "app.services.sandbox_service.docker_command", AsyncMock(side_effect=responses)
        ) as docker:
            result = await self.service.execute(self.pr, source, "tests/test_a.py", revision)
        return result, docker

    async def test_security_flags_fixed_command_revision_and_cleanup(self):
        result, docker = await self.execute([
            ExecutionResult("id", 0), ExecutionResult("", 0), ExecutionResult("FAILED", 0),
            ExecutionResult(json.dumps({"Status": "exited", "ExitCode": 1}), 0),
            ExecutionResult("removed", 0),
        ])
        self.github.download_archive.assert_awaited_once_with("owner", "repo", "base")
        args = docker.await_args_list[0].args
        for option, value in (("--network", "none"), ("--cap-drop", "ALL"),
                              ("--user", "65534:65534"), ("--memory", "512m"),
                              ("--pids-limit", "64"), ("--pull", "never")):
            self.assertEqual(args[args.index(option) + 1], value)
        self.assertIn("--read-only", args)
        self.assertNotIn("--volume", args)
        self.assertIn("pytest", args)
        self.assertEqual(args[-2:], ("--", "/repo/tests/test_a.py"))
        self.assertTrue(result.completed)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(docker.await_args_list[-1].args[:3], ("rm", "--force", "--volumes"))

    async def test_timeout_cleans_up_and_is_not_evidence(self):
        result, docker = await self.execute([
            ExecutionResult("id", 0), ExecutionResult("", 0),
            ExecutionResult("partial", None, timed_out=True, completed=False), ExecutionResult("", 0),
        ])
        self.assertFalse(result.completed)
        self.assertTrue(result.timed_out)
        self.assertEqual(docker.await_args_list[-1].args[0], "rm")

    async def test_creation_failure_cleans_up(self):
        result, docker = await self.execute([ExecutionResult("missing image", 1), ExecutionResult("", 0)])
        self.assertFalse(result.completed)
        self.assertEqual(docker.await_count, 2)

    async def test_copy_failure_cleans_up_without_starting(self):
        result, docker = await self.execute([
            ExecutionResult("id", 0), ExecutionResult("copy failed", 1), ExecutionResult("", 0),
        ])
        self.assertFalse(result.completed)
        self.assertEqual([call.args[0] for call in docker.await_args_list], ["create", "cp", "rm"])

    async def test_cleanup_failure_is_visible_and_invalidates_evidence(self):
        result, _ = await self.execute([ExecutionResult("", 1), ExecutionResult("failed", 1)])
        self.assertFalse(result.completed)
        self.assertIn("Operator cleanup is required", result.output)

    async def test_cancellation_still_removes_container(self):
        async def command(*args, **kwargs):
            if args[0] == "start":
                raise asyncio.CancelledError()
            return ExecutionResult("", 0)
        with patch("app.services.sandbox_service.settings.sandbox_enabled", True), patch(
            "app.services.sandbox_service.docker_command", AsyncMock(side_effect=command)
        ) as docker, self.assertRaises(asyncio.CancelledError):
            await self.service.execute(self.pr, "run_test", "tests/test_a.py", "head")
        self.assertEqual(docker.await_args_list[-1].args[0], "rm")

    async def test_oom_execution_cannot_confirm(self):
        result, _ = await self.execute([
            ExecutionResult("id", 0), ExecutionResult("", 0), ExecutionResult("", 0),
            ExecutionResult(json.dumps({"Status": "exited", "ExitCode": 1, "OOMKilled": True}), 0),
            ExecutionResult("", 0),
        ])
        self.assertFalse(result.completed)

    async def test_collection_error_cannot_confirm(self):
        result, _ = await self.execute([
            ExecutionResult("id", 0), ExecutionResult("", 0), ExecutionResult("collection error", 0),
            ExecutionResult(json.dumps({"Status": "exited", "ExitCode": 2}), 0), ExecutionResult("", 0),
        ])
        self.assertFalse(result.completed)

    async def test_static_analysis_uses_isolated_config(self):
        _, docker = await self.execute([ExecutionResult("", 1), ExecutionResult("", 0)],
                                       source="run_static_analysis")
        args = docker.await_args_list[0].args
        self.assertIn("ruff", args)
        self.assertIn("--isolated", args)

    async def test_tool_node_retains_failed_test_execution_artifact(self):
        node = build_tool_node(build_verification_tools(self.github))
        builder = StateGraph(ReviewState)
        builder.add_node("tools", node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        with patch.object(SandboxService, "execute", AsyncMock(
            return_value=ExecutionResult("assertion failed", 1)
        )):
            result = await builder.compile().ainvoke({"pull_request": self.pr, "messages": [
                AIMessage(content="", tool_calls=[{"id": "test", "name": "run_test",
                    "args": {"path": "tests/test_a.py", "revision": "head"}}])
            ]})
        message = result["messages"][-1]
        self.assertIsInstance(message, ToolMessage)
        self.assertTrue(message.artifact.succeeded)
        self.assertEqual(message.artifact.exit_code, 1)
        self.assertEqual(message.artifact.ref, "head")

    async def test_process_output_is_drained_and_bounded(self):
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_data(b"x" * (OUTPUT_LIMIT * 3))
        stdout.feed_eof()
        stderr.feed_eof()
        process = SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0, wait=AsyncMock())
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as launch:
            result = await docker_command("start", "--attach", "test")
        self.assertIn("[TRUNCATED]", result.output)
        self.assertLess(len(result.output), OUTPUT_LIMIT + 30)
        self.assertEqual(launch.await_args.args, ("docker", "start", "--attach", "test"))


@unittest.skipUnless(os.environ.get("SANDBOX_SMOKE_TEST") == "1",
                     "Set SANDBOX_SMOKE_TEST=1 with Docker and the trusted sandbox image available.")
class LiveSandboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_container_isolation_and_assertion_failure(self):
        fixture = '''import os
import socket
import pytest

def test_isolation():
    assert os.getuid() == 65534
    assert not any(key.lower() in ("github_token", "openai_api_key") for key in os.environ)
    assert not os.path.exists("/var/run/docker.sock")
    with pytest.raises(OSError):
        open("/repo/forbidden", "w")
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 443), timeout=1)

def test_failure():
    assert False, "expected fixture failure"
'''
        github = SimpleNamespace(download_archive=AsyncMock(
            return_value=archive([("root/tests/test_isolation.py", fixture)])
        ))
        pr = SimpleNamespace(owner="fixture", repository="fixture", head_sha="head", base_sha="base")
        with patch("app.services.sandbox_service.settings.sandbox_enabled", True):
            result = await SandboxService(github).execute(pr, "run_test", "tests/test_isolation.py", "head")
        self.assertTrue(result.completed, result.output)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("1 failed, 1 passed", result.output)
