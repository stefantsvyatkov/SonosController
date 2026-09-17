"""URL validation and confirmation regressions without network access."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
package = types.ModuleType('url_test_package')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'globalPlugins/sonosController')]
sys.modules[package.__name__] = package
from url_test_package import urls

class Response:
    def __init__(self, url, mime, body=b'', status=200, **headers):
        self.url, self.body, self.status_code = url, body, status
        self.headers = {'Content-Type': mime, **headers}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError('HTTP failure')
    def iter_content(self, size): yield self.body

class Player:
    def __init__(self, states=('PLAYING', 'PLAYING'), error=None):
        self.group = types.SimpleNamespace(coordinator=self)
        self.states = iter(states)
        self.state = 'STOPPED'
        self.uri = ''
        self.calls = []
        self.error = error
    def play_uri(self, uri, **kwargs):
        self.calls.append('uri')
        self.uri = uri
    def play(self):
        self.calls.append('play')
        if self.error:
            error, self.error = self.error, None
            raise error
    def get_current_media_info(self): return {'uri': self.uri}
    def get_current_transport_info(self):
        self.state = next(self.states, self.state)
        return {'current_transport_state': self.state}

class UrlTests(unittest.TestCase):
    def test_playlist_resolves_and_html_rejected(self):
        def get(url, **kwargs):
            if url.endswith('.pls'):
                return Response(url, 'audio/x-scpls', b'[playlist]\nFile1=https://example.com/stream\nTitle1=Radio')
            return Response(url, 'audio/mpeg')
        self.assertEqual(urls.resolve_audio('https://example.com/a.pls', get).url, 'https://example.com/stream')
        with self.assertRaisesRegex(RuntimeError, 'web page'):
            urls.resolve_audio('https://example.com', lambda *a, **kw: Response(a[0], 'text/html'))

    def test_hls_keeps_manifest_and_https(self):
        address = 'https://example.com/live.m3u8'
        source = urls.resolve_audio(address, lambda *a, **kw: Response(address, 'application/vnd.apple.mpegurl', b'#EXTM3U\n#EXT-X-VERSION:3\nsegment.ts'))
        self.assertEqual(source.kind, 'hls')
        self.assertEqual(urls.transport_candidates(source), [address])

    def test_redirect_downgrade_and_loop_rejected(self):
        address = 'https://example.com/live'
        for target, message in [('http://example.com/live', 'Insecure'), (address, 'loop')]:
            with self.assertRaisesRegex(RuntimeError, message):
                urls.resolve_audio(address, lambda *a, **kw: Response(address, '', status=302, Location=target))

    def test_playlist_tries_reachable_alternative(self):
        def get(address, **kwargs):
            if address.endswith('.m3u'): return Response(address, 'audio/mpegurl', b'missing\nworking')
            return Response(address, 'audio/mpeg', status=404 if address.endswith('missing') else 200)
        self.assertTrue(urls.resolve_audio('http://example.com/list.m3u', get).url.endswith('/working'))

    def test_unsafe_urls_rejected(self):
        for address in ['file:///music.mp3', 'http://user:password@example.com/a', 'http://example.com:99999/a', 'http://example.com/a\nb']:
            with self.assertRaises(RuntimeError): urls.clean_url(address)

    def test_701_retries_only_play(self):
        class NotReady(Exception): error_code = '701'
        player = Player(('STOPPED', 'TRANSITIONING', 'PLAYING', 'PLAYING'), NotReady())
        with patch.object(urls.time, 'sleep'):
            urls._confirmed_start(player, 'http://example.com/live', '', lambda: False, lambda msg: None)
        self.assertEqual(player.calls, ['uri', 'play', 'play'])

    def test_timeout_after_success_does_not_restart(self):
        # A timeout may arrive after the speaker has already started.
        player = Player(error=TimeoutError())
        with patch.object(urls.time, 'sleep'):
            urls._confirmed_start(player, 'http://example.com/live', '', lambda: False, lambda msg: None)
        self.assertEqual(player.calls, ['uri', 'play'])

    def test_stopped_is_not_success(self):
        player = Player(('STOPPED',))
        with patch.object(urls.time, 'sleep'), patch.object(urls.time, 'monotonic', side_effect=range(30)):
            with self.assertRaisesRegex(RuntimeError, 'did not start'):
                urls._confirmed_start(player, 'http://example.com/live', '', lambda: False, lambda msg: None)

    def test_cancel_does_not_touch_speaker(self):
        player = Player()
        self.assertEqual(urls.play_url(player, 'http://example.com/live', cancelled=lambda: True), '')
        self.assertEqual(player.calls, [])

    def test_changed_source_is_not_success(self):
        player = Player()
        player.get_current_media_info = lambda: {'uri': 'http://example.com/other'}
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            urls._confirmed_start(player, 'http://example.com/live', '', lambda: False, lambda msg: None)

    def test_format_rejection_tries_second_direct_transport(self):
        class Rejected(Exception): error_code = '714'
        source = urls.AudioSource('http://example.com/live', 'radio', 'audio/mpeg', 'Radio')
        with patch.object(urls, 'resolve_audio', return_value=source), patch.object(urls, '_confirmed_start', side_effect=[Rejected(), True]) as start:
            self.assertEqual(urls.play_url(Player(), source.url), 'Playing Radio')
            self.assertEqual(start.call_count, 2)


    def test_playlist_prefers_explicit_aac_over_ogg(self):
        requested = []
        def get(address, **kwargs):
            requested.append(address)
            if address.endswith('.m3u'):
                return Response(address, 'audio/mpegurl', b'#EXTM3U\nradio.ogg\nradio.aac')
            return Response(address, 'audio/aac')
        source = urls.resolve_audio('http://example.com/radio.m3u', get)
        self.assertTrue(source.url.endswith('radio.aac'))
        self.assertEqual(len(requested), 2)

    def test_failed_ogg_has_actionable_error(self):
        source = urls.AudioSource('http://example.com/radio.ogg', 'radio', 'application/ogg', 'Radio')
        with patch.object(urls, 'resolve_audio', return_value=source), patch.object(urls, '_confirmed_start', side_effect=RuntimeError('The speaker did not start the stream.')):
            with self.assertRaisesRegex(RuntimeError, 'MP3 or AAC'):
                urls.play_url(Player(), source.url)

    def test_opus_is_detected(self):
        sample = b"OggS" + b"\0" * 24 + b"OpusHead"
        source = urls.resolve_audio("http://example.com/live", lambda *a, **kw: Response(a[0], "application/octet-stream", sample))
        self.assertEqual(source.mime, "audio/opus")
