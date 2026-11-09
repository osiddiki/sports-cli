#!/usr/bin/env python3
"""Shoot every main screen of ./sports-cli as a beginner's Mac shows it, for the UI council.

    shoot.py OUT_DIR

Runs the real app in a pseudo-terminal at 80 x 24 with 256 colours (macOS Terminal's default
window), drives the flows below, and writes OUT_DIR/NN-name.png in Terminal's default white
"Basic" profile, the same screen as NN-name.txt, and flows.md, which lists the keys pressed
between shots so reviewers can walk each task. Live data: shoot every version you compare in
the same sitting.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

WALK = pathlib.Path(__file__).resolve().parents[2] / 'walk-sports-cli' / 'scripts'
sys.path.insert(0, str(WALK))
import walk  # noqa: E402  (re-executes in the verify venv when pyte is missing)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pillow'], check=True)
    from PIL import Image, ImageDraw, ImageFont

import pyte  # noqa: E402

ROWS, COLUMNS = 24, 80
# macOS Terminal "Basic" profile: black on white, its standard ANSI colours.
BACKGROUND, FOREGROUND = (255, 255, 255), (0, 0, 0)
ANSI = {'black': (0, 0, 0), 'red': (153, 0, 0), 'green': (0, 166, 0), 'brown': (153, 153, 0),
        'blue': (0, 0, 178), 'magenta': (178, 0, 178), 'cyan': (0, 166, 178), 'white': (191, 191, 191)}

# (name, key before the shot or None, seconds to let it settle, regex that must show first or None)
FLOWS = {
    'Watch a live game from Home': [
        ('home', None, 3, None),
        ('sources-live-game', 'Enter', 14, r'^ Streams'),
    ],
    'Reach the menu, then Live TV and a channel': [
        ('home-menu', 'Up', 1, None),
        ('menu-live-tv', 'Right Right Right', 1, None),
        ('live-tv', 'Enter', 6, r'^ Live TV'),
        ('channel-sources', 'Enter', 14, r'^ Streams'),
    ],
    "One sport's schedule": [
        ('choose-sport', 'Up Right Enter', 1, r'choose a sport'),
        ('schedule', 'Enter', 6, r'^ Games'),
    ],
    'Replays': [
        ('menu-replays', 'Up Right Right', 1, None),
        ('replays-choose-sport', 'Enter', 1, r'choose a sport'),
        ('replays-list', 'Enter', 12, r'^ Replays(?!:)'),
    ],
    'Search for a team': [
        ('search-empty', '/', 1, r'^ Search'),
        ('search-lakers', 'lakers', 6, None),
    ],
    'Settings and help': [
        ('settings', ',', 1, r'^ Settings'),
        ('help', '?', 1, r'^ Help'),
    ],
}


class Screen(pyte.Screen):
    """pyte drops SGR 2 (dim) and the scroll codes SU and SD; curses uses all three, and without them old
    lines stay on screen. Dim becomes a colour marker; scrolling moves the lines inside the margins."""

    def scroll_up(self, count=None):
        self._scroll(self.delete_lines, count)

    def scroll_down(self, count=None):
        self._scroll(self.insert_lines, count)

    def _scroll(self, move, count):
        top = self.margins.top if self.margins else 0
        x, y = self.cursor.x, self.cursor.y
        self.cursor.y = top
        move(count or 1)
        self.cursor.x, self.cursor.y = x, y

    def select_graphic_rendition(self, *attrs, private=False):
        dim = 2 in attrs
        normal = 22 in attrs or 0 in attrs or not attrs
        super().select_graphic_rendition(*[a for a in attrs if a != 2])
        fg = self.cursor.attrs.fg
        if dim and not fg.startswith('dim:'):
            self.cursor.attrs = self.cursor.attrs._replace(fg='dim:' + fg)
        elif normal and fg.startswith('dim:') and not dim:
            self.cursor.attrs = self.cursor.attrs._replace(fg=fg[4:])


class Stream(pyte.ByteStream):
    csi = dict(pyte.ByteStream.csi, S='scroll_up', T='scroll_down')


class App(walk.App):
    def __init__(self):
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix='sports-cli-council-'))
        (self.scratch / 'mpv').mkdir()
        (self.scratch / 'mpv' / 'mpv.conf').write_text('vo=null\nao=null\nlength=3\n')
        env = dict(os.environ, TERM='xterm-256color', LINES=str(ROWS), COLUMNS=str(COLUMNS),
                   XDG_CONFIG_HOME=str(self.scratch / 'config'), MPV_HOME=str(self.scratch / 'mpv'))
        self.screen = Screen(COLUMNS, ROWS)
        self.stream = Stream(self.screen)
        self.raw = b''
        self.pid, self.fd = os.forkpty()
        if self.pid == 0:
            os.chdir(walk.REPO)
            os.execvpe('python3', ['python3', 'sports-cli'], env)
        walk.fcntl.ioctl(self.fd, walk.termios.TIOCSWINSZ, walk.struct.pack('HHHH', ROWS, COLUMNS, 0, 0))


def colour(name, default):
    dim = name.startswith('dim:')
    name = name[4:] if dim else name
    if name == 'default': rgb = default
    elif name in ANSI: rgb = ANSI[name]
    elif len(name) == 6: rgb = tuple(int(name[i:i + 2], 16) for i in (0, 2, 4))
    else: rgb = default
    if dim: rgb = tuple((c + b) // 2 for c, b in zip(rgb, BACKGROUND))
    return rgb


def render(screen, path):
    regular = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 26, index=0)
    bold = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 26, index=1)
    braille = ImageFont.truetype('/System/Library/Fonts/Apple Braille.ttf', 26)
    width, height = 16, 32
    image = Image.new('RGB', (COLUMNS * width + 24, ROWS * height + 24), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for y in range(ROWS):
        for x in range(COLUMNS):
            char = screen.buffer[y][x]
            fg, bg = colour(char.fg, FOREGROUND), colour(char.bg, BACKGROUND)
            if char.reverse: fg, bg = bg, fg
            left, top = 12 + x * width, 12 + y * height
            if bg != BACKGROUND: draw.rectangle([left, top, left + width, top + height], fill=bg)
            # Menlo has no braille, which the loading spinner uses; Terminal falls back to Apple Braille.
            font = braille if '\u2800' <= char.data <= '\u28ff' else bold if char.bold else regular
            if char.data.strip(): draw.text((left, top + 3), char.data, fill=fg, font=font)
            if char.underscore: draw.line([left, top + height - 3, left + width, top + height - 3], fill=fg, width=2)
    image.save(path)


def main():
    out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    notes, number = ['# Flows', '', 'Each flow starts in a freshly opened app, on Home.', ''], 0
    for flow, steps in FLOWS.items():
        notes += [f'## {flow}', '']
        app = App()
        try:
            app.wait_for(r'LIVE|LATER|No games', 20)
            for name, key, settle, expect in steps:
                if key:
                    keys = key.split(' ') if key in walk.KEYS or len(key) == 1 or ' ' in key else None
                    for part in keys or [key]: app.send(walk.KEYS.get(part, part) if keys else part); app.pump(.3)
                    notes.append(f'- pressed `{key}`' if keys else f'- typed `{key}`')
                if expect: app.wait_for(expect, 20)
                app.pump(settle)
                number += 1
                stem = f'{number:02d}-{name}'
                (out / f'{stem}.txt').write_text(app.text(), encoding='utf-8')
                render(app.screen, out / f'{stem}.png')
                notes.append(f'- shot `{stem}.png`')
        finally:
            app.close()
        notes.append('')
    (out / 'flows.md').write_text('\n'.join(notes), encoding='utf-8')
    print(f'{number} shots in {out}')


if __name__ == '__main__':
    main()
