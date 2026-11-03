import json
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

import replays


class ReplayRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        directory = self.contexts.enter_context(TemporaryDirectory())
        self.credential = Path(directory) / "replay-relay-token"
        self.token = "0" * 64
        self.credential.write_text(self.token, encoding="ascii")
        self.contexts.enter_context(patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": directory}))
        self.fetch = self.contexts.enter_context(patch.object(replays.requests, "get"))

    def response(self, payload: dict[str, object], status: int = 200) -> Mock:
        response = Mock(spec=requests.Response)
        response.status_code = status
        response.json.return_value = payload
        return response

    def test_list_and_detail_use_authenticated_domain(self) -> None:
        name = "T2.test&params[a]=other.chernarus"
        replay = [[], [0, [
            [1, 1, "Alpha", "", 1], [1, 2, "Bravo", "", 0],
            [3, 1, "Alpha", "Alpha"], [3, 2, "Bravo", "Bravo"],
        ]], [5, [[4, 5, 1, 2, "rifle"]]]]
        self.fetch.side_effect = [
            self.response({"rows": [{"name": name}], "more": False}),
            self.response({"json": json.dumps(replay)}),
        ]
        found = replays.get_new_replays([])
        self.assertEqual(len(found), 1)
        detail = urlsplit(found[0].url)
        self.assertEqual(f"{detail.scheme}://{detail.netloc}{detail.path}", replays.REPLAY_API_URL)
        self.assertEqual(parse_qs(detail.query)["params[f]"], [name])
        self.assertEqual(parse_qs(detail.query)["a"], ["gl"])
        frags = replays.get_frags(found[0].url, name)
        self.assertEqual(frags, [replays.Frag("Alpha", "Bravo", "0:00:05", False, name)])
        for call in self.fetch.call_args_list:
            self.assertEqual(call.kwargs["headers"], {"Authorization": f"Bearer {self.token}"})
            self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(self.fetch.call_args_list[0].args[0], replays.REPLAY_API_URL)

    def test_secret_never_sent_to_other_targets(self) -> None:
        for url in (
            "https://replay.tsgames.ru/rv/ajax.php?a=gl",
            "http://replay-relay.murtagy.stream/replay?a=gl",
            "https://replay-relay.murtagy.stream.evil.example/replay?a=gl",
            "https://replay-relay.murtagy.stream:8443/replay?a=gl",
            "https://user:pass@replay-relay.murtagy.stream/replay?a=gl",
            "https://replay-relay.murtagy.stream/private?a=gl",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "not allowed"):
                replays.get_frags(url, "test")
        self.fetch.assert_not_called()

    def test_missing_credential_directory_fails_without_direct_fallback(self) -> None:
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": ""}):
            with self.assertRaisesRegex(RuntimeError, "credentials unavailable"):
                replays.get_new_replays([])
        self.fetch.assert_not_called()

    def test_missing_credential_file_fails_without_request(self) -> None:
        self.credential.unlink()
        with self.assertRaisesRegex(RuntimeError, "credentials unavailable"):
            replays.get_new_replays([])
        self.fetch.assert_not_called()

    def test_invalid_credentials_are_rejected_without_disclosure(self) -> None:
        for token in ("", "not-a-token\nheader", "secret\N{SNOWMAN}"):
            self.credential.write_text(token, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "^Replay relay credentials (invalid|unavailable)$"):
                replays.get_new_replays([])
        self.fetch.assert_not_called()

    def test_redirects_are_rejected(self) -> None:
        self.fetch.return_value = self.response({}, status=302)
        with self.assertRaisesRegex(RuntimeError, "redirects are not allowed"):
            replays.get_new_replays([])
        self.assertFalse(self.fetch.call_args.kwargs["allow_redirects"])
