"""Guest-only real IPC driver. The parent mounts only copied fixture inputs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path('/lab')
WRAPPER = '/usr/share/omarchy/bin/omarchy-shell'


def run():
    # Refuse direct execution; a private marker and mount environment are
    # provided only by run_profiles.py's fresh networkless Bubblewrap command.
    if (os.environ.get('OMABIT_THEME_PROFILE_IPC') != 'private-qml-v1'
            or os.environ.get('HOME') != '/lab/home'
            or os.environ.get('XDG_RUNTIME_DIR') != '/lab/runtime'
            or not (ROOT / 'ipc-fixture-marker').is_file()
            or Path('/home').exists() or Path('/run/user').exists()):
        raise RuntimeError('private QML IPC namespace required')
    outcomes = []
    output = ROOT / 'ipc-shell.log'
    with output.open('wb') as stream:
        shell = subprocess.Popen(
            ['/usr/bin/quickshell', '--no-color', '--path', '/lab/shell/shell.qml'],
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            def call(target, method, require=True):
                reply = subprocess.run([WRAPPER, target, method],
                                       capture_output=True, text=True, timeout=4)
                if require and reply.returncode != 0:
                    raise AssertionError(target + '.' + method + ': ' + reply.stderr[-512:])
                return reply

            def snapshot(expected=None):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    assert shell.poll() is None, 'private shell exited'
                    reply = call('profileTest', 'snapshot', require=False)
                    if reply.returncode == 0:
                        value = json.loads(reply.stdout)
                        if all(row['ready'] for row in value.values()):
                            if expected is None or [value[k]['generation'] for k in ('stable', 'dev', 'edge')] == expected:
                                assert all(row['ships'] == 0 and row['error'] == '' for row in value.values())
                                return value
                    time.sleep(0.05)
                raise AssertionError('private IPC readiness deadline exceeded')

            before = snapshot([0, 0, 0])
            assert [before[k]['target'] for k in ('stable', 'dev', 'edge')] == ['urbit-theme', '', '']
            for target in ['urbit-theme.dev', 'urbit-theme.edge']:
                assert call(target, 'themeChanged', require=False).returncode != 0
            outcomes.append({'before_injection': before})
            assert call('profileTest', 'inject').stdout.strip() == 'injected'
            expected = [0, 0, 0]
            for index, target in enumerate(['urbit-theme', 'urbit-theme.dev', 'urbit-theme.edge']):
                assert call(target, 'themeChanged').stdout.strip() == 'queued'
                expected[index] += 1
                after = snapshot(expected)
                outcomes.append({'called': target, 'state': after})
            for index, suffix in enumerate(['', '.dev', '.edge']):
                theme_hook = '/lab/hooks/omarchy-urbit-theme' + suffix
                font_hook = '/lab/hooks/omarchy-urbit-font' + suffix
                for args in [[theme_hook], [font_hook, 'Fixture Mono']]:
                    reply = subprocess.run(['/usr/bin/bash', *args], capture_output=True, timeout=10)
                    assert (reply.returncode, reply.stdout, reply.stderr) == (0, b'', b'')
                # Hooks notify their helper only; explicit IPC above is separate.
                outcomes.append({'called_hook': suffix or 'stable', 'state': snapshot(expected)})
                before_notifications = (ROOT / 'hook-notifications.jsonl').read_bytes()
                reply = subprocess.run(['/usr/bin/bash', theme_hook], capture_output=True, timeout=5,
                                       env={**os.environ, 'OMARCHY_URBIT_THEME_REMOTE': '1'})
                assert reply.returncode == 0
                assert (ROOT / 'hook-notifications.jsonl').read_bytes() == before_notifications
            notifications = [json.loads(line) for line in (ROOT / 'hook-notifications.jsonl').read_text().splitlines()]
            assert notifications == [entry for suffix in ['', '.dev', '.edge'] for entry in [
                {'plugin': 'thelifeandtimes.urbit-theme' + suffix, 'args': ['notify', 'theme']},
                {'plugin': 'thelifeandtimes.urbit-theme' + suffix, 'args': ['notify', 'font', 'Fixture Mono']},
            ]]
            assert call('profileTest', 'clearDev').stdout.strip() == 'cleared'
            assert call('urbit-theme.dev', 'themeChanged', require=False).returncode != 0
            assert call('urbit-theme', 'themeChanged').stdout.strip() == 'queued'
            expected[0] += 1
            after = snapshot(expected)
            assert after['dev']['target'] == ''
            outcomes.append({'cleared_dev': after})
            assert call('profileTest', 'stop').stdout.strip() == 'stopping'
            assert shell.wait(timeout=3) == 0
        finally:
            if shell.poll() is None:
                os.killpg(shell.pid, signal.SIGTERM)
                try:
                    shell.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(shell.pid, signal.SIGKILL)
                    shell.wait(timeout=2)
    log = output.read_text()
    assert not any(marker in log for marker in ('ERROR', 'ReferenceError', 'TypeError', 'FIXTURE_STDERR'))
    (ROOT / 'ipc-evidence.json').write_text(json.dumps({
        'passed': True, 'calls': outcomes, 'shell_exit': shell.returncode,
        'real_ipc': True, 'real_backend': False, 'actual_compositor': False,
        'hook_notifications': notifications,
    }, indent=2) + '\n')
    print('THEME_PROFILE_IPC_PASS: separate stable/dev/edge hook delivery, late identity and removal')


if __name__ == '__main__':
    run()
