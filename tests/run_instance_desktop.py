#!/usr/bin/env python3
"""Qualify Theme instance files in a networkless HOME with inert desktop commands."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, work = args.source.resolve(), args.output.absolute()
    if (work.parent != Path('/tmp') or work.exists()
            or not re.fullmatch('omabit-theme-profile-hooks-check-[a-zA-Z0-9-]+', work.name)):
        raise RuntimeError('use a fresh /tmp/omabit-theme-profile-hooks-check-NAME')
    work.mkdir(mode=0o700)
    for name in ['home', 'runtime']:
        (work / name).mkdir(mode=0o700)
    command = [
        'bwrap', '--unshare-all', '--die-with-parent', '--clearenv',
        '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
        '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib', '/lib64',
        '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
        '--ro-bind', str(source), '/repo', '--bind', str(work), '/lab', '--chdir', '/repo',
    ]
    environment = {
        'HOME': '/lab/home', 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
        'XDG_RUNTIME_DIR': '/lab/runtime', 'PYTHONPATH': '/repo:/repo/tests',
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    for key, value in environment.items():
        command += ['--setenv', key, value]
    command += [
        '--', '/usr/bin/python3', '-B', '-m', 'unittest', '-v',
        'test_native.NativeHooksTests', 'test_profile.DesktopTests',
        'test_sync.CoordinatorTests', 'test_instance_desktop.InstanceFilesTests',
        'test_instance_desktop.AppearanceFilesTests', 'test_appearance_owner.AppearanceOwnerTests',
        'test_checkpoints.CheckpointTests',
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    (work / 'output.log').write_text(output)
    hashes = {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in ['client', 'hooks', 'tests'] for path in (source / folder).rglob('*')
        if path.is_file() and '__pycache__' not in path.parts
    }
    (work / 'evidence.json').write_text(json.dumps({
        'passed': result.returncode == 0, 'exit': result.returncode,
        'source': str(source), 'source_sha256': hashes,
        'networkless': True, 'private_home': True,
        'desktop_commands_inert': True, 'real_hook_scripts': True,
    }, indent=2) + '\n')
    print(output)
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
