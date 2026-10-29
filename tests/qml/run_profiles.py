#!/usr/bin/env python3
"""Check Theme profile routing using real QML, native controls and inert I/O.

Only a fresh Bubblewrap namespace may run the fixture. The real IPC phase uses
an inert fixture helper, never the plugin backend. It has no network,
production HOME, desktop bus, compositor sockets or Python plugin helper.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHELL = Path('/usr/share/omarchy/shell')
UI = [
    'Panel', 'PanelController', 'BarWidget', 'BarIconButton', 'WidgetButton',
    'OpticalGlyph', 'Button', 'TextField', 'ToggleSwitch', 'PanelHero',
    'PanelSeparator', 'PanelSectionHeader', 'BorderSurface', 'BorderOverlay',
    'PanelKeyCatcher', 'PanelActionButton', 'PanelToolTip',
]
COMMONS = ['Style.qml', 'Util.qml', 'Border.qml', 'BorderGeometry.js']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    runner = Path('/usr/lib/qt6/bin/qmltestrunner')
    native_inputs = [
        *[SHELL / 'Ui' / (name + '.qml') for name in UI],
        *[SHELL / 'Commons' / name for name in COMMONS],
    ]
    sdk_inputs = [Path('/usr/bin/quickshell'), Path('/usr/bin/qs'),
                  Path('/usr/share/omarchy/bin/omarchy-shell')]
    for path in [runner, *native_inputs, *sdk_inputs]:
        if not path.is_file():
            raise RuntimeError('missing QML prerequisite: ' + str(path))
    if not shutil.which('bwrap'):
        raise RuntimeError('Bubblewrap is required; no host fallback')

    work = args.output.absolute()
    if (work.parent != Path('/tmp')
            or not re.fullmatch('omabit-theme-profile-qml-[a-zA-Z0-9-]+', work.name)
            or work.exists()):
        raise RuntimeError('use a fresh /tmp/omabit-theme-profile-qml-NAME')
    work.mkdir(mode=0o700)
    for name in ['home', 'runtime', 'cache', 'tests']:
        (work / name).mkdir(mode=0o700)
    (work / 'shell').mkdir()
    (work / 'hooks').mkdir()
    (work / 'ipc-fixture-marker').write_text('private-qml-v1\n')
    fixture = ROOT / 'tests/qml'
    shutil.copytree(fixture / 'imports', work / 'imports')
    for name in UI:
        shutil.copy2(SHELL / 'Ui' / (name + '.qml'),
                     work / 'imports/qs/Ui' / (name + '.qml'))
    (work / 'imports/qs/Ui/qmldir').write_text(
        'module qs.Ui\n' + ''.join(
            name + ' 1.0 ' + name + '.qml\n'
            for name in [*UI, 'KeyboardPanel']))
    for name in COMMONS:
        shutil.copy2(SHELL / 'Commons' / name, work / 'imports/qs/Commons' / name)

    source_hashes = {}
    for profile in ['stable', 'dev', 'edge']:
        target = work / profile
        target.mkdir()
        for path in [*sorted(source.glob('*.qml')),
                     *sorted(source.glob('*.js'))]:
            contents = path.read_text()
            source_hashes[path.name] = digest(path)
            (target / path.name).write_text(contents)
        (target / 'client').mkdir()
        shutil.copy2(fixture / 'helper.py', target / 'client/main.py')
        sdk = work / 'shell' / profile
        (sdk / 'client').mkdir(parents=True)
        for name in ['Service.qml', 'Model.js']:
            shutil.copy2(source / name, sdk / name)
        shutil.copy2(fixture / 'helper.py', sdk / 'client/main.py')
        suffix = '' if profile == 'stable' else '.' + profile
        plugin = work / 'home/.config/omarchy/plugins' / ('thelifeandtimes.urbit-theme' + suffix)
        (plugin / 'client').mkdir(parents=True)
        shutil.copy2(fixture / 'profile_notify.py', plugin / 'client/sync.py')
        for name in ['omarchy-urbit-theme', 'omarchy-urbit-font']:
            shutil.copy2(source / 'hooks' / name, work / 'hooks' / (name + suffix))
            source_hashes['hooks/' + name] = digest(source / 'hooks' / name)
    shutil.copy2(fixture / 'tst_profiles.qml', work / 'tests/tst_profiles.qml')
    legacy = work / 'stable/tests/qml'
    legacy.mkdir(parents=True)
    shutil.copy2(fixture / 'tst_plugin.qml', legacy / 'tst_plugin.qml')
    shutil.copy2(fixture / 'profile-ipc.qml', work / 'shell/shell.qml')
    shutil.copy2(fixture / 'profile_ipc.py', work / 'profile_ipc.py')
    inputs = {str(path): digest(path) for path in [runner, *native_inputs, *sdk_inputs]}
    inputs.update({str(path): digest(path) for path in sorted(fixture.rglob('*'))
                   if path.is_file() and '__pycache__' not in path.parts})

    command = [
        'bwrap', '--unshare-all', '--die-with-parent', '--clearenv',
        '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
        '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib', '/lib64',
        '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/etc',
        '--ro-bind', '/etc/fonts', '/etc/fonts', '--bind', str(work), '/lab',
        '--chdir', '/lab',
    ]
    environment = {
        'HOME': '/lab/home', 'XDG_RUNTIME_DIR': '/lab/runtime',
        'XDG_CACHE_HOME': '/lab/cache', 'PATH': '/usr/share/omarchy/bin:/usr/bin:/bin',
        'LANG': 'C.UTF-8', 'QT_QPA_PLATFORM': 'offscreen',
        'QT_QUICK_BACKEND': 'software', 'QML_DISABLE_DISK_CACHE': '1',
        'QT_QUICK_CONTROLS_STYLE': 'Basic', 'QT_QPA_PLATFORMTHEME': '',
        'XDG_CONFIG_HOME': '/lab/config', 'XDG_DATA_HOME': '/lab/data',
        'XDG_STATE_HOME': '/lab/state', 'OMARCHY_PATH': '/lab',
        'OMABIT_THEME_PROFILE_IPC': 'private-qml-v1',
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    for key, value in environment.items():
        command += ['--setenv', key, value]
    results = []
    for input_path in ['/lab/tests', '/lab/stable/tests/qml']:
        check = command + ['--', str(runner), '-input', input_path, '-import', '/lab/imports']
        results.append(subprocess.run(check, capture_output=True, text=True, timeout=45))
    output = ''.join(result.stdout + result.stderr for result in results)
    (work / 'output.log').write_text(output)
    passed = all(result.returncode == 0 for result in results) and not any(
        marker in output for marker in ('QWARN', 'ReferenceError', 'TypeError', 'FAIL!'))
    ipc_exit = None
    if passed:
        ipc = subprocess.run(command + ['--', '/usr/bin/python3', '-B', '/lab/profile_ipc.py'],
                             capture_output=True, text=True, timeout=45)
        ipc_exit = ipc.returncode
        (work / 'ipc-output.log').write_text(ipc.stdout + ipc.stderr)
        print(ipc.stdout + ipc.stderr)
        passed = ipc_exit == 0 and 'THEME_PROFILE_IPC_PASS' in ipc.stdout
    (work / 'evidence.json').write_text(json.dumps({
        'passed': passed, 'exit': [result.returncode for result in results], 'source': str(source),
        'source_sha256': source_hashes, 'fixture_native_sha256': inputs,
        'no_network_or_host_home': True, 'qml_inert_io': True,
        'actual_compositor': False, 'ipc_exit': ipc_exit,
        'ipc_uses_inert_helper': True,
    }, indent=2) + '\n')
    print(output)
    print('Evidence:', work)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
