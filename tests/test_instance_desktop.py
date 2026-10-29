"""Instance-owned hook and appearance files; all desktop effects are inert."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from client.instance import CANONICAL_ID, load_instance
from client.native import HOOKS, ensure_hooks
from client.appearance import Ownership
from client.profile import Desktop
from client.support import Failure
from client.sync import notify
import test_profile as fixtures
from test_profile import profile

ROOT = Path(__file__).resolve().parents[1]


class InstanceFilesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.calls = []
        self.instances = {}
        self.sources = {}
        for name in ['default', 'dev', 'edge']:
            suffix = '' if name == 'default' else '.' + name
            source = self.home / '.config/omarchy/plugins' / (CANONICAL_ID + suffix)
            (source / '.git').mkdir(parents=True)
            (source / 'hooks').mkdir()
            (source / 'manifest.json').write_text(json.dumps({'id': CANONICAL_ID + suffix}))
            (source / 'application-profile.json').write_text(json.dumps({'version': 1, 'profile': name}))
            for _, hook in HOOKS:
                shutil.copyfile(ROOT / 'hooks' / hook, source / 'hooks' / hook)
            self.instances[name] = load_instance(source)
            self.sources[name] = source

    def state(self, name):
        return self.root / 'state' / self.instances[name].state_name

    def install(self, name, runner=None):
        return ensure_hooks(self.sources[name], self.home, self.state(name), runner or self.runner,
                            instance=self.instances[name])

    def runner(self, args, **kwargs):
        self.calls.append(args)
        event, source = args[3], Path(args[4])
        target = self.home / '.config/omarchy/hooks' / (event + '.d') / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return 0, b''

    def hook(self, name, kind='theme'):
        suffix = '' if name == 'default' else '.' + name
        return self.home / '.config/omarchy/hooks' / (kind + '-set.d') / ('omarchy-urbit-' + kind + suffix)

    def test_three_installations_are_distinct_and_restart_is_idempotent(self):
        for name in self.instances:
            self.assertTrue(self.install(name))
        before = {name: self.hook(name).read_bytes() for name in self.instances}
        self.assertEqual(len(self.calls), 6)
        for name in self.instances:
            self.assertTrue(self.install(name))
            self.assertEqual(before[name], self.hook(name).read_bytes())
            suffix = '' if name == 'default' else '.' + name
            receipt = json.loads((self.state(name) / 'native-hooks.json').read_text())
            self.assertEqual(set(receipt['hooks']), {event + '/' + hook + suffix for event, hook in HOOKS})
            self.assertEqual(list(self.state(name).glob('.native-hooks-*')), [])
        self.assertEqual(len(self.calls), 6)

    def test_dev_conflict_preserves_stable_and_refuses_all_dev_effects(self):
        self.install('default')
        before = self.hook('default').read_bytes()
        target = self.hook('dev', 'font')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('user customization')
        with self.assertRaises(Failure):
            self.install('dev')
        self.assertEqual(self.hook('default').read_bytes(), before)
        self.assertEqual(target.read_text(), 'user customization')
        self.assertFalse(self.hook('dev').exists())
        self.assertEqual(len(self.calls), 2)

    def test_dev_partial_install_resumes_without_reinstalling_other_instances(self):
        self.install('default')
        def failing(args, **kwargs):
            return (1, b'') if args[3] == 'font-set' else self.runner(args, **kwargs)
        with self.assertRaises(Failure):
            self.install('dev', failing)
        self.install('dev')
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(self.hook('dev', 'font').is_file())
        self.assertEqual(list(self.state('dev').glob('.native-hooks-*')), [])

    def test_misbound_receipt_and_source_are_refused_without_writes(self):
        self.install('default')
        self.state('dev').mkdir(parents=True)
        shutil.copyfile(self.state('default') / 'native-hooks.json', self.state('dev') / 'native-hooks.json')
        with self.assertRaises(Failure):
            self.install('dev')
        self.assertFalse(self.hook('dev').exists())
        self.assertFalse(ensure_hooks(self.sources['default'], self.home, self.state('edge'),
                                     self.runner, instance=self.instances['edge']))
        self.assertEqual(len(self.calls), 2)

    def test_real_hook_scripts_select_only_their_helper_without_ipc(self):
        log = self.root / 'calls.jsonl'
        binaries = self.root / 'bin'; binaries.mkdir()
        shim = binaries / 'omarchy-shell'
        shim.write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\nwith open(os.environ["CALL_LOG"],"a") as f:f.write(json.dumps({"ipc":sys.argv[1:]})+"\\n")\n')
        shim.chmod(0o755)
        for name in self.instances:
            self.install(name)
            helper = self.sources[name] / 'client/sync.py'; helper.parent.mkdir()
            helper.write_text('import json,os,sys\nwith open(os.environ["CALL_LOG"],"a") as f:f.write(json.dumps({"helper":'+repr(name)+',"args":sys.argv[1:]})+"\\n")\n')
            env = {'HOME': str(self.home), 'PATH': str(binaries) + ':/usr/bin:/bin',
                   'CALL_LOG': str(log), 'LANG': 'C.UTF-8'}
            for kind in ['theme', 'font']:
                args = ['/usr/bin/bash', str(self.hook(name, kind))] + (['Fixture Mono'] if kind == 'font' else [])
                result = subprocess.run(args, env=env, capture_output=True, timeout=10)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
            count = len(log.read_text().splitlines())
            result = subprocess.run(['/usr/bin/bash', str(self.hook(name))],
                                    env={**env, 'OMARCHY_URBIT_THEME_REMOTE': '1'}, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(len(log.read_text().splitlines()), count)
        before = log.read_bytes()
        for suffix in ['.default', '.Dev', '.dev.extra', '.' + 'a' * 33]:
            invalid = self.root / ('omarchy-urbit-theme' + suffix)
            shutil.copyfile(ROOT / 'hooks/omarchy-urbit-theme', invalid)
            result = subprocess.run(['/usr/bin/bash', str(invalid)], env=env,
                                    capture_output=True, timeout=5)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
        self.assertEqual(log.read_bytes(), before)
        rows = [json.loads(s) for s in log.read_text().splitlines()]
        self.assertEqual(rows, [entry for name in self.instances for entry in [
            {'helper': name, 'args': ['notify', 'theme']},
            {'helper': name, 'args': ['notify', 'font', 'Fixture Mono']},
        ]])

    def test_notification_command_uses_its_metadata_profile(self):
        # Exercise the real CLI entry point and INSTANCE import, not an injected
        # instance parameter. Only hyprctl is replaced inside this private HOME.
        shutil.copytree(ROOT / 'client', self.sources['dev'] / 'client')
        stable = self.home / '.local/state/omarchy-urbit-theme/appearance.lua'
        dev = self.home / '.local/state/omarchy-urbit-theme.dev/appearance.lua'
        for path, contents in [(stable, 'stable fixture'), (dev, 'dev fixture')]:
            path.parent.mkdir(parents=True); path.write_text(contents)
        binaries = self.root / 'notification-bin'; binaries.mkdir()
        shim = binaries / 'hyprctl'
        shim.write_text('#!/bin/sh\n[ "$1" = reload ]\n'); shim.chmod(0o755)
        env = {'HOME': str(self.home), 'PATH': str(binaries) + ':/usr/bin:/bin',
               'XDG_STATE_HOME': str(self.root / 'state'), 'LANG': 'C.UTF-8'}
        with Ownership(self.home, self.instances['dev']).hold():
            pass
        result = subprocess.run(['/usr/bin/python3', '-B', str(self.sources['dev'] / 'client/sync.py'),
                                 'notify', 'theme'], env=env, capture_output=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
        self.assertTrue(stable.exists(), 'dev notification removed stable appearance')
        self.assertEqual(stable.read_text(), 'stable fixture')
        self.assertEqual(dev.read_text(), "dev fixture")
        self.assertTrue((self.state('dev') / 'appearance-notify.json').is_file())
        self.assertFalse((self.state('default') / 'appearance-notify.json').exists())

    def test_notify_queues_only_selected_profile_without_desktop_mutation(self):
        from client.profile import appearance_root
        paths = {}
        for name, instance in self.instances.items():
            path = appearance_root(self.home, instance) / 'appearance.lua'
            path.parent.mkdir(parents=True);path.write_text('owned by ' + name);paths[name] = path
        reloads = []
        with patch.dict(os.environ, {}, clear=True):
            with Ownership(self.home, self.instances['dev']).hold():
                notify(self.state('dev'), theme=True, home=self.home, instance=self.instances['dev'],
                       runner=lambda *args, **kwargs: reloads.append(args))
                notice = json.loads((self.state('dev') / 'appearance-notify.json').read_text())
                self.assertTrue(notice['theme'])
                before = (self.state('dev') / 'appearance-notify.json').read_bytes()
                notify(self.state('edge'), font='Fixture Mono', home=self.home, instance=self.instances['edge'])
                self.assertFalse((self.state('edge') / 'appearance-notify.json').exists())
            # Selected profile survives helper exit, so font hooks during a shell
            # restart still queue its intent. Inactive hooks remain silent.
            notify(self.state('dev'), font='Fixture Mono', home=self.home, instance=self.instances['dev'])
            self.assertEqual(json.loads((self.state('dev') / 'appearance-notify.json').read_text())['font'], 'Fixture Mono')
            self.assertNotEqual((self.state('dev') / 'appearance-notify.json').read_bytes(), before)
        self.assertEqual(reloads, [])
        self.assertFalse((self.state('default') / 'appearance-notify.json').exists())
        for name, path in paths.items():
            self.assertEqual(path.read_text(), 'owned by ' + name)


class AppearanceFilesTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    runner = fixtures.DesktopTests.runner
    # Reuse the established temporary HOME and inert Omarchy/Hyprland runner.
    def test_dev_apply_preserves_stable_owned_files(self):
        Desktop(self.home, self.runner).apply(profile())
        canonical = self.home / '.local/state/omarchy-urbit-theme'
        before = {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob('*') if p.is_file()}
        metadata = self.home / 'profile-metadata'; metadata.mkdir()
        (metadata / 'manifest.json').write_text(json.dumps({'id': CANONICAL_ID + '.dev'}))
        (metadata / 'application-profile.json').write_text('{"version":1,"profile":"dev"}')
        instance = load_instance(metadata)
        desktop = Desktop(self.home, self.runner, instance=instance)
        desktop.apply(profile(color='#334455'))
        after = {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue((self.theme.parent / 'urbit-synced.dev/colors.toml').is_file())
        self.assertTrue((self.theme.parent / 'urbit-synced/colors.toml').is_file())
        self.assertIn('/omarchy-urbit-theme.dev/appearance.lua', (self.home / '.config/hypr/hyprland.lua').read_text())
        self.assertFalse(desktop.needs_repair())
        self.assertEqual(desktop.ownership.read("owner"), instance.state_name)
