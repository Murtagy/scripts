import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import db
from plan_content import image_notices, image_urls, telegram_chunks
from plan_monitor import ImageDownloadError, PlanStore, download_image


class PlanImageContentTests(unittest.TestCase):
    def test_markdown_and_html_images_preserve_order_and_decode_entities(self) -> None:
        text = '''![](https://img.tsgames.ru/a.png "title")
![map](<https://img.tsgames.ru/b.png>)
<img alt="slots" src="https://tsgames.ru/c.jpg?a=1&amp;b=2">
![](https://img.tsgames.ru/a.png)
[not an image](https://img.tsgames.ru/link.png)'''
        self.assertEqual(image_urls(text), (
            "https://img.tsgames.ru/a.png", "https://img.tsgames.ru/b.png", "https://tsgames.ru/c.jpg?a=1&b=2",
        ))

    def test_image_caption_includes_nested_section_path_and_original_url(self) -> None:
        notice, = image_notices("@@@ Логистика\n@@@ DER\n![](https://img.tsgames.ru/map.png)\n@@@\n@@@")
        self.assertEqual(notice.text, "План\nЛогистика-DER\nhttps://img.tsgames.ru/map.png")
        self.assertEqual(notice.image_url, "https://img.tsgames.ru/map.png")

    def test_photo_captions_respect_utf16_limit_without_losing_overflow(self) -> None:
        text = "🪖" * 1500
        chunks = telegram_chunks(text, limit=1024)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 1024 for chunk in chunks))
        with self.assertRaises(ValueError):
            telegram_chunks(text, limit=1)

    def test_legacy_delivery_schema_migration_keeps_sent_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", str(Path(directory) / "legacy.sqlite3")):
            with db.get_conn() as conn:
                conn.execute("""CREATE TABLE plan_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, reference INTEGER NOT NULL,
                    kind TEXT NOT NULL, identity TEXT NOT NULL, text TEXT NOT NULL,
                    sent_parts INTEGER NOT NULL DEFAULT 0, UNIQUE(chat_id,reference,kind,identity)
                )""")
                conn.execute("INSERT INTO plan_deliveries (chat_id,reference,kind,identity,text,sent_parts) VALUES ('chat',6042,'initial','initial','Old plan',1)")
                conn.commit()
            db.init_db()
            db.init_db()
            self.assertEqual(PlanStore().pending("chat", 6042), ())
            with db.get_conn() as conn:
                self.assertEqual(conn.execute("SELECT text,sent_parts,image_url FROM plan_deliveries").fetchone()[:], ("Old plan", 1, None))


class ImageDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []
        self.response = httpx.Response(200, content=b"image", headers={"content-type": "image/png"})

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.response

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(self.client.aclose)
        factory = patch("plan_monitor.httpx.AsyncClient", return_value=self.client)
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    async def test_downloads_bytes_without_headquarters_credentials(self) -> None:
        content = await download_image("https://img.tsgames.ru/api/files/test.png")
        self.assertEqual(content, b"image")
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("access-token", str(self.requests[0].url))
        self.assertNotIn("authorization", self.requests[0].headers)

    async def test_current_plan_author_cloud_bucket_is_downloaded_as_bytes(self) -> None:
        self.response = httpx.Response(200, content=b"webp", headers={"content-type": "image/webp"})
        content = await download_image("https://nitro-n.s3-website.cloud.ru/plans/test/map.webp")
        self.assertEqual(content, b"webp")
        self.assertEqual(self.requests[0].url.host, "nitro-n.s3-website.cloud.ru")
        self.assertNotIn("authorization", self.requests[0].headers)

    async def test_untrusted_origins_and_malformed_urls_are_refused_before_request(self) -> None:
        urls = (
            "http://img.tsgames.ru/image.png", "https://127.0.0.1/image.png",
            "https://tsgames.ru.evil.test/image.png", "https://img.tsgames.ru:444/image.png",
            "https://user:secret@img.tsgames.ru/image.png", "https://[broken/image.png",
            "https://nitro-n.s3-website.cloud.ru.evil.test/image.png",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ImageDownloadError):
                await download_image(url)
        self.assertEqual(self.requests, [])

    async def test_redirect_is_not_followed(self) -> None:
        self.response = httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        with self.assertRaises(ImageDownloadError):
            await download_image("https://img.tsgames.ru/image.png")
        self.assertEqual(len(self.requests), 1)

    async def test_html_response_is_rejected(self) -> None:
        self.response = httpx.Response(200, content=b"login", headers={"content-type": "text/html"})
        with self.assertRaises(ImageDownloadError):
            await download_image("https://img.tsgames.ru/image.png")

    async def test_empty_image_is_rejected(self) -> None:
        self.response = httpx.Response(200, content=b"", headers={"content-type": "image/png"})
        with self.assertRaises(ImageDownloadError):
            await download_image("https://img.tsgames.ru/image.png")

    async def test_oversized_image_is_rejected(self) -> None:
        self.response = httpx.Response(200, content=b"x" * 10_000_001, headers={"content-type": "image/jpeg"})
        with self.assertRaises(ImageDownloadError):
            await download_image("https://img.tsgames.ru/image.jpg")
