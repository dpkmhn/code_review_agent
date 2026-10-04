"""Execute fixed Python verification commands in disposable Linux containers."""

import asyncio
import io
import json
import re
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath



OUTPUT_LIMIT = 12_000
EXECUTION_TIMEOUT = 60
ARCHIVE_LIMIT = 100 * 1024 * 1024


class SandboxCleanupError(RuntimeError):
    pass


def relative_path(value: str) -> str:
    if (not value or value.startswith(("/", "\\", "-")) or "\\" in value
            or ":" in value or "\x00" in value
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("Use a relative path without options or traversal.")
    return value


def extract_snapshot(archive: bytes, destination: Path) -> None:
    """Validate the entire ZIP before writing; never materialize links or devices."""
    with zipfile.ZipFile(io.BytesIO(archive)) as snapshot:
        entries = snapshot.infolist()
        if len(entries) > 10_000 or sum(item.file_size for item in entries) > ARCHIVE_LIMIT:
            raise ValueError("Repository snapshot exceeds extraction limits.")
        roots = set()
        files = []
        seen = set()
        for item in entries:
            name = relative_path(item.filename.rstrip("/"))
            parts = PurePosixPath(name).parts
            roots.add(parts[0])
            mode = item.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("Repository snapshot contains a link or special file.")
            if item.is_dir():
                continue
            if len(parts) < 2:
                raise ValueError("Expected a GitHub archive root directory.")
            path = Path(*parts[1:])
            # Windows aliases and reserved names must not escape or overwrite paths.
            for part in parts:
                if (part.endswith((".", " ")) or any(c in part for c in '<>"|?*')
                        or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
                    raise ValueError("Unsupported snapshot filename.")
            key = path.as_posix().casefold()
            if key in seen:
                raise ValueError("Duplicate snapshot path.")
            seen.add(key)
            files.append((item, path))
        if len(roots) != 1:
            raise ValueError("Expected exactly one archive root.")
        for item, path in files:
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(snapshot.read(item))
            target.chmod(0o644)


@dataclass
class ExecutionResult:
    output: str
    exit_code: int | None
    timed_out: bool = False
    completed: bool = True


async def docker_command(*arguments: str, timeout: int = 20) -> ExecutionResult:
    process = await asyncio.create_subprocess_exec(
        "docker", *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    async def drain(stream):
        retained = bytearray()
        truncated = False
        while chunk := await stream.read(4096):
            available = OUTPUT_LIMIT - len(retained)
            retained.extend(chunk[:available])
            truncated |= len(chunk) > available
        return retained.decode("utf-8", errors="replace") + (
            "\n[TRUNCATED]" if truncated else "")

    readers = [asyncio.create_task(drain(stream)) for stream in (process.stdout, process.stderr)]
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        timed_out = True
        process.kill()
        await process.wait()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        raise
    outputs = await asyncio.gather(*readers)
    return ExecutionResult("\n".join(outputs), process.returncode, timed_out, not timed_out)


class ContainerRunner:
    def __init__(self, image, command=docker_command):
        self.image = image
        self.command = command

    async def _execute_container(self, root: Path, source: str, path: str) -> ExecutionResult:
        name = "review-sandbox-" + uuid.uuid4().hex
        command = (["-m", "pytest", "-q", "-p", "no:cacheprovider", "--", f"/repo/{path}"]
                   if source == "run_test" else
                   ["-m", "ruff", "check", "--isolated", "--no-cache", "--", f"/repo/{path}"])
        try:
            created = await self.command(
                "create", "--name", name, "--pull", "never", "--network", "none",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "--cpus", "1", "--memory", "512m",
                "--memory-swap", "512m", "--pids-limit", "64", "--log-driver", "none",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
                "--workdir", "/repo", "--env", "HOME=/tmp",
                "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                "--entrypoint", "python", self.image, *command,
            )
            if created.exit_code != 0 or not created.completed:
                return ExecutionResult("Sandbox container creation failed; execution did not run.",
                                       None, completed=False)
            copied = await self.command("cp", str(root) + "/.", name + ":/repo")
            if copied.exit_code != 0 or not copied.completed:
                return ExecutionResult("Snapshot transfer failed; execution did not run.", None, completed=False)
            result = await self.command("start", "--attach", name, timeout=EXECUTION_TIMEOUT)
            if result.timed_out:
                return result
            inspected = await self.command("inspect", "--format", "{{json .State}}", name)
            try:
                state = json.loads(inspected.output.strip())
                if (inspected.exit_code != 0 or not inspected.completed or state.get("Running")
                        or state.get("Status") != "exited" or state.get("OOMKilled") or state.get("Error")):
                    raise ValueError("Container execution incomplete.")
                result.exit_code = state["ExitCode"]
                result.completed = result.exit_code in (0, 1)
            except (ValueError, KeyError, TypeError):
                result.completed = False
            return result
        finally:
            # The name is generated before creation so partial failures are removable.
            cleanup_task = asyncio.create_task(self.command("rm", "--force", "--volumes", name))
            try:
                cleanup = await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise
            absent = "No such container" in cleanup.output
            if not cleanup.completed or (cleanup.exit_code != 0 and not absent):
                raise SandboxCleanupError("Sandbox cleanup failed.")
