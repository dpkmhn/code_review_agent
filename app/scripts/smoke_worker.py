"""Check live worker isolation using synthetic code and no upstream credentials."""

import asyncio
import io
import os
import zipfile

import httpx


FIXTURE = '''import os
import socket
import pytest

def test_isolation():
    assert os.getuid() == 65534
    assert not any(k.lower() in ("github_token", "openai_api_key", "sandbox_worker_token") for k in os.environ)
    assert not os.path.exists("/var/run/docker.sock")
    with pytest.raises(OSError):
        open("/repo/forbidden", "w")
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 443), timeout=1)

def test_failure():
    assert False, "expected fixture failure"
'''


async def main():
    url = os.environ["SANDBOX_WORKER_URL"].rstrip("/")
    token = os.environ["SANDBOX_WORKER_TOKEN"]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("root/tests/test_isolation.py", FIXTURE)
    async with httpx.AsyncClient(timeout=180, trust_env=False, follow_redirects=False) as client:
        response = await client.post(url + "/execute", content=archive.getvalue(),
            params={"source": "run_test", "path": "tests/test_isolation.py"},
            headers={"Authorization": "Bearer " + token})
        response.raise_for_status()
        result = response.json()
        if not (result["completed"] and result["exit_code"] == 1 and "1 failed, 1 passed" in result["output"]):
            raise SystemExit("Worker isolation smoke test failed: " + result["output"])
    print("Worker isolation smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
