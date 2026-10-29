"""Inert copied hook target: record binding and arguments, with no backend imports."""
import json
import os
from pathlib import Path
import sys

assert os.environ.get('OMABIT_THEME_PROFILE_IPC') == 'private-qml-v1'
assert sys.argv[1:] == ['notify', 'theme'] or sys.argv[1:] == ['notify', 'font', 'Fixture Mono']
with Path('/lab/hook-notifications.jsonl').open('a') as stream:
    stream.write(json.dumps({'plugin': Path(__file__).resolve().parents[1].name,
                             'args': sys.argv[1:]}) + '\n')
