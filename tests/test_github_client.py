import base64
import json
import unittest

import httpx

from app.github_client import GitHubClient


class ReadFileTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        self.requests = []

        def record(request):
            self.requests.append(request)
            return handler(request)
        return GitHubClient(transport=httpx.MockTransport(record))

    async def read(self, client, path="pkg/helper.py"):
        return await client.read_file(owner="owner", repository="repo", path=path, ref="head")

    async def test_base64_file_is_decoded(self):
        payload = {"type": "file", "encoding": "base64",
                   "content": base64.b64encode(b"return value\n").decode()}
        client = self.client(lambda request: httpx.Response(200, json=payload))
        self.assertEqual(await self.read(client), "return value\n")
        self.assertEqual(self.requests[0].url.params["ref"], "head")

    async def test_large_file_falls_back_to_raw_content(self):
        def handle(request):
            if request.headers["accept"] == "application/vnd.github.raw+json":
                return httpx.Response(200, content=b"x = 1\n" * 10)
            return httpx.Response(200, json={"type": "file", "encoding": "none", "content": ""})
        client = self.client(handle)
        self.assertEqual(await self.read(client), "x = 1\n" * 10)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1].url.params["ref"], "head")

    async def test_raw_content_over_limit_is_rejected(self):
        def handle(request):
            if request.headers["accept"] == "application/vnd.github.raw+json":
                return httpx.Response(200, content=b"a" * (GitHubClient.RAW_FILE_LIMIT + 1))
            return httpx.Response(200, json={"type": "file", "encoding": "none", "content": ""})
        with self.assertRaises(ValueError):
            await self.read(self.client(handle))

    async def test_directories_and_symlinks_are_not_files(self):
        for payload in ([{"type": "file", "path": "pkg/a.py"}], {"type": "symlink", "target": "a.py"},
                        {"type": "submodule"}):
            client = self.client(lambda request, body=payload: httpx.Response(
                200, content=json.dumps(body).encode()))
            with self.assertRaises(ValueError):
                await self.read(client)

    async def test_path_characters_are_encoded_without_truncation(self):
        payload = {"type": "file", "encoding": "base64", "content": base64.b64encode(b"ok").decode()}
        client = self.client(lambda request: httpx.Response(200, json=payload))
        await self.read(client, "docs/a b#c?.py")
        self.assertEqual(self.requests[0].url.raw_path.split(b"?")[0],
                         b"/repos/owner/repo/contents/docs/a%20b%23c%3F.py")


if __name__ == "__main__":
    unittest.main()
