"""Private HOME, real file locks and Lua execution; no desktop command fallback."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from client.appearance import HEADER, Ownership, guarded_include, legacy_include
from client.instance import CANONICAL_ID, load_instance
from client.profile import APPEARANCE_HEADER, Desktop, appearance_code, window_lua
from client.support import Failure
from client.sync import notify
import test_profile as fixtures
from test_profile import profile


class AppearanceOwnerTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    runner = fixtures.DesktopTests.runner

    def desktop(self, name):
        root = self.home / ('metadata-' + name)
        root.mkdir(exist_ok=True)
        suffix = '' if name == 'default' else '.' + name
        (root / 'manifest.json').write_text(json.dumps({'id': CANONICAL_ID + suffix}))
        (root / 'application-profile.json').write_text(json.dumps({'version': 1, 'profile': name}))
        return Desktop(self.home, self.runner, instance=load_instance(root))

    def lua(self):
        # Execute the actual generated config/includes. Record configuration
        # calls; two active overrides would produce two lines, not a hidden win.
        code = 'hl={config=function(t) print(t.decoration.rounding) end, curve=function() end, animation=function() end}\ndofile(os.getenv("HOME").."/.config/hypr/hyprland.lua")'
        result = subprocess.run(['/usr/bin/lua', '-e', code], env={'HOME': str(self.home)}, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_stable_dev_edge_stable_only_one_lua_owner_and_saved_files_retained(self):
        stable, dev, edge = [self.desktop(n) for n in ('default', 'dev', 'edge')]
        values = {}
        for d, rounding in ((stable, 8), (dev, 14), (edge, 22), (stable, 8)):
            p = profile(); p['windows']['decoration:rounding'] = {'int': rounding}
            with d.session():
                d.apply(p)
                self.assertEqual(self.lua(), [str(rounding)])
            for name, data in values.items():
                self.assertEqual((self.home / '.local/state' / name / 'appearance.lua').read_bytes(), data)
            values[d.instance.state_name] = (d.appearance / 'appearance.lua').read_bytes()
        self.assertEqual(len(values), 3)
        self.assertEqual(self.lua(), ['8'], 'last selected appearance survives helper exit')
        self.assertIn('-- personal settings stay', (self.home / '.config/hypr/hyprland.lua').read_text())

    def test_shared_lease_refuses_a_second_real_process_then_allows_after_exit(self):
        d = self.desktop('dev')
        program = 'from client.appearance import Ownership; import sys\nwith Ownership(sys.argv[1]).hold(): print("acquired")'
        with d.session():
            before = (d.ownership.root / 'active').read_bytes()
            r = subprocess.run([sys.executable, '-B', '-c', program, str(self.home)], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('Another Theme profile owns this desktop', r.stderr)
            self.assertEqual((d.ownership.root / 'active').read_bytes(), before)
            self.assertFalse(self.calls)
        r = subprocess.run([sys.executable, '-B', '-c', program, str(self.home)], capture_output=True, text=True, timeout=5)
        self.assertEqual((r.returncode, r.stdout), (0, 'acquired\n'), r.stderr)

    def test_manual_theme_notice_disables_all_overrides_and_preserves_files(self):
        d = self.desktop('dev'); d.apply(profile())
        appearance = (d.appearance / 'appearance.lua').read_bytes()
        root = self.home / 'notifications'
        notify(root, theme=True, home=self.home, instance=d.instance)
        self.assertTrue(json.loads((root / 'appearance-notify.json').read_text())['theme'])
        self.assertEqual(self.lua(), ['8'], 'a hook cannot mutate the desktop')
        with d.session():
            d.local_theme_selected()
        self.assertEqual(self.lua(), [])
        self.assertEqual((d.appearance / 'appearance.lua').read_bytes(), appearance)
        self.assertTrue(d.needs_repair())
        d.apply(profile())
        self.assertEqual(self.lua(), ['8'])

    def test_exact_legacy_include_is_guarded_without_editing_its_file(self):
        d = self.desktop('dev')
        legacy = self.home / '.local/state/omarchy-urbit-theme/appearance.lua'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('hl.config({decoration={rounding=99}})\n')
        config = self.home / '.config/hypr/hyprland.lua'
        config.write_text(config.read_text() + legacy_include('omarchy-urbit-theme'))
        d.apply(profile())
        self.assertEqual(self.lua(), ['8'])
        self.assertEqual(legacy.read_text(), 'hl.config({decoration={rounding=99}})\n')
        self.assertIn(guarded_include('omarchy-urbit-theme'), config.read_text())

    def test_edited_include_refuses_before_commands_and_preserves_bytes(self):
        d = self.desktop('dev')
        config = self.home / '.config/hypr/hyprland.lua'
        edited = legacy_include('omarchy-urbit-theme').replace('dofile(p)', 'dofile(p); print("custom")')
        config.write_text(config.read_text() + edited)
        before = config.read_bytes()
        with self.assertRaises(Failure): d.apply(profile())
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.calls, [])
        self.assertIsNone(d.ownership.read('owner'))

    def test_edited_appearance_refuses_before_commands_and_preserves_bytes(self):
        d = self.desktop('dev'); d.apply(profile())
        p = d.appearance / 'appearance.lua'; p.write_text(p.read_text() + '-- manual edit\n')
        before = p.read_bytes(); self.calls.clear()
        with self.assertRaises(Failure): d.apply(profile())
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_unknown_owner_record_refuses_and_lua_runs_no_override(self):
        d = self.desktop('dev'); d.apply(profile())
        p = d.ownership.root / 'owner'; p.write_text('Network Theme owner 2\nfuture\n')
        before = p.read_bytes(); self.calls.clear()
        with self.assertRaises(Failure): d.apply(profile())
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(self.lua(), [])
        self.assertEqual(self.calls, [])

    def test_owner_symlink_and_public_record_refused(self):
        d = self.desktop('dev')
        with d.session(): d.ownership.write('owner', d.instance.state_name)
        p = d.ownership.root / 'owner'; before = p.read_bytes(); p.chmod(0o644)
        with self.assertRaises(Failure): d.ownership.read('owner')
        self.assertEqual(p.read_bytes(), before)
        p.chmod(0o600); target = self.home / 'target'; p.rename(target); p.symlink_to(target)
        with self.assertRaises(Failure):
            with d.session(): pass
        self.assertEqual(target.read_bytes(), before)

    def test_foreign_owner_prevents_direct_apply_and_notification_effect(self):
        stable, dev = self.desktop('default'), self.desktop('dev')
        with stable.session():
            stable.apply(profile()); self.calls.clear()
            with self.assertRaises(Failure): dev.apply(profile())
            notify(self.home / 'inactive', theme=True, home=self.home, instance=dev.instance)
            self.assertFalse((self.home / 'inactive').exists())
            self.assertEqual(self.calls, [])
            self.assertEqual(self.lua(), ['8'])

    def test_hook_selection_survives_restart_but_not_leaving_and_returning(self):
        stable, dev = self.desktop('default'), self.desktop('dev')
        root = self.home / 'notifications'
        with stable.session():
            notify(root, font='Fixture Mono', home=self.home, instance=stable.instance)
        notice = json.loads((root / 'appearance-notify.json').read_text())
        with stable.session():
            self.assertTrue(stable.ownership.notification_current(notice))
        with dev.session():
            self.assertFalse(stable.ownership.notification_current(notice))
        with stable.session():
            self.assertFalse(stable.ownership.notification_current(notice))
            notify(root, font='New Font', home=self.home, instance=stable.instance)
            current = json.loads((root / 'appearance-notify.json').read_text())
            self.assertTrue(stable.ownership.notification_current(current))
            self.assertNotEqual(notice['selection'], current['selection'])

    def test_unknown_active_format_preserved_before_any_selection_change(self):
        d = self.desktop('dev')
        with d.session(): pass
        p = d.ownership.root / 'active'; p.write_text('Network Theme owner 9\nunknown\n')
        before = p.read_bytes()
        with self.assertRaises(Failure):
            with d.session(): pass
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_real_serve_helpers_require_previous_process_exit_across_profiles(self):
        import selectors
        import shutil
        root = Path(__file__).resolve().parents[1]
        env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
               'XDG_STATE_HOME': str(self.home / 'accounts'), 'LANG': 'C.UTF-8'}
        helpers = []
        def start(name):
            d = self.desktop(name)
            metadata = self.home / ('metadata-' + name)
            if not (metadata / 'client').exists(): shutil.copytree(root / 'client', metadata / 'client')
            p = subprocess.Popen([sys.executable, '-B', str(metadata / 'client/sync.py'), 'serve'],
                                 env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            helpers.append(p)
            def cleanup():
                if p.poll() is None:
                    p.kill(); p.wait(timeout=5)
                for stream in (p.stdin, p.stdout, p.stderr): stream.close()
            self.addCleanup(cleanup)
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(5), 'helper did not report status')
            return p, json.loads(p.stdout.readline())
        for name, rival in [('default', 'dev'), ('dev', 'edge'), ('edge', 'default'), ('default', 'dev')]:
            p, status = start(name)
            self.assertEqual(status['status'], 'Add a ship to start desktop sync')
            rejected, error = start(rival)
            self.assertEqual(rejected.wait(timeout=5), 1)
            self.assertIn('Another Theme profile owns this desktop', error['error'])
            self.assertIsNone(p.poll())
            p.stdin.write('{"action":"shutdown-transport"}\n'); p.stdin.flush()
            output, errors = p.communicate(timeout=8)
            self.assertEqual((p.returncode, errors), (0, ''))
            receipt = json.loads(output.splitlines()[-1])['transport']
            self.assertTrue(receipt['closed'])
            self.assertFalse(receipt['collectionReady'])
        self.assertEqual(self.calls, [])
        self.assertEqual((self.home / '.config/hypr/hyprland.lua').read_text(), '-- personal settings stay\n')

    def test_missing_owner_after_interrupted_selection_requires_reapply(self):
        d = self.desktop('dev'); d.apply(profile())
        (d.ownership.root / 'owner').unlink()
        self.assertEqual(self.lua(), [])
        self.assertTrue(d.needs_repair())
        d.apply(profile())
        self.assertEqual(self.lua(), ['8'])

    def legacy(self, d, p):
        # Reconstruct the actual pre-owner source expression independently of
        # the migration renderer, including header and exact newlines.
        raw = ('-- Network Theme appearance adapter 2\n'
               'local f=io.open(os.getenv("HOME").."/.local/state/omarchy/current/theme.name","r")\n'
               'local n=f and f:read("*l"); if f then f:close() end\n'
               'if n==' + json.dumps(p['theme']) + ' then\n' + window_lua(p) + 'end\n')
        d.appearance.mkdir(parents=True, exist_ok=True)
        (d.appearance / 'appearance.lua').write_text(raw)
        return raw.encode()

    def test_known_legacy_adoption_preserves_exact_backup_and_reapplies_new_profile(self):
        d = self.desktop('dev'); old = profile(); old['windows']['decoration:rounding'] = {'int': 17}
        raw = self.legacy(d, old)
        config = self.home / '.config/hypr/hyprland.lua'
        config.write_text(config.read_text() + legacy_include(d.instance.state_name))
        self.assertTrue(d.adopt_legacy([old, None]))
        self.assertEqual(self.calls, [], 'migration issued a desktop command')
        self.assertEqual((d.appearance / 'appearance-before-owner-v3.lua').read_bytes(), raw)
        self.assertEqual((d.appearance / 'appearance.lua').read_text(), appearance_code(old, d.instance))
        self.assertEqual(self.lua(), [], 'migration claimed applied ownership')
        self.assertTrue(d.needs_repair())
        d.apply(profile())
        self.assertEqual(self.lua(), ['8'])
        self.assertFalse(d.adopt_legacy([old]))
        self.assertEqual((d.appearance / 'appearance-before-owner-v3.lua').read_bytes(), raw)

    def test_legacy_matching_in_progress_profile_is_accepted(self):
        d = self.desktop('dev'); applying = profile()
        self.legacy(d, applying)
        self.assertTrue(d.adopt_legacy([None, applying]))
        self.assertTrue((d.appearance / 'appearance.lua').read_text().startswith(APPEARANCE_HEADER))

    def test_legacy_unknown_edited_wrong_profile_and_unversioned_are_preserved(self):
        from copy import deepcopy
        for case in ('unknown', 'edited', 'wrong-profile', 'unversioned'):
            with self.subTest(case=case):
                d = self.desktop('dev'); old = profile(); raw = self.legacy(d, old)
                candidates = [old]
                if case == 'unknown': candidates = [None, None]
                if case == 'edited': raw += b'-- manual change\n'
                if case == 'wrong-profile':
                    candidates = [deepcopy(old)]; candidates[0]['windows']['decoration:rounding'] = {'int': 9}
                if case == 'unversioned': raw = raw.split(b'\n', 1)[1]
                path = d.appearance / 'appearance.lua'; path.write_bytes(raw)
                with self.assertRaises(Failure): d.adopt_legacy(candidates)
                self.assertEqual(path.read_bytes(), raw)
                self.assertFalse((d.appearance / 'appearance-before-owner-v3.lua').exists())
                self.assertEqual(self.calls, [])

    def test_interrupted_legacy_migration_reuses_backup_and_retries_exactly(self):
        from unittest.mock import patch
        from client.profile import atomic
        d = self.desktop('dev'); old = profile(); raw = self.legacy(d, old)
        target = d.appearance / 'appearance.lua'
        def fail_target(path, data):
            if Path(path) == target: raise OSError('injected interruption before replacement')
            return atomic(path, data)
        with patch('client.profile.atomic', side_effect=fail_target):
            with self.assertRaises(OSError): d.adopt_legacy([old])
        backup = d.appearance / 'appearance-before-owner-v3.lua'
        self.assertEqual((backup.read_bytes(), target.read_bytes()), (raw, raw))
        self.assertTrue(d.adopt_legacy([old]))
        before = target.read_bytes()
        self.assertFalse(d.adopt_legacy([old]))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(backup.read_bytes(), raw)

    def test_conflicting_backup_and_linked_legacy_are_preserved(self):
        d = self.desktop('dev'); old = profile(); raw = self.legacy(d, old)
        target = d.appearance / 'appearance.lua'; backup = d.appearance / 'appearance-before-owner-v3.lua'
        backup.write_text('keep my notes')
        with self.assertRaises(Failure): d.adopt_legacy([old])
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(backup.read_text(), 'keep my notes')
        backup.unlink(); os.link(target, backup)
        with self.assertRaises(Failure): d.adopt_legacy([old])
        self.assertEqual((target.read_bytes(), backup.read_bytes()), (raw, raw))

    def test_interrupted_theme_requires_lock_and_intact_owned_generated_directory(self):
        stable, dev = self.desktop('default'), self.desktop('dev')
        dev.apply(profile())
        (self.current / 'theme.name').write_text('urbit-synced.dev\n')
        self.assertFalse(stable.interrupted_transition())
        with stable.session():
            self.assertTrue(stable.interrupted_transition())
            target = self.home / '.config/omarchy/themes/urbit-synced.dev/btop.theme'
            target.write_text('user edited')
            self.assertFalse(stable.interrupted_transition())
            self.assertEqual(target.read_text(), 'user edited')
            (self.current / 'theme.name').write_text('unknown-working-theme\n')
            self.assertFalse(stable.interrupted_transition())
