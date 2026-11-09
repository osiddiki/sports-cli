"""A seek on a replay timeline moves the playhead and does not carry a score."""
import json
import re
import unittest
from unittest.mock import patch

from sportscli import app as nba
from sportscli import timeline


def vod(body):
    return '#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n' + body + '#EXT-X-ENDLIST\n'


def quarters(seconds=1200):
    """Four equal stretches and no cues, long enough to slice into periods."""
    lines = []
    for _ in range(4):
        lines.append(f'#EXTINF:{seconds},\npart.ts\n')
    return vod(''.join(lines))


class TimelineTests(unittest.TestCase):
    def test_a_seek_command_moves_the_playhead_and_does_not_expose_a_score(self):
        built = timeline.from_playlist(quarters(), 'basketball')
        playhead, chapter, command = timeline.move(built, 30, {'by': timeline.STEP})
        self.assertEqual(playhead, 40)
        self.assertEqual(command, {'seek': 40.0, 'chapter': 'Q1'})
        self.assertEqual(chapter, 'Q1')
        further, _, minute = timeline.move(built, playhead, {'by': timeline.MINUTE})
        self.assertEqual(further, 100)
        self.assertEqual(minute['seek'], 100)
        shown = json.dumps({'timeline': built.as_dict(), 'command': command, 'chapter': chapter})
        self.assertNotIn('score', shown.casefold())
        self.assertIsNone(re.search(r'\d+\s*[-–—]\s*\d+', shown))
        self.assertNotIn('scoreboard', shown.casefold())

    def test_shift_sized_and_period_seeks_stay_inside_the_replay(self):
        built = timeline.from_playlist(quarters(), 'basketball')
        end, _, command = timeline.move(built, 4790, {'by': timeline.STEP})
        self.assertEqual(end, 4800)
        self.assertEqual(command['seek'], 4800)
        start, name, back = timeline.move(built, 5, {'by': -timeline.MINUTE})
        self.assertEqual((start, name, back['seek']), (0, 'Q1', 0))
        landing, chapter, jump = timeline.move(built, 30, {'chapter': 1})
        self.assertEqual(landing, 1200)
        self.assertEqual(chapter, 'Q2')
        self.assertEqual(jump, {'seek': 1200, 'chapter': 'Q2'})
        # In the middle of Q2, the previous period is Q1, matching mpv's add chapter -1.
        previous, name, _ = timeline.move(built, 1500, {'chapter': -1})
        self.assertEqual((previous, name), (0, 'Q1'))
        stuck, _, hold = timeline.move(built, 4000, {'chapter': 1})
        self.assertEqual(stuck, 4000)
        self.assertEqual(hold['chapter'], 'Q4')

    def test_a_playlist_that_never_ends_is_not_seekable_and_has_no_invented_periods(self):
        manifest = '#EXTM3U\n#EXTINF:6,\nseg.ts\n'
        built = timeline.from_playlist(manifest, 'basketball')
        self.assertFalse(built.seekable)
        self.assertEqual(built.chapters, ())
        playhead, chapter, command = timeline.move(built, 12, {'by': timeline.STEP})
        self.assertEqual(playhead, 12)
        self.assertIsNone(chapter)
        self.assertEqual(command, {'seek': 12, 'chapter': None})

    def test_a_short_replay_can_be_seeked_without_invented_quarters(self):
        built = timeline.from_playlist(vod('#EXTINF:30,\nonly.ts\n'), 'basketball')
        self.assertTrue(built.seekable)
        self.assertEqual(built.chapters, ())
        playhead, chapter, command = timeline.move(built, 0, {'to': 12})
        self.assertEqual((playhead, chapter, command['seek']), (12, None, 12))

    def test_cue_boundaries_set_the_periods_when_they_match_the_sport(self):
        lengths = (100, 200, 100, 200)
        body = []
        for length in lengths:
            body.append(f'#EXTINF:{length},\nplay.ts\n#EXT-X-CUE-OUT:10\n#EXTINF:10,\nad.ts\n#EXT-X-CUE-IN\n')
        built = timeline.from_playlist(vod(''.join(body)), 'basketball')
        self.assertEqual([chapter.name for chapter in built.chapters], ['Q1', 'Q2', 'Q3', 'Q4'])
        self.assertEqual(built.chapters[1].start, 110)
        self.assertNotAlmostEqual(built.chapters[1].start, built.duration / 4)

    def test_a_named_cue_is_used_and_a_score_title_is_not(self):
        manifest = vod(
            '#EXT-X-PROGRAM-DATE-TIME:2024-06-01T00:00:00.000Z\n'
            '#EXT-X-DATERANGE:ID="q1",TITLE="Q1",START-DATE="2024-06-01T00:00:00.000Z"\n'
            '#EXT-X-DATERANGE:ID="score",TITLE="108-99",START-DATE="2024-06-01T00:20:00.000Z"\n'
            '#EXT-X-DATERANGE:ID="q2",TITLE="Q2",START-DATE="2024-06-01T00:30:00.000Z"\n'
            '#EXTINF:7200,\nfull.ts\n'
        )
        built = timeline.from_playlist(manifest, 'basketball')
        self.assertEqual([(chapter.name, chapter.start) for chapter in built.chapters], [('Q1', 0), ('Q2', 1800)])
        self.assertNotIn('108-99', json.dumps(built.as_dict()))

    def test_baseball_length_does_not_invent_innings(self):
        built = timeline.from_playlist(quarters(), 'baseball')
        self.assertTrue(built.seekable)
        self.assertEqual(built.chapters, ())

    def test_chapter_file_names_periods_for_mpv(self):
        built = timeline.from_playlist(quarters(), 'hockey')
        text = timeline.ffmetadata(built)
        self.assertIn('title=P1', text)
        self.assertIn('title=P3', text)
        self.assertNotIn('score', text.casefold())
        args = timeline.player_args('/tmp/sports-replay/chapters.ffmeta', '/tmp/sports-replay/chapters.conf')
        self.assertTrue(any(arg.startswith('--chapters-file=') for arg in args))
        self.assertTrue(any(arg.startswith('--input-commands=load-input-conf ') for arg in args))
        self.assertNotIn('--no-input-default-bindings', args)

    def test_replay_playback_hands_mpv_the_periods_and_not_a_score(self):
        screen = type('Screen', (), {'getmaxyx': lambda self: (24, 80)})()
        player = nba.App(screen, {})
        self.addCleanup(player.close)
        player.feed_context = 'replay'
        player.league = 'nba'
        with patch.object(timeline, 'media_playlist', return_value=quarters()):
            command, marks, note = player.replay_marks('https://cdn.example/a.m3u8', {}, ['mpv', '--', 'https://cdn.example/a.m3u8'])
        self.assertEqual(note, '')
        self.assertEqual(marks['chapters'][0]['name'], 'Q1')
        self.assertTrue(any(arg.startswith('--chapters-file=') for arg in command))
        self.assertEqual(command[0], 'mpv')
        self.assertEqual(command[-1], 'https://cdn.example/a.m3u8')
        self.assertNotIn('score', json.dumps(marks).casefold())
        playhead, _, sent = timeline.move(timeline.from_playlist(quarters(), 'basketball'), 0, {'by': timeline.STEP})
        self.assertEqual(sent['seek'], playhead)


    def test_a_playlist_the_worker_already_fetched_is_not_downloaded_again_on_the_screen_thread(self):
        screen = type('Screen', (), {'getmaxyx': lambda self: (24, 80)})()
        player = nba.App(screen, {})
        self.addCleanup(player.close)
        player.feed_context = 'replay'; player.league = 'nba'
        player.replay_manifests = {'https://cdn.example/a.m3u8': quarters()}
        with patch.object(timeline, 'media_playlist', side_effect=AssertionError('downloaded on the screen thread')):
            _, marks, _ = player.replay_marks('https://cdn.example/a.m3u8', {}, ['mpv', '--', 'https://cdn.example/a.m3u8'])
        self.assertEqual(marks['chapters'][0]['name'], 'Q1')
        self.assertEqual(player.replay_manifests, {})

if __name__ == '__main__':
    unittest.main()
