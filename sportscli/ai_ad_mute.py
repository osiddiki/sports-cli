"""Optional, explicitly installed local NBA commercial detection for mpv."""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from . import progress

from . import ad_mute
from .ad_mute import MuteState


MODEL_URL = ('https://huggingface.co/plt3/NBA-redzone-ML/resolve/'
             'cd44632d73cd178c975304dbb13502d6a49e3d91/part5_cropped_5-29-24.keras?download=true')
MODEL_SHA256 = '463f8964888ee2d55c176b385f4587eabd7cc508be36813c58db54925460ac83'
MAX_MODEL_BYTES = 160_000_000
WORKER = pathlib.Path(__file__).resolve().with_name('ai_ad_worker.py')
EXPORTER = pathlib.Path(__file__).resolve().parent / 'tools' / 'export_ad_model.py'


def installation_dir():
    base = pathlib.Path(os.environ.get('XDG_DATA_HOME') or pathlib.Path.home() / '.local' / 'share')
    return base / 'sports-live' / 'ad-mute'


def venv_python(venv):
    venv = pathlib.Path(venv)
    return venv / 'Scripts' / 'python.exe' if os.name == 'nt' else venv / 'bin' / 'python'


def installation_paths():
    root = installation_dir()
    # A locally trained model installed by tools.train_ad_model replaces the original.
    trained = root / 'ad-model.tflite'
    return venv_python(root / 'venv'), trained if trained.is_file() else root / 'nba-ads-v1.tflite'


def supported_leagues():
    """Leagues the active model was trained on; the original model covers only the NBA."""
    try:
        document = json.loads((installation_dir() / 'ad-model.json').read_text(encoding='utf-8'))
        leagues = document.get('leagues') if (installation_dir() / 'ad-model.tflite').is_file() else None
    except (OSError, ValueError, AttributeError):
        leagues = None
    if not isinstance(leagues, list) or not all(isinstance(item, str) for item in leagues): return {'nba'}
    return set(leagues) or {'nba'}


def installed():
    python, model = installation_paths()
    return python.is_file() and model.is_file()


def compatible_python():
    """A Python that the model conversion tools support (TensorFlow 2.16 needs 3.9 through 3.12)."""
    lowest = 10 if os.name == 'nt' else 9  # the Windows detection runtime starts at 3.10
    if (3, lowest) <= sys.version_info[:2] <= (3, 12):
        return sys.executable, sys.version_info[:2]
    for minor in range(12, lowest - 1, -1):
        if os.name == 'nt':
            # The Windows launcher finds every installed version: py -3.12.
            launcher = shutil.which('py')
            if not launcher: break
            try:
                found = subprocess.run([launcher, f'-3.{minor}', '-c', 'import sys; print(sys.executable)'],
                                       capture_output=True, text=True, timeout=20)
            except (OSError, subprocess.SubprocessError): continue
            if found.returncode == 0 and found.stdout.strip(): return found.stdout.strip(), (3, minor)
        else:
            executable = shutil.which(f'python3.{minor}')
            if executable: return executable, (3, minor)
    return None, None


def _captured_text(exc):
    """The tool's own last line, when a setup command captured it."""
    if not isinstance(exc, subprocess.CalledProcessError):
        return str(exc)
    captured = exc.stderr or exc.stdout or ''
    if isinstance(captured, bytes):
        captured = captured.decode('utf-8', 'replace')
    lines = [line.strip() for line in captured.splitlines() if line.strip()]
    return lines[-1] if lines else str(exc)


def install(log=None):
    """Install only when the user explicitly requests the optional model.

    log collects this install's text and the output of its setup commands.
    Without log, that text goes to the terminal, as the command-line install does.
    """
    def say(message, *, error=False, flush=False):
        print(message, file=log if log is not None else (sys.stderr if error else sys.stdout), flush=flush)

    def keep(chunk):
        if not log or not chunk:
            return
        if isinstance(chunk, bytes):
            chunk = chunk.decode('utf-8', 'replace')
        log.write(chunk if chunk.endswith('\n') else chunk + '\n')

    def spawn(args, **kwargs):
        if log is None:
            return subprocess.run(args, **kwargs)
        forwarded = dict(kwargs)
        if not forwarded.get('capture_output'):
            forwarded['stdout'] = subprocess.PIPE
            forwarded['stderr'] = subprocess.PIPE
        forwarded['text'] = True
        try:
            completed = subprocess.run(args, **forwarded)
        except subprocess.CalledProcessError as exc:
            keep(exc.stdout); keep(exc.stderr)
            raise
        keep(completed.stdout); keep(completed.stderr)
        return completed

    if installed():
        say(f'Local AI ad detector already installed in {installation_dir()}.')
        return 0
    if sys.platform not in ('darwin', 'win32') and not sys.platform.startswith('linux'):
        say('AI ad detection requires macOS, Linux or Windows.', error=True); return 1
    interpreter, python_version = compatible_python()
    if not interpreter:
        command = os.environ.get('SPORTS_CLI_COMMAND')
        say('The one-time AI setup needs Python 3.10 through 3.12.' if os.name == 'nt' else
            'The one-time AI setup needs Python 3.9 through 3.12 and venv.', error=True)
        if command: say(f'Run "{command} --setup-ads" to install a suitable Python and the detector.', error=True)
        return 1
    intel_mac = sys.platform == 'darwin' and platform.machine().lower() in ('x86_64', 'amd64')
    root = installation_dir()
    try: root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        say(f'Cannot create detector directory: {exc}', error=True); return 1
    if root.exists():
        # Only an interrupted earlier setup leaves this folder without a working detector; start over.
        say(f'Removing an incomplete earlier setup in {root}.')
        try: shutil.rmtree(root)
        except OSError as exc:
            say(f'Cannot remove the incomplete setup: {exc}', error=True); return 1
    say('Installing the optional NBA ad detector. This downloads a 150 MB source model and conversion packages;', flush=True)
    say('allow about 1.5 GB of temporary space. The final model is about 22 MB.', flush=True)
    if intel_mac: say('Intel macOS needs the larger TensorFlow runtime.')
    try: temporary = pathlib.Path(tempfile.mkdtemp(prefix='ad-mute-', dir=str(root.parent)))
    except OSError as exc:
        say(f'Cannot create temporary installation: {exc}', error=True); return 1
    try:
        build = temporary / 'build'; build.mkdir()
        source = build / 'source.keras'
        digest = hashlib.sha256(); total = 0
        with urllib.request.urlopen(MODEL_URL, timeout=45) as response, source.open('wb') as out:
            try: length = int(getattr(response, 'headers', {}).get('Content-Length', ''))
            except (ValueError, TypeError): length = None
            if length and length > MAX_MODEL_BYTES: raise ValueError('Model download exceeded the expected size')
            with progress.Download('Downloading model', length, log) as download:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk: break
                    total += len(chunk)
                    if total > MAX_MODEL_BYTES: raise ValueError('Model download exceeded the expected size')
                    digest.update(chunk); out.write(chunk)
                    download.update(total)
        if digest.hexdigest() != MODEL_SHA256: raise ValueError('Model checksum did not match')
        converter = build / 'venv'
        with progress.activity('Installing temporary conversion tools', log):
            spawn([interpreter, '-m', 'venv', str(converter)], check=True)
            conversion_python = venv_python(converter)
            tensorflow = 'tensorflow-cpu==2.16.2' if sys.platform.startswith('linux') else 'tensorflow==2.16.2'
            spawn([str(conversion_python), '-m', 'pip', 'install', '--quiet', '--no-cache-dir',
                   tensorflow, 'keras==3.3.3'], check=True)
        env = os.environ.copy()
        env.update({'CUDA_VISIBLE_DEVICES':'-1', 'OMP_NUM_THREADS':'2', 'OPENBLAS_NUM_THREADS':'1',
                    'TF_NUM_INTRAOP_THREADS':'2', 'TF_NUM_INTEROP_THREADS':'1', 'TF_CPP_MIN_LOG_LEVEL':'3'})
        model = temporary / 'nba-ads-v1.tflite'
        with progress.activity('Optimizing the model locally', log):
            spawn([str(conversion_python), str(EXPORTER), str(source), str(model)], env=env, check=True)
        if not model.is_file() or not 0 < model.stat().st_size <= 25_000_000:
            raise ValueError('Converted model has an unexpected size')
        shutil.rmtree(build)
        with progress.activity('Installing the isolated detection runtime', log):
            spawn([interpreter, '-m', 'venv', str(temporary / 'venv')], check=True)
            python = venv_python(temporary / 'venv')
            runtime = ('tensorflow==2.16.2' if intel_mac else
                       'ai-edge-litert==1.4.0' if python_version == (3, 9) else 'ai-edge-litert==2.3.0')
            spawn([str(python), '-m', 'pip', 'install', '--quiet', '--no-cache-dir', runtime, 'Pillow>=10,<12'], check=True)
        check = spawn([str(python), '-u', str(WORKER), str(model)], input='', capture_output=True,
                       text=True, env=env, timeout=60, check=True)
        if not json.loads(check.stdout.splitlines()[0]).get('ready'):
            raise ValueError('The installed detector could not load the model')
        # Use the venv Python directly; do not rely on relocation-sensitive pip scripts.
        os.rename(temporary, root)
        command = os.environ.get('SPORTS_CLI_COMMAND') or './sports-cli'
        say(f'Installed. Run "{command} --ai-ad-observe" to preview it, or "{command} --ai-ad-mute" to mute ads.')
        return 0
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        say(f'Optional AI detector installation failed: {_captured_text(exc)}', error=True)
        return 1
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


class Decision:
    """Require repeated evidence and release mute if predictions stay uncertain."""
    def __init__(self):
        self.ad = False
        self.ads = self.games = self.uncertain = 0

    def update(self, score):
        if score <= .2:
            self.ads += 1; self.games = self.uncertain = 0
            if self.ads >= 2: self.ad = True
        elif score >= .8:
            self.games += 1; self.ads = self.uncertain = 0
            # Returning coverage should be audible on the first strong frame.
            self.ad = False
        else:
            self.uncertain += 1; self.ads = self.games = 0
            if self.uncertain >= 3: self.ad = False
        return self.ad


class PlaybackGate:
    """Count evidence only when the player is running and its timeline changes."""
    def __init__(self):
        self.position = None
        self.sampled_at = None
        self.discontinuity = False

    def accept(self, paused, position):
        self.discontinuity = False
        if paused is not False or isinstance(position, bool) or not isinstance(position, (int, float)):
            return False
        if not math.isfinite(position): return False
        if self.position is not None and abs(position - self.position) < .05: return False
        now = time.monotonic()
        if self.position is not None:
            elapsed = now - self.sampled_at
            difference = position - self.position
            self.discontinuity = difference < 0 or difference > max(5, elapsed * 3 + 1)
        self.position = position
        self.sampled_at = now
        return True


class WorkerBusy(OSError):
    """The detector has not answered yet; it may still answer late."""


class Monitor:
    # A frame that takes longer is skipped; only a detector silent this long is given up on.
    frame_timeout, late_limit = 3, 30

    def __init__(self, socket_path, directory, python=None, model=None, interval=2, mute=True, on_break=None):
        default_python, default_model = installation_paths()
        self.path, self.directory = socket_path, pathlib.Path(directory)
        self.python, self.model = pathlib.Path(python or default_python), pathlib.Path(model or default_model)
        self.interval = interval
        self.mute = mute
        # Called as on_break(active, command) when a muted break starts or ends.
        self.on_break = on_break
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.process = None
        self.replies = queue.Queue()

    def start(self): self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.process and self.process.poll() is None:
            try: self.process.terminate()
            except ProcessLookupError: pass
        if self.thread.is_alive(): self.thread.join(timeout=3)
        if self.process and self.process.poll() is None:
            try: self.process.kill(); self.process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired): pass

    def _collect_replies(self):
        # A reader thread, because Windows cannot wait on a pipe with select.
        for line in self.process.stdout: self.replies.put(line)
        self.replies.put(None)

    def _read_worker(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.stopped.is_set() and time.monotonic() < deadline:
            try: line = self.replies.get(timeout=.2)
            except queue.Empty: continue
            if line is None: raise OSError('AI detector exited')
            value = json.loads(line)
            if isinstance(value, dict): return value
        raise WorkerBusy('AI detector did not respond')

    def run(self):
        connection = None; state = MuteState(); command = None; on_break = False
        image = self.directory / 'current-frame.jpg'
        try:
            env = os.environ.copy()
            env.update({'CUDA_VISIBLE_DEVICES':'-1', 'OMP_NUM_THREADS':'2', 'OPENBLAS_NUM_THREADS':'1',
                        'TF_NUM_INTRAOP_THREADS':'2', 'TF_NUM_INTEROP_THREADS':'1', 'TF_CPP_MIN_LOG_LEVEL':'3'})
            self.process = subprocess.Popen([str(self.python), '-u', str(WORKER), str(self.model)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            threading.Thread(target=self._collect_replies, daemon=True).start()
            connection = ad_mute.connect(self.path, 2, self.stopped)
            if connection is None: return
            reader = connection.reader; request_id = 0; playback_epoch = 0
            def invoke(*args):
                nonlocal request_id, playback_epoch
                request_id += 1
                connection.send((json.dumps({'command':args, 'request_id':request_id})+'\n').encode())
                deadline = time.monotonic() + 2
                # Cleanup must still be able to use IPC after stop() is requested.
                while time.monotonic() < deadline:
                    raw = reader.readline(100_000)
                    if not raw: raise OSError('mpv disconnected')
                    response = json.loads(raw)
                    if response.get('event') in ('seek', 'start-file', 'file-loaded', 'end-file'):
                        playback_epoch += 1
                    if response.get('request_id') == request_id: return response
                raise OSError('mpv did not respond')
            command = invoke
            if not self._read_worker(30).get('ready'): raise OSError('AI detector failed to load')
            command('show-text', 'Local NBA ad detection active' if self.mute else 'Ad detection preview: audio unchanged', 2000)
            decision = Decision(); playback = PlaybackGate(); failures = 0; seen_epoch = playback_epoch
            manual_override = False; late_since = None
            def sync_break():
                nonlocal on_break
                if not self.on_break or state.in_ad == on_break: return
                on_break = state.in_ad
                try: self.on_break(on_break, command)
                except (OSError, ValueError, TypeError): pass
            def reset_detection():
                nonlocal state, decision, manual_override
                if state.applied:
                    muted = command('get_property', 'mute')
                    if muted.get('error') != 'success' or not isinstance(muted.get('data'), bool):
                        raise OSError('Could not read the player mute state')
                    if not muted['data']: manual_override = True
                    if muted['data'] and command('set_property', 'mute', False).get('error') != 'success':
                        raise OSError('Could not restore the player mute state')
                state = MuteState(); decision = Decision()
                sync_break()
            def unavailable_frame():
                nonlocal failures
                failures += 1
                reset_detection()
                if failures == 3:
                    command('show-text', 'Ad detection paused: frames unavailable', 3000)
            while not self.stopped.is_set():
                started = time.monotonic()
                if late_since is not None:
                    # Wait for the slow frame's reply, so replies keep pairing with their frames.
                    try: self._read_worker(self.interval)
                    except WorkerBusy:
                        if self.stopped.is_set() or time.monotonic() - late_since > self.late_limit: raise
                        unavailable_frame(); continue
                    late_since = None
                paused = command('get_property', 'pause')
                position = command('get_property', 'time-pos')
                if playback_epoch != seen_epoch:
                    reset_detection(); playback = PlaybackGate(); manual_override = False
                    seen_epoch = playback_epoch
                if not playback.accept(paused.get('data') if paused.get('error') == 'success' else None,
                                       position.get('data') if position.get('error') == 'success' else None):
                    reset_detection()
                    self.stopped.wait(self.interval); continue
                if playback.discontinuity:
                    reset_detection(); manual_override = False
                frame_epoch = playback_epoch
                frame_position = position['data']
                captured_at = time.monotonic()
                image.unlink(missing_ok=True)
                result = command('screenshot-to-file', str(image), 'video')
                if result.get('error') != 'success' or not image.exists():
                    unavailable_frame()
                    self.stopped.wait(self.interval); continue
                self.process.stdin.write(json.dumps({'image':str(image)})+'\n'); self.process.stdin.flush()
                try: response = self._read_worker(self.frame_timeout)
                except WorkerBusy:
                    # One slow frame, for example while the computer is busy, is no reason to stop for the game.
                    if self.stopped.is_set(): raise
                    image.unlink(missing_ok=True); late_since = time.monotonic(); continue
                image.unlink(missing_ok=True)
                score = response.get('score')
                if isinstance(score, bool) or not isinstance(score, (int,float)) or not 0 <= score <= 1:
                    unavailable_frame()
                    self.stopped.wait(self.interval); continue
                # mpv may have sought, changed files, or paused while inference ran.
                current_pause = command('get_property', 'pause')
                current_position = command('get_property', 'time-pos')
                current = current_position.get('data')
                elapsed = time.monotonic() - captured_at
                if (self.stopped.is_set() or playback_epoch != frame_epoch or
                    current_pause.get('error') != 'success' or current_pause.get('data') is not False or
                    current_position.get('error') != 'success' or isinstance(current, bool) or
                    not isinstance(current, (int, float)) or not math.isfinite(current) or
                    current < frame_position or current - frame_position > max(2, elapsed * 3 + 1)):
                    reset_detection(); playback = PlaybackGate(); manual_override = False
                    seen_epoch = playback_epoch
                    self.stopped.wait(self.interval); continue
                failures = 0
                was_ad = decision.ad
                in_ad = decision.update(score)
                if score >= .8: manual_override = False
                if self.mute:
                    muted = command('get_property', 'mute')
                    muted = muted.get('data') if muted.get('error') == 'success' else None
                    if not isinstance(muted, bool): raise OSError('Could not read the player mute state')
                    if self.stopped.is_set() or playback_epoch != frame_epoch:
                        reset_detection(); playback = PlaybackGate(); manual_override = False
                        seen_epoch = playback_epoch
                        continue
                    if state.applied and not muted: manual_override = True
                    previous = state.in_ad, state.applied
                    action = state.update(in_ad and not manual_override, muted)
                    if action is not None and command('set_property', 'mute', action).get('error') != 'success':
                        state.in_ad, state.applied = previous
                        raise OSError('Could not change the player mute state')
                    if self.stopped.is_set() or playback_epoch != frame_epoch:
                        # A seek can arrive with the mute command's reply. Undo
                        # a newly applied mute before waiting or sampling again.
                        reset_detection(); playback = PlaybackGate(); manual_override = False
                        seen_epoch = playback_epoch
                        continue
                    sync_break()
                if in_ad != was_ad:
                    label = ('Possible ad: auto-mute' if self.mute else 'Possible ad: audio unchanged') if in_ad else 'Broadcast detected'
                    command('show-text', label, 2000)
                self.stopped.wait(max(0, self.interval-(time.monotonic()-started)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
            if command and not self.stopped.is_set():
                try: command('show-text', 'Local ad detector stopped', 3000)
                except (OSError, ValueError): pass
        finally:
            if self.on_break and on_break:
                try: self.on_break(False, command)
                except (OSError, ValueError, TypeError): pass
            # Never strand a player muted if detection fails while it is still open.
            if command and state.applied:
                try:
                    muted = command('get_property', 'mute')
                    if muted.get('data') is True: command('set_property', 'mute', False)
                except (OSError, ValueError): pass
            try: image.unlink(missing_ok=True)
            except OSError: pass
            if connection:
                try: connection.close()
                except OSError: pass
            if self.process:
                if self.process.poll() is None:
                    try: self.process.terminate()
                    except ProcessLookupError: pass
                try: self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try: self.process.kill(); self.process.wait()
                    except ProcessLookupError: pass
                for stream in (self.process.stdin, self.process.stdout):
                    if stream:
                        try: stream.close()
                        except OSError: pass
