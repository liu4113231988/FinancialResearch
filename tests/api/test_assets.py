import unittest

from fastapi.testclient import TestClient

from market_research.api.app import app


class IconAssetTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _assert_asset(self, path, media_type, magic):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        self.assertEqual(response.headers["content-type"], media_type, path)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=86400", path)
        body = response.content
        self.assertGreater(len(body), 0, path)
        self.assertTrue(body.startswith(magic), path)

    def test_root_icon_aliases(self):
        self._assert_asset("/favicon.ico", "image/x-icon", b"\x00\x00\x01\x00")
        self._assert_asset("/favicon.svg", "image/svg+xml", b"<svg")
        self._assert_asset("/apple-touch-icon.png", "image/png",
                           b"\x89PNG\r\n\x1a\n")
        response = self.client.get("/site.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"],
                         "application/manifest+json")
        self.assertEqual(response.json()["short_name"], "Market Research")

    def test_static_icon_routes_have_same_types_and_cache(self):
        for path, media, magic in (
            ("/static/favicon.ico", "image/x-icon", b"\x00\x00\x01\x00"),
            ("/static/favicon.svg", "image/svg+xml", b"<svg"),
            ("/static/apple-touch-icon.png", "image/png", b"\x89PNG\r\n\x1a\n"),
            ("/static/icon-192.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        ):
            self._assert_asset(path, media, magic)

    def test_index_declares_svg_first_and_manifest(self):
        html = self.client.get("/").text
        svg = html.find('rel="icon" href="/static/favicon.svg"')
        ico = html.find('rel="icon" href="/static/favicon.ico"')
        self.assertGreater(svg, -1)
        self.assertGreater(ico, svg)
        self.assertIn('rel="apple-touch-icon" href="/static/apple-touch-icon.png"',
                      html)
        self.assertIn('rel="manifest" href="/static/site.webmanifest"', html)
        self.assertIn('sizes="16x16 32x32 48x48"', html)

    def test_manifest_icons_are_real_files(self):
        manifest = self.client.get("/site.webmanifest").json()
        self.assertEqual(manifest["theme_color"], "#0d1117")
        sources = {icon["src"] for icon in manifest["icons"]}
        self.assertIn("/static/icon-192.png", sources)
        self.assertIn("/static/icon-512.png", sources)
        for src in sources:
            self.assertEqual(self.client.get(src).status_code, 200, src)


if __name__ == "__main__":
    unittest.main()
