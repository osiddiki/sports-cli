import http.client
import threading
import unittest
from unittest import mock

from sportscli import app, web


def bare_web():
    server = object.__new__(web.Web)
    server.token, server.lock, server.proxied = 'secret', threading.Lock(), {}
    return server


class RewriteTests(unittest.TestCase):
    def test_playlist_links_come_back_through_the_proxy_with_headers(self):
        server = bare_web()
        headers = {'Referer': 'https://player.example/'}
        text = ('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:6,\nseg1.ts\n'
                '#EXT-X-STREAM-INF:BANDWIDTH=1\nlow/index.m3u8?token=1\n')
        lines = server.rewrite(text, 'https://cdn.example/live/main.m3u8', headers).decode().splitlines()
        links = [line for line in lines if line.startswith('/secret/p/')]
        self.assertTrue(links[0].endswith('.ts'))
        self.assertTrue(links[1].endswith('.m3u8'))
        self.assertRegex(lines[1], r'URI="/secret/p/[0-9a-f]{32}\.ts"')
        targets = {url for url, saved in server.proxied.values() if saved == headers}
        self.assertEqual(targets, {'https://cdn.example/live/key.bin', 'https://cdn.example/live/seg1.ts',
                                   'https://cdn.example/live/low/index.m3u8?token=1'})


class ListingTests(unittest.TestCase):
    def test_working_sources_rank_by_smoothness_and_failed_ones_fill_up_to_five(self):
        feeds = [app.Feed('S', f'Source {i}', kind='sf') for i in range(7)]
        listing = web.Listing(feeds)
        listing.checks = {0: ('Failed', None, None), 1: ('OK', 720, 0.9), 2: ('OK', 540, 3.0),
                          3: ('HTTP 404', None, None), 4: ('Failed', None, None),
                          5: ('Failed', None, None), 6: ('Failed', None, None)}
        rows = listing.rows()
        self.assertEqual([row['i'] for row in rows[:2]], [2, 1])
        self.assertTrue(rows[0]['status'].startswith('Smooth'))
        self.assertTrue(rows[1]['status'].startswith('May lag'))
        self.assertEqual(sum(not row['hidden'] for row in rows), web.MIN_VISIBLE)
        self.assertTrue(all(row['failed'] for row in rows[2:]))


class TokenPathTests(unittest.TestCase):
    """The link must work with or without the slash after the token (people type it either way)."""
    def setUp(self):
        with mock.patch.object(web, 'addresses', return_value=['localhost']), mock.patch.object(web, 'Web'):
            self.server, _, _ = web.start({'web_token': 'secret'}, '127.0.0.1', 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def get(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        connection.request('GET', path); response = connection.getresponse()
        return response.status, response.getheader('Location'), response.read()

    def test_link_without_trailing_slash_redirects_so_relative_api_paths_keep_the_token(self):
        self.assertEqual(self.get('/secret')[:2], (301, '/secret/'))
        self.assertEqual(self.get('/secret?x=1')[:2], (301, '/secret/?x=1'))
        status, _, body = self.get('/secret/')
        self.assertEqual(status, 200); self.assertIn(b'<title>Sports CLI</title>', body)

    def test_paths_without_the_token_are_refused(self):
        self.assertEqual(self.get('/api/leagues')[0], 404)
        self.assertEqual(self.get('/secretX/')[0], 404)


if __name__ == '__main__':
    unittest.main()
