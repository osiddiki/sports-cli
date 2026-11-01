import os
import pathlib
import hashlib
import io
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from sportscli import ai_ad_mute, app as nba


class FakePlayer:
    """A live IPC peer with mutable playback and a deterministic frame worker."""
    def __init__(self, path, muted=False):
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path); self.server.listen(1)
        self.muted = muted; self.paused = False; self.position = 10.0
        self.stalled = False; self.score = .01; self.delay = 0
        self.commands = []; self.frames = 0; self.events = []; self.errors = []
        self.frame_error = False; self.mute_error = False; self.hook = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            connection, _ = self.server.accept()
            with connection, connection.makefile('rb') as reader:
                for line in reader:
                    request = json.loads(line); command = request['command']
                    self.commands.append(command)
                    if self.hook: self.hook(command)
                    data = None; error = 'success'
                    if command[:2] == ['get_property', 'pause']: data = self.paused
                    elif command[:2] == ['get_property', 'time-pos']:
                        if not self.stalled: self.position += .1
                        data = self.position
                    elif command[:2] == ['get_property', 'mute']: data = self.muted
                    elif command[:2] == ['set_property', 'mute']:
                        if self.mute_error: error = 'property unavailable'
                        else: self.muted = command[2]
                    elif command[0] == 'screenshot-to-file':
                        self.frames += 1
                        if self.frame_error: error = 'screenshot failed'
                        else:
                            pathlib.Path(command[1]).write_text(json.dumps({'score':self.score, 'delay':self.delay}))
                    while self.events:
                        connection.sendall((json.dumps({'event':self.events.pop(0)})+'\n').encode())
                    connection.sendall((json.dumps({'request_id':request['request_id'], 'error':error, 'data':data})+'\n').encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.errors.append(exc)

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return True
            time.sleep(.005)
        return False

    @property
    def mute_commands(self):
        return [command[2] for command in self.commands if command[:2] == ['set_property', 'mute']]


class AiAdMuteTests(unittest.TestCase):
    @contextmanager
    def detector(self, muted=False, on_break=None):
        with tempfile.TemporaryDirectory(prefix='ai-ipc-test-', dir='/tmp') as root:
            worker = pathlib.Path(root) / 'worker.py'
            worker.write_text('import json,sys,time\nprint(json.dumps({"ready":True}),flush=True)\n'
                              'for line in sys.stdin:\n'
                              ' with open(json.loads(line)["image"]) as f: frame=json.load(f)\n'
                              ' time.sleep(frame["delay"])\n'
                              ' print(json.dumps({"score":frame["score"]}),flush=True)\n')
            path = str(pathlib.Path(root) / 'player.sock')
            player = FakePlayer(path, muted=muted)
            monitor = ai_ad_mute.Monitor(path, root, python=sys.executable, model='unused', interval=.03, on_break=on_break)
            try:
                with patch.object(ai_ad_mute, 'WORKER', worker):
                    monitor.start()
                    yield player, monitor
                    monitor.stop()
                self.assertFalse(monitor.thread.is_alive())
                self.assertFalse((pathlib.Path(root) / 'current-frame.jpg').exists())
                self.assertEqual(player.errors, [])
            finally:
                monitor.stop(); player.server.close(); player.thread.join(timeout=2)

    def test_repeated_predictions_and_uncertain_frames_are_conservative(self):
        decision = ai_ad_mute.Decision()
        self.assertFalse(decision.update(.1))
        self.assertTrue(decision.update(.05))
        self.assertFalse(decision.update(.9))
        self.assertFalse(decision.update(.95))
        self.assertFalse(decision.update(.1))
        self.assertTrue(decision.update(.1))
        self.assertTrue(decision.update(.5))
        self.assertTrue(decision.update(.5))
        self.assertFalse(decision.update(.5))

    def test_playback_gate_ignores_paused_stalled_and_invalid_positions(self):
        gate = ai_ad_mute.PlaybackGate()
        self.assertTrue(gate.accept(False, 10.0))
        self.assertFalse(gate.accept(True, 10.5))
        self.assertFalse(gate.accept(False, 10.0))
        self.assertFalse(gate.accept(False, None))
        self.assertFalse(gate.accept(False, float('nan')))
        self.assertFalse(gate.accept(None, 10.5))
        self.assertTrue(gate.accept(False, 10.5))
        self.assertTrue(gate.accept(False, 3.0))  # Seeking backwards is valid.
        self.assertTrue(gate.discontinuity)
        self.assertTrue(gate.accept(False, 90.0))
        self.assertTrue(gate.discontinuity)

    def test_stop_restores_detector_mute_while_player_stays_open(self):
        with self.detector() as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.muted))
            monitor.stop()
            self.assertFalse(player.muted)
            self.assertEqual(player.mute_commands, [True, False])

    def test_stop_preserves_player_that_was_already_muted(self):
        with self.detector(muted=True) as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.frames >= 4))
            monitor.stop()
            self.assertTrue(player.muted)
            self.assertEqual(player.mute_commands, [])

    def test_first_coverage_frame_releases_detector_mute(self):
        with self.detector() as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.muted))
            player.score = .95
            coverage_frames = []
            def hook(command):
                if command[0] == 'screenshot-to-file': coverage_frames.append(player.frames + 1)
            player.hook = hook
            self.assertTrue(player.wait_for(lambda: not player.muted))
            self.assertEqual(len(coverage_frames), 1)

    def test_break_callback_follows_detected_breaks(self):
        events = []
        def on_break(active, command):
            events.append(active)
            self.assertEqual(command('get_property', 'mute')['error'], 'success')
        with self.detector(on_break=on_break) as (player, monitor):
            self.assertTrue(player.wait_for(lambda: events == [True]))
            player.score = .95
            self.assertTrue(player.wait_for(lambda: events == [True, False]))
            player.score = .01
            self.assertTrue(player.wait_for(lambda: events == [True, False, True]))
            monitor.stop()
        self.assertEqual(events, [True, False, True, False])

    def test_break_callback_ends_when_user_unmutes(self):
        events = []
        with self.detector(on_break=lambda active, command: events.append(active)) as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.muted))
            player.muted = False
            self.assertTrue(player.wait_for(lambda: events == [True, False]))

    def test_pause_or_stall_releases_mute(self):
        for pause in (False, True):
            with self.subTest(paused=pause), self.detector() as (player, monitor):
                self.assertTrue(player.wait_for(lambda: player.muted))
                player.paused = pause; player.stalled = True
                self.assertTrue(player.wait_for(lambda: not player.muted))

    def test_failed_frame_releases_mute_and_discards_previous_evidence(self):
        with self.detector() as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.muted))
            player.frame_error = True
            self.assertTrue(player.wait_for(lambda: not player.muted))
            player.score = .95; player.frame_error = False
            self.assertTrue(player.wait_for(lambda: player.frames >= 5))
            self.assertEqual(player.mute_commands, [True, False])

    def test_worker_failure_restores_mute(self):
        with self.detector() as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.muted))
            monitor.process.kill()
            self.assertTrue(player.wait_for(lambda: not monitor.thread.is_alive()))
            self.assertFalse(player.muted)

    def test_invalid_prediction_releases_mute(self):
        for score in (True, float('nan')):
            with self.subTest(score=score), self.detector() as (player, monitor):
                self.assertTrue(player.wait_for(lambda: player.muted))
                player.score = score
                self.assertTrue(player.wait_for(lambda: not player.muted))

    def test_seek_releases_owned_mute_before_classifying_new_position(self):
        with self.detector() as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.muted))
            frames_before_seek = player.frames; released_at = []
            def hook(command):
                if command == ['set_property', 'mute', False]: released_at.append(player.frames)
            player.hook = hook; player.score = .95
            player.position += 100; player.events.append('seek')
            self.assertTrue(player.wait_for(lambda: not player.muted))
            self.assertEqual(released_at, [frames_before_seek])

    def test_seek_with_mute_reply_immediately_restores_newly_owned_mute(self):
        with self.detector() as (player, monitor):
            released_at = []
            def hook(command):
                if command == ['set_property', 'mute', True]:
                    player.position += 100; player.events.append('seek'); player.score = .95
                elif command == ['set_property', 'mute', False]:
                    released_at.append(player.frames)
            player.hook = hook
            self.assertTrue(player.wait_for(lambda: bool(released_at)))
            self.assertEqual(released_at, [2])
            self.assertEqual(player.mute_commands, [True, False])
            self.assertFalse(player.muted)

    def test_rejected_mute_command_does_not_claim_user_mute(self):
        with self.detector() as (player, monitor):
            player.mute_error = True
            self.assertTrue(player.wait_for(lambda: not monitor.thread.is_alive()))
            self.assertFalse(player.muted)
            self.assertEqual(player.mute_commands, [True])

    def test_seek_during_inference_discards_old_frame(self):
        for event in ('seek', 'file-loaded'):
            with self.subTest(event=event), self.detector() as (player, monitor):
                player.delay = .08
                def hook(command):
                    if command[0] == 'screenshot-to-file' and player.frames == 1:
                        player.position += 100
                        player.events.append(event)
                    elif command[0] == 'screenshot-to-file' and player.frames >= 2:
                        player.score = .95
                player.hook = hook
                self.assertTrue(player.wait_for(lambda: player.frames >= 4))
                self.assertEqual(player.mute_commands, [])

    def test_seek_resets_accumulated_ad_evidence_without_an_event(self):
        with self.detector() as (player, monitor):
            def hook(command):
                if command[0] == 'screenshot-to-file' and player.frames == 0:
                    player.position += 100
                elif command[0] == 'screenshot-to-file' and player.frames >= 2:
                    player.score = .95
            player.hook = hook
            self.assertTrue(player.wait_for(lambda: player.frames >= 4))
            self.assertEqual(player.mute_commands, [])

    def test_manual_unmute_survives_uncertain_predictions_until_coverage(self):
        with self.detector() as (player, monitor):
            self.assertTrue(player.wait_for(lambda: player.muted))
            player.muted = False
            start = player.frames
            self.assertTrue(player.wait_for(lambda: player.frames >= start + 2))
            player.score = .5; start = player.frames
            self.assertTrue(player.wait_for(lambda: player.frames >= start + 5))
            player.score = .01; start = player.frames
            self.assertTrue(player.wait_for(lambda: player.frames >= start + 4))
            self.assertFalse(player.muted)
            self.assertEqual(player.mute_commands, [True])
            player.score = .95; start = player.frames
            self.assertTrue(player.wait_for(lambda: player.frames >= start + 2))
            player.score = .01
            self.assertTrue(player.wait_for(lambda: player.muted))

    def test_model_and_runtime_are_external_and_never_enabled_by_config(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XDG_DATA_HOME':root}):
            python, model = ai_ad_mute.installation_paths()
            self.assertTrue(python.is_relative_to(pathlib.Path(root)))
            self.assertTrue(model.is_relative_to(pathlib.Path(root)))
            self.assertFalse(ai_ad_mute.installed())
            self.assertFalse(nba.App(None, {'ai_ad_mute':True}).ai_ad_mute)
            self.assertTrue(nba.App(None, {}, ai_ad_mute_enabled=True).ai_ad_mute)

    def test_windows_uses_named_pipes_scripts_and_the_py_launcher(self):
        windows = SimpleNamespace(name='nt', environ=os.environ)
        with patch.object(ai_ad_mute, 'os', windows), patch.object(ai_ad_mute.ad_mute, 'os', SimpleNamespace(name='nt', path=os.path)):
            self.assertEqual(ai_ad_mute.ad_mute.ipc_path('/tmp/sports-mpv-abc'), r'\\.\pipe\sports-mpv-sports-mpv-abc')
            self.assertEqual(ai_ad_mute.venv_python('/data/venv').parts[-2:], ('Scripts', 'python.exe'))
            found = SimpleNamespace(returncode=0, stdout='C:\\Python312\\python.exe\n')
            missing = SimpleNamespace(returncode=103, stdout='')
            with patch.object(ai_ad_mute.sys, 'version_info', (3, 13, 0)), \
                 patch.object(ai_ad_mute.shutil, 'which', return_value='C:\\Windows\\py.exe'), \
                 patch.object(ai_ad_mute.subprocess, 'run', side_effect=[found]) as run:
                self.assertEqual(ai_ad_mute.compatible_python(), ('C:\\Python312\\python.exe', (3, 12)))
                self.assertEqual(run.call_args.args[0][1], '-3.12')
            # 3.9 has no Windows detection runtime, so it is never chosen there.
            with patch.object(ai_ad_mute.sys, 'version_info', (3, 9, 0)), \
                 patch.object(ai_ad_mute.shutil, 'which', return_value='C:\\Windows\\py.exe'), \
                 patch.object(ai_ad_mute.subprocess, 'run', return_value=missing) as run:
                self.assertEqual(ai_ad_mute.compatible_python(), (None, None))
                self.assertEqual([call.args[0][1] for call in run.call_args_list], ['-3.12', '-3.11', '-3.10'])

    def test_installer_replaces_an_interrupted_setup(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XDG_DATA_HOME': root}), \
             patch.object(ai_ad_mute, 'compatible_python', return_value=(sys.executable, (3, 12))), \
             patch.object(ai_ad_mute.urllib.request, 'urlopen', side_effect=OSError('offline')), \
             patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as errors:
            leftover = ai_ad_mute.installation_dir() / 'venv'
            leftover.mkdir(parents=True)
            self.assertEqual(ai_ad_mute.install(), 1)
            self.assertFalse(leftover.exists())
            self.assertIn('offline', errors.getvalue())

    def test_explicit_installer_checks_download_before_installing(self):
        contents = b'local test model'
        def run(args, **kwargs):
            if len(args) > 3 and args[1:3] == ['-m', 'venv']:
                python = pathlib.Path(args[3]) / 'bin' / 'python'
                python.parent.mkdir(parents=True)
                python.write_text('test interpreter')
            elif len(args) > 3 and args[1] == str(ai_ad_mute.EXPORTER):
                pathlib.Path(args[3]).write_bytes(b'converted model')
            return SimpleNamespace(stdout='{"ready":true}\n', returncode=0)
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XDG_DATA_HOME':root}), \
             patch.object(ai_ad_mute.urllib.request, 'urlopen', side_effect=lambda *a, **k:io.BytesIO(contents)), \
             patch.object(ai_ad_mute.subprocess, 'run', side_effect=run) as execute, \
             patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            with patch.object(ai_ad_mute, 'MODEL_SHA256', 'wrong-checksum'):
                self.assertEqual(ai_ad_mute.install(), 1)
            self.assertFalse(ai_ad_mute.installed())
            execute.assert_not_called()
            with patch.object(ai_ad_mute, 'MODEL_SHA256', hashlib.sha256(contents).hexdigest()):
                self.assertEqual(ai_ad_mute.install(), 0)
            self.assertTrue(ai_ad_mute.installed())
            self.assertEqual(execute.call_count, 6)

    def test_other_sports_play_without_nba_classifier(self):
        class Screen:
            def refresh(self): pass
        app = nba.App(Screen(), {}, ai_ad_mute_enabled=True)
        app.league = 'nfl'; app.render = lambda: None
        app.resolve_feed = lambda feed: ('https://example.com/video', {})
        feed = nba.Feed('Example', 'Football', kind='direct')
        # A locally installed multi-sport model must not change this result.
        with patch.object(ai_ad_mute, 'installed', return_value=True), \
             patch.object(ai_ad_mute, 'supported_leagues', return_value={'nba'}), \
             patch.object(ai_ad_mute, 'Monitor') as monitor, \
             patch.object(nba.shutil, 'which', return_value='/usr/bin/mpv'), \
             patch.object(nba.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(nba.curses, 'def_prog_mode'), patch.object(nba.curses, 'endwin'), \
             patch.object(nba.curses, 'reset_prog_mode'):
            app.play(feed)
        monitor.assert_not_called()
        self.assertIn('limited to NBA', app.message)

    def test_nba_detector_playback_copies_frames_back_for_software_screenshots(self):
        class Screen:
            def refresh(self): pass
        app = nba.App(Screen(), {}, ai_ad_mute_enabled=True)
        app.league = 'nba'; app.render = lambda: None
        app.resolve_feed = lambda feed: ('https://example.com/video', {})
        commands = []
        with patch.object(ai_ad_mute, 'installed', return_value=True), \
             patch.object(ai_ad_mute, 'supported_leagues', return_value={'nba'}), \
             patch.object(ai_ad_mute, 'Monitor'), \
             patch.object(nba.shutil, 'which', return_value='/usr/bin/mpv'), \
             patch.object(nba.subprocess, 'run', side_effect=lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0)), \
             patch.object(nba.curses, 'def_prog_mode'), patch.object(nba.curses, 'endwin'), \
             patch.object(nba.curses, 'reset_prog_mode'):
            app.play(nba.Feed('Example', 'Game', kind='direct'))
        mpv = next(command for command in commands if command[0] == 'mpv')
        # Screenshots of GPU-only frames fail after a timestamp jump; copied-back frames do not.
        self.assertIn('--hwdec=auto-copy-safe', mpv)
        self.assertIn('--screenshot-sw=yes', mpv)
        self.assertNotIn('--hwdec=auto-safe', mpv)

    def test_observe_mode_shows_predictions_without_changing_audio(self):
        with tempfile.TemporaryDirectory(prefix='observe-test-', dir='/tmp') as root:
            worker = pathlib.Path(root) / 'worker.py'
            worker.write_text('import json,sys\nprint(json.dumps({"ready":True}),flush=True)\n'
                              'for line in sys.stdin: print(json.dumps({"score":0.01}),flush=True)\n')
            path = str(pathlib.Path(root) / 'player.sock')
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path); server.listen(1)
            commands = []; showed_ad = threading.Event()
            def serve():
                connection, _ = server.accept()
                with connection, connection.makefile('rb') as reader:
                    position = 0
                    for line in reader:
                        request = json.loads(line); command = request['command']; commands.append(command)
                        data = None
                        if command[:2] == ['get_property','pause']: data = False
                        elif command[:2] == ['get_property','time-pos']:
                            position += 1; data = position
                        elif command[0] == 'screenshot-to-file': pathlib.Path(command[1]).write_bytes(b'frame')
                        elif command[0] == 'show-text' and 'Possible ad' in command[1]: showed_ad.set()
                        response = {'request_id':request['request_id'], 'error':'success', 'data':data}
                        connection.sendall((json.dumps(response)+'\n').encode())
            thread = threading.Thread(target=serve, daemon=True); thread.start()
            monitor = ai_ad_mute.Monitor(path, root, python=sys.executable, model='unused', interval=.05, mute=False)
            try:
                with patch.object(ai_ad_mute, 'WORKER', worker):
                    monitor.start()
                    self.assertTrue(showed_ad.wait(3), 'No ad prediction was displayed')
                    monitor.stop()
                self.assertFalse(any(command[:2] == ['set_property','mute'] for command in commands))
            finally:
                monitor.stop(); server.close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
