#!/usr/bin/env python3
"""Walk ./sports-cli screen by screen from walk files and write a ledger of what each step showed.

    walk.py [--out DIR] [--mpv-seconds N] WALK...

A walk file has one step per line. Blank lines and lines starting with # are skipped.

    args --league nba          arguments for ./sports-cli (before the first step, optional)
    start REGEX                wait for the first screen
    key NAME [=> REGEX]        Enter Esc Tab Space Backspace Up Down Left Right Ctrl-F Ctrl-X, or one character
    type TEXT [=> REGEX]       type text into a prompt or search
    click TEXT [=> REGEX]      left-click the first cell showing TEXT, as a terminal would
    wait SECONDS

REGEX is searched line by line (^ and $ anchor a line); every screen line starts with a
space, so `^ Settings` matches the Settings heading and not a Home row. A REGEX that
already matches before a key, type or click fails that step: it would prove nothing.

Each walk starts its own app with its own settings and headless mpv, as drive.py does,
and stops at its first failed step. A click is sent only if the app has turned on mouse
reporting; otherwise a terminal sends nothing, and the step records that. Verdicts:
pass (REGEX showed), fail (it did not), no effect (a click left the heading and the
selected row unchanged), seen (no REGEX given), not run (after a failure).

Writes OUT/<walk>/ledger.md with one row per step, OUT/<walk>/NN.txt screens and
OUT/<walk>/verdicts.txt (step, verdict, action; diff two runs with it), plus OUT/summary.md. Exit status: 0 when no step failed, 1 otherwise.
"""
import argparse
import datetime as dt
import fcntl
import os
import pathlib
import re
import select
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

REPO = pathlib.Path(__file__).resolve().parents[4]
VENV = pathlib.Path.home() / '.cache' / 'sports-cli-verify' / 'venv'
RUNS = pathlib.Path.home() / '.cache' / 'sports-cli-verify' / 'walks'
ROWS, COLUMNS = 30, 110
TIMEOUT, SETTLE = 20, 1.5

try:
    import pyte
except ImportError:
    pyte = None
    if __name__ == '__main__':
        python = VENV / 'bin' / 'python'
        if not python.exists():
            subprocess.run([sys.executable, '-m', 'venv', str(VENV)], check=True)
            subprocess.run([str(python), '-m', 'pip', 'install', '-q', 'pyte==0.8.2'], check=True)
        os.execv(str(python), [str(python), __file__, *sys.argv[1:]])

KEYS = {'Enter': '\r', 'Esc': '\x1b', 'Tab': '\t', 'Space': ' ', 'Backspace': '\x7f', 'Up': 'k', 'Down': 'j',
        'Left': 'h', 'Right': 'l', 'Ctrl-F': '\x06', 'Ctrl-X': '\x18'}
MOUSE_MODES = {'1000', '1002', '1003', '1006'}


def click_report(modes, col, row):
    """A left press and release at a 1-based cell, as the terminal would send it."""
    if '1006' in modes:
        return f'\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m'.encode()
    # X10 puts each coordinate in one raw byte (32 + n), so it must not go through UTF-8.
    return b'\x1b[M' + bytes([32, 32 + col, 32 + row]) + b'\x1b[M' + bytes([35, 32 + col, 32 + row])


def parse(path):
    args, steps = '', []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'): continue
        action, _, rest = line.partition(' ')
        arg, _, expect = rest.partition(' => ')
        if action == 'args' and not steps: args = rest; continue
        if action not in ('start', 'key', 'type', 'click', 'wait'):
            raise SystemExit(f'{path}:{number}: unknown step {line!r}')
        if action == 'start': arg, expect = '', rest
        steps.append((action, arg.strip(), expect.strip() or None))
    return args, steps


class App:
    def __init__(self, app_args, mpv_seconds):
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix='sports-cli-walk-'))
        (self.scratch / 'mpv').mkdir()
        (self.scratch / 'mpv' / 'mpv.conf').write_text(f'vo=null\nao=null\nlength={mpv_seconds}\n')
        env = dict(os.environ, TERM='xterm', LINES=str(ROWS), COLUMNS=str(COLUMNS),
                   XDG_CONFIG_HOME=str(self.scratch / 'config'), MPV_HOME=str(self.scratch / 'mpv'))
        self.screen = pyte.Screen(COLUMNS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.raw = b''
        self.pid, self.fd = os.forkpty()
        if self.pid == 0:
            os.chdir(REPO)
            os.execvpe('python3', ['python3', 'sports-cli', *shlex.split(app_args)], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', ROWS, COLUMNS, 0, 0))

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], .05)[0]:
                try: data = os.read(self.fd, 65536)
                except OSError: return
                self.raw += data
                self.stream.feed(data)

    def send(self, data):
        try: os.write(self.fd, data if isinstance(data, bytes) else data.encode())
        except OSError: pass

    def text(self):
        return '\n'.join(line.rstrip() for line in self.screen.display).rstrip() + '\n'

    def shows(self, pattern):
        return bool(re.search(pattern, self.text(), re.M))

    def wait_for(self, pattern, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline and not self.shows(pattern): self.pump(.25)
        return self.shows(pattern)

    def mouse(self):
        on = set()
        for modes, flag in re.findall(rb'\x1b\[\?([\d;]+)([hl])', self.raw):
            for mode in modes.decode().split(';'):
                if mode in MOUSE_MODES: (on.add if flag == b'h' else on.discard)(mode)
        return on

    def click(self, target):
        for row, line in enumerate(self.screen.display, 1):
            col = line.find(target) + 1
            if col: break
        else:
            return None, f'{target!r} is not on screen'
        modes = self.mouse()
        if not modes:
            return False, f'row {row} col {col}; app did not turn on mouse reporting, so the terminal sent nothing'
        self.send(click_report(modes, col, row))
        return True, f'row {row} col {col}; sent a left click (mouse modes {",".join(sorted(modes))})'

    def view(self):
        lines = [line.rstrip() for line in self.screen.display]
        selected = next((line.strip()[2:].strip() for line in lines if line.lstrip().startswith('> ')), '')
        status = next((line.strip() for line in reversed(lines) if line.strip()), '')
        return lines[1].strip(), selected, status

    def close(self):
        self.send('q'); self.pump(1)
        if os.waitpid(self.pid, os.WNOHANG) == (0, 0):
            os.kill(self.pid, signal.SIGTERM); self.pump(.5)
            try: os.waitpid(self.pid, 0)
            except ChildProcessError: pass
        subprocess.run(['rm', '-rf', str(self.scratch)], check=False)


def cell(text):
    return text.replace('|', '\\|') if text else ''


def walk(path, out, mpv_seconds):
    app_args, steps = parse(path)
    out.mkdir(parents=True, exist_ok=True)
    app = App(app_args, mpv_seconds)
    rows, failed = [], False
    app.pump(3)
    try:
        for number, (action, arg, expect) in enumerate(steps, 1):
            label = f'{action} {arg}'.strip() if action != 'start' else 'start'
            if failed:
                rows.append((number, '', label, expect, '', 'not run', '')); continue
            before = app.view()
            stale = action in ('key', 'type', 'click') and expect and app.shows(expect)
            sent = True
            if action == 'key': app.send(KEYS.get(arg, arg))
            elif action == 'type': app.send(arg)
            elif action == 'wait': app.pump(float(arg))
            elif action == 'click':
                sent, note = app.click(arg)
                label += f' ({note})'
            app.pump(.4)
            if sent is None or stale:
                matched = False
            elif expect:
                matched = app.wait_for(expect, TIMEOUT if sent else SETTLE)
            else:
                app.pump(SETTLE if action != 'wait' else 0); matched = None
            heading, selected, status = after = app.view()
            shot = out / f'{number:02d}.txt'
            shot.write_text(app.text(), encoding='utf-8')
            if matched is None: verdict = 'seen'
            elif matched: verdict = 'pass'
            elif action == 'click' and sent is not None and not stale and after[:2] == before[:2]: verdict = 'no effect'
            else: verdict = 'fail'; failed = True
            showed = f'{heading} · selected: {selected or "none"} · status: {status}'
            if stale: showed = 'expected text was already on screen before this step, so it proves nothing · ' + showed
            rows.append((number, before[0], label, expect, showed, verdict, shot.name))
    finally:
        mouse = ','.join(sorted(app.mouse())) or 'none'
        app.close()
    commit = subprocess.run(['git', 'describe', '--always', '--dirty'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    rerun = shlex.join([str(pathlib.Path(__file__).relative_to(REPO)), str(path.resolve().relative_to(REPO))
                        if path.resolve().is_relative_to(REPO) else str(path)])
    lines = [f'# Walk: {path.stem}', '',
             f'- Commit: `{commit}`', f'- Rerun: `{rerun}`', f'- App arguments: `{app_args or "none"}`',
             f'- Started: {dt.datetime.now().astimezone().isoformat(timespec="seconds")}',
             f'- Mouse reporting on at the end of the walk: {mouse}',
             '', '| # | Screen | Action | Expected | Showed | Verdict | Shot |', '|---|---|---|---|---|---|---|']
    lines += [f'| {n} | {cell(s)} | {cell(a)} | {cell(e) and f"`{cell(e)}`"} | {cell(w)} | {v} | {f} |'
              for n, s, a, e, w, v, f in rows]
    (out / 'ledger.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (out / 'verdicts.txt').write_text(''.join(f'{n}\t{v}\t{a}\n' for n, s, a, e, w, v, f in rows), encoding='utf-8')
    return [row[5] for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=pathlib.Path, help=f'evidence folder (default: {RUNS}/<time>)')
    parser.add_argument('--mpv-seconds', type=int, default=8)
    parser.add_argument('walks', nargs='+', type=pathlib.Path)
    args = parser.parse_args()
    out = args.out or RUNS / dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    if any(name in os.environ for name in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy')):
        print('note: a proxy is set; network steps can fail for reasons outside the app', file=sys.stderr)
    summary = ['| Walk | Pass | Fail | No effect | Seen | Not run | Ledger |', '|---|---|---|---|---|---|---|']
    ok = True
    for path in args.walks:
        verdicts = walk(path, out / path.stem, args.mpv_seconds)
        ok &= 'fail' not in verdicts
        counts = [verdicts.count(v) for v in ('pass', 'fail', 'no effect', 'seen', 'not run')]
        summary.append(f'| {path.stem} | ' + ' | '.join(map(str, counts)) + f' | {path.stem}/ledger.md |')
        print(f'{path.stem}: ' + ', '.join(f'{c} {v}' for c, v in zip(counts, ('pass', 'fail', 'no effect', 'seen', 'not run')) if c))
    (out / 'summary.md').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    print('evidence:', out)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
