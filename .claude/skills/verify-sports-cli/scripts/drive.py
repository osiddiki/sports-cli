#!/usr/bin/env python3
"""Drive ./sports-cli in a pseudo-terminal like a user and save each screen as evidence.

    drive.py [--app-args="--tv"] [--out DIR] [--mpv-seconds N] STEP...

Steps run in order:
    key:NAME        press a key: Enter Esc Tab Space Backspace Up Down Ctrl-F Ctrl-X, or one character
    type:TEXT       type text (letters only reach the app's own prompts and search)
    wait:SECONDS    let the app work
    expect:REGEX    wait up to 20 s for the screen to match; fail the run if it never does
    shot:LABEL      save the current screen to OUT/NN-LABEL.txt

Every run gets its own settings folder (XDG_CONFIG_HOME) so toggles never touch the
user's config, and its own mpv config folder that plays headless for --mpv-seconds
(default 8) and then exits, so playback can be proven without a window. The script
ends the app it started with q, then stops only that process if it is still running.
Exit status: 0 when every expect matched, 1 otherwise.
"""
import argparse
import datetime as dt
import os
import pathlib
import re
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[4]
VENV = pathlib.Path.home() / '.cache' / 'sports-cli-verify' / 'venv'
RUNS = pathlib.Path.home() / '.cache' / 'sports-cli-verify' / 'runs'
ROWS, COLUMNS = 30, 110

try:
    import pyte
except ImportError:
    # The terminal emulator lives in its own venv so the app's runtime stays standard-library only.
    python = VENV / 'bin' / 'python'
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(VENV)], check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-q', 'pyte==0.8.2'], check=True)
    os.execv(str(python), [str(python), __file__, *sys.argv[1:]])

KEYS = {'Enter': '\r', 'Esc': '\x1b', 'Tab': '\t', 'Space': ' ', 'Backspace': '\x7f', 'Up': 'k', 'Down': 'j',
        'Left': 'h', 'Right': 'l',
        'Ctrl-F': '\x06', 'Ctrl-X': '\x18'}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app-args', default='', help='arguments for ./sports-cli, e.g. "--tv"')
    parser.add_argument('--out', type=pathlib.Path, help=f'evidence folder (default: {RUNS}/<time>)')
    parser.add_argument('--mpv-seconds', type=int, default=8)
    parser.add_argument('steps', nargs='+')
    args = parser.parse_args()
    out = args.out or RUNS / dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=True)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix='sports-cli-verify-'))
    (scratch / 'mpv').mkdir()
    (scratch / 'mpv' / 'mpv.conf').write_text(f'vo=null\nao=null\nlength={args.mpv_seconds}\n')
    env = dict(os.environ, TERM='vt100', LINES=str(ROWS), COLUMNS=str(COLUMNS),
               XDG_CONFIG_HOME=str(scratch / 'config'), MPV_HOME=str(scratch / 'mpv'))
    for name in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy'):
        if name in env: print(f'note: {name} is set; a sandbox proxy can break stream hosts', file=sys.stderr)

    screen = pyte.Screen(COLUMNS, ROWS); stream = pyte.ByteStream(screen)
    pid, fd = os.forkpty()
    if pid == 0:
        os.chdir(REPO)
        os.execvpe('python3', ['python3', 'sports-cli', *shlex.split(args.app_args)], env)
    import fcntl, struct, termios
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', ROWS, COLUMNS, 0, 0))

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], .05)[0]:
                try: stream.feed(os.read(fd, 65536))
                except OSError: return

    def text():
        return '\n'.join(line.rstrip() for line in screen.display).rstrip() + '\n'

    shots = 0
    def save(label):
        nonlocal shots
        shots += 1
        path = out / f'{shots:02d}-{re.sub(r"[^A-Za-z0-9_.-]+", "-", label)}.txt'
        path.write_text(text(), encoding='utf-8')
        return path

    ok = True
    pump(3)
    try:
        for step in args.steps:
            kind, _, value = step.partition(':')
            if kind == 'key':
                os.write(fd, KEYS.get(value, value).encode()); pump(.4)
            elif kind == 'type':
                os.write(fd, value.encode()); pump(.4)
            elif kind == 'wait':
                pump(float(value))
            elif kind == 'shot':
                print('saved', save(value))
            elif kind == 'expect':
                deadline = time.time() + 20
                while time.time() < deadline and not re.search(value, text(), re.M): pump(.25)
                if re.search(value, text(), re.M):
                    print('matched', repr(value))
                else:
                    ok = False; print('NOT FOUND', repr(value), '->', save('failed-expect')); break
            else:
                raise SystemExit(f'unknown step {step!r}')
    finally:
        os.write(fd, b'q'); pump(1)
        if os.waitpid(pid, os.WNOHANG) == (0, 0):
            os.kill(pid, signal.SIGTERM); pump(.5)
            try: os.waitpid(pid, 0)
            except ChildProcessError: pass
        subprocess.run(['rm', '-rf', str(scratch)], check=False)
    print('evidence:', out)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
