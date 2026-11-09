#!/usr/bin/env python3
"""Tabulate the council: score.py ROUND_DIR... prints each reviewer's scores per round and the lowest."""
import json
import pathlib
import re
import sys

CATEGORIES = ['findability', 'hierarchy', 'wording', 'feedback', 'consistency', 'polish', 'task_ease']


def scores(path):
    text = path.read_text(encoding='utf-8')
    found = None
    for match in re.finditer(r'\{\s*"scores"', text):
        depth = 0
        for end in range(match.start(), len(text)):
            depth += {'{': 1, '}': -1}.get(text[end], 0)
            if depth == 0:
                try: found = json.loads(text[match.start():end + 1])
                except ValueError: pass
                break
    return found and found['scores']


for folder in map(pathlib.Path, sys.argv[1:]):
    print(f'{folder.name:14}' + ' '.join(f'{c[:6]:>7}' for c in CATEGORIES) + '    min   mean')
    for review in sorted(folder.glob('review-*.md')):
        got = scores(review)
        if not got:
            print(f'  {review.stem[7:]:12} no JSON scores'); continue
        values = [float(got.get(c, 0)) for c in CATEGORIES]
        print(f'  {review.stem[7:]:12}' + ''.join(f'{v:7.1f}' for v in values) + f'{min(values):7.1f}{sum(values) / len(values):7.2f}')
