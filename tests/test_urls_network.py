"""How the URL probe reports network failures, and which servers it tolerates.

A station on an HTTPS address could not be played: the server still
offers only a key exchange OpenSSL 3 has dropped from its default cipher list,
so the handshake failed and the raw requests exception reached the user as
"Command failed. Try again."
"""

import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import load

urls = load("urls")
exceptions = load("vendors").requests().exceptions


class NetworkMessages(unittest.TestCase):
    """Every failure the probe can hit must say what went wrong."""

    def test_each_failure_has_its_own_wording(self):
        seen = {}
        for error in (exceptions.SSLError("x"), exceptions.ConnectTimeout("x"),
                      exceptions.ReadTimeout("x"), exceptions.ConnectionError("x"),
                      exceptions.RequestException("x")):
            message = urls.describe_network_error(error)
            self.assertTrue(message, f"{type(error).__name__} has no message")
            seen[type(error).__name__] = message
        self.assertEqual(len(set(seen.values())), len(seen), f"wording repeats: {seen}")

    def test_an_unrelated_error_is_left_alone(self):
        self.assertIsNone(urls.describe_network_error(ValueError("not a request")))

    def test_the_resolver_raises_something_speakable(self):
        """A bare requests error reaches the user as a useless generic message."""
        def refuse(*args, **kwargs):
            raise exceptions.SSLError("handshake failure")

        with self.assertRaises(RuntimeError) as caught:
            urls.resolve_audio("https://example.com/a.mp3", get=refuse)
        self.assertIn("secure connection", str(caught.exception))

    def test_cancelling_still_cancels(self):
        def refuse(*args, **kwargs):
            raise exceptions.SSLError("handshake failure")

        with self.assertRaises(urls.Cancelled):
            urls.resolve_audio("https://example.com/a.mp3", get=refuse,
                               cancelled=lambda: True)


class LegacyTls(unittest.TestCase):
    def test_a_handshake_failure_is_retried_with_a_wider_cipher_list(self):
        attempts = []

        class Session:
            def __init__(self, tag):
                self.tag = tag

            def get(self, url, **kwargs):
                attempts.append(self.tag)
                if self.tag == "default":
                    raise exceptions.SSLError("handshake failure")
                return "response"

            def close(self):
                pass

        fetcher = urls.Fetcher()
        fetcher._build = lambda ciphers=None: Session("default" if ciphers is None else "wide")
        self.assertEqual(fetcher("https://example.com/a.mp3"), "response")
        self.assertEqual(attempts, ["default", "wide"])

    def test_the_wider_list_still_verifies_the_certificate(self):
        """Widening ciphers must not become 'trust anything'."""
        fetcher = urls.Fetcher()
        contexts = []
        real = ssl.create_default_context

        def record(*args, **kwargs):
            context = real(*args, **kwargs)
            contexts.append(context)
            return context

        with patch.object(ssl, "create_default_context", record):
            fetcher._build(urls.LEGACY_CIPHERS)
        self.assertEqual(len(contexts), 1)
        context = contexts[0]
        self.assertTrue(context.check_hostname, "hostname checking was turned off")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED,
                         "certificate verification was turned off")

    def test_the_wider_list_actually_adds_the_missing_cipher(self):
        strict = {c["name"] for c in ssl.create_default_context().get_ciphers()}
        wide = ssl.create_default_context()
        wide.set_ciphers(urls.LEGACY_CIPHERS)
        added = {c["name"] for c in wide.get_ciphers()} - strict
        self.assertTrue(added, "the wider list offers nothing new; the retry is pointless")


if __name__ == "__main__":
    unittest.main()
