from __future__ import annotations

import unittest

from utils import is_trusted_twitch_url, redact_sensitive_data


class SecurityHelpersTests(unittest.TestCase):
    def test_only_trusted_https_domains_are_allowed(self) -> None:
        self.assertTrue(is_trusted_twitch_url("https://api.twitch.tv/helix/games"))
        self.assertTrue(is_trusted_twitch_url("https://video-edge.ttvnw.net/stream.m3u8"))
        self.assertTrue(is_trusted_twitch_url("https://static-cdn.jtvnw.net/image.png"))
        self.assertFalse(is_trusted_twitch_url("http://api.twitch.tv/helix/games"))
        self.assertFalse(is_trusted_twitch_url("https://api.twitch.tv.example.com/"))
        self.assertFalse(is_trusted_twitch_url("https://127.0.0.1/private"))

    def test_sensitive_log_values_are_redacted(self) -> None:
        data = {
            "headers": {"Authorization": "OAuth secret-token"},
            "data": {"token": "secret-token", "password": "secret-password"},
            "url": "https://usher.ttvnw.net/stream.m3u8?sig=signature&quality=low",
        }

        redacted = repr(redact_sensitive_data(data))

        self.assertNotIn("secret-token", redacted)
        self.assertNotIn("secret-password", redacted)
        self.assertNotIn("signature", redacted)
        self.assertIn("quality", redacted)


if __name__ == "__main__":
    unittest.main()
