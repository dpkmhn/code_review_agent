import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.local_app import mount_frontend


class LocalUITests(unittest.IsolatedAsyncioTestCase):
    async def test_built_ui_and_api_share_origin_without_exposing_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            (dist / "assets").mkdir(parents=True)
            (dist / "index.html").write_text("<html>Review workspace</html>", encoding="utf-8")
            (dist / "assets" / "app.js").write_text("console.log('ui')", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            app = FastAPI()

            @app.get("/api/v1/example")
            def example():
                return {"api": True}

            mount_frontend(app, dist)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
                self.assertIn("Review workspace", (await client.get("/")).text)
                self.assertEqual((await client.get("/assets/app.js")).status_code, 200)
                self.assertEqual((await client.get("/api/v1/example")).json(), {"api": True})
                for path in ("/api/v1/missing", "/assets/missing.js", "/.env", "/../.env", "/%2e%2e/.env"):
                    self.assertEqual((await client.get(path)).status_code, 404)

    async def test_missing_build_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "npm run build"):
                mount_frontend(FastAPI(), Path(temporary))


if __name__ == "__main__":
    unittest.main()
