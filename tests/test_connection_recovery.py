import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import test_connection as fixtures
from test_connection import SECRET, STATE_NAME
from client.application_connection import reconcile
from client.connection_journal import Journal, secret_present
from client.connection_offer import HandoffError
from client.support import empty_ship


class Crash(BaseException):
    pass


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.ConnectionTests.setUp
    receive = fixtures.ConnectionTests.receive
    saved = fixtures.ConnectionTests.saved

    def request(self):
        return {k: self.offer[k] for k in ('version', 'request_id', 'connection_id', 'plugin_id', 'profile')} | {
            'action': 'reconcile', 'origin': self.ship.url, 'ship': '~zod'}

    def present(self, instance, origin):
        if self.keys.locked:
            raise HandoffError('keyring-unavailable')
        return (instance.state_name, origin) in self.keys.values

    def recover(self, value=None):
        args = dict(store=self.store, instance=self.instance, present=self.present)
        args['keyring'] = self.keyring
        return reconcile(json.dumps(value or self.request()), **args)

    def account_exists(self):
        return bool(self.store.load()['ships'])

    def test_recovery_removes_only_its_account_and_is_idempotent(self):
        self.receive()
        self.keys.values[(STATE_NAME, self.ship.url)] = b'production-sentinel'
        self.ship.sessions.discard(SECRET)  # Issuer has already revoked it.
        reply = self.recover()
        self.assertEqual(reply['status'], 'reconciled')
        self.assertEqual(reply['outcome'], 'removed')
        self.assertFalse(self.account_exists())
        self.assertNotIn((self.instance.state_name, self.ship.url), self.keys.values)
        self.assertEqual(self.keys.values[(STATE_NAME, self.ship.url)], b'production-sentinel')
        self.assertEqual(self.recover(), reply)
        self.assertNotIn(SECRET, json.dumps(reply))
        for path in self.store.root.glob('*.json'):
            self.assertNotIn(SECRET.encode(), path.read_bytes())

    def test_revoked_session_with_abandoned_watch_can_be_removed(self):
        from client.transport_journal import TransportJournal
        self.receive(); session = self.saved()
        journal = TransportJournal(self.store.root, self.instance)
        with journal.operation() as owner:
            row = journal.begin(owner, session, 'watch')
        self.ship.sessions.discard(SECRET)
        self.assertEqual(self.recover()['outcome'], 'removed')
        saved = journal.status()['channels'][0]
        self.assertEqual((saved['phase'], saved['delete_status']), ('closed', 303))
        self.assertFalse(self.account_exists())
        self.assertNotIn((self.instance.state_name, self.ship.url), self.keys.values)
        self.assertIn('0v.another-plugin', self.ship.sessions)
        self.assertEqual(self.recover()['outcome'], 'removed')

    def test_failed_logout_keeps_revoked_session_cleanup_retryable(self):
        from client.transport_journal import TransportJournal
        from client.eyre import Eyre
        from client.support import Failure
        self.receive(); session = self.saved()
        journal = TransportJournal(self.store.root, self.instance)
        with journal.operation() as owner:
            journal.begin(owner, session, 'watch')
        self.ship.sessions.discard(SECRET)
        with patch.object(Eyre, 'logout', side_effect=Failure('network', 'offline', True)):
            with self.assertRaises(Failure): self.recover()
        self.assertTrue(self.account_exists())
        self.assertIn((self.instance.state_name, self.ship.url), self.keys.values)
        self.assertEqual(journal.status()['channels'][0]['phase'], 'closing')
        self.assertEqual(self.recover()['outcome'], 'removed')

    def test_wrong_attempt_binding_preserves_everything(self):
        self.receive()
        previous = copy.deepcopy(self.keys.values)
        request = self.request()
        request['connection_id'] = 'f' * 32
        with self.assertRaises(HandoffError):
            self.recover(request)
        self.assertTrue(self.account_exists())
        self.assertEqual(self.keys.values, previous)

    def test_locked_keyring_keeps_account_and_pending_recovery(self):
        self.receive()
        self.keys.locked = True
        with self.assertRaises(HandoffError):
            self.recover()
        self.assertTrue(self.account_exists())
        self.keys.locked = False
        self.assertEqual(self.recover()['outcome'], 'removed')

    def test_absent_token_is_not_a_locked_keyring(self):
        self.receive()
        self.keys.values.pop((self.instance.state_name, self.ship.url))
        self.assertEqual(self.recover()['outcome'], 'removed')
        self.assertFalse(self.account_exists())

    def test_newer_standalone_account_is_preserved(self):
        self.receive()
        session = self.saved()
        session['cookieValue'] = '0v.new-standalone'
        with self.store.locked():
            self.keyring.store(self.ship.url, session)
            record = self.store.load()
            record['ships'] = [dict(empty_ship(), id='f' * 64, url=self.ship.url, ship='~zod', fingerprint='')]
            self.store.save(record)
        previous = copy.deepcopy(self.keys.values)
        self.assertEqual(self.recover()['outcome'], 'superseded')
        self.assertEqual(self.keys.values, previous)
        self.assertTrue(self.account_exists())

    def test_keyring_write_without_account_commit_can_be_recovered(self):
        original = Journal.save
        # Beginning the journal is durable before the keyring can be touched.
        def crash_after_begin(journal):
            original(journal)
            if journal.entries[-1]['phase'] == 'pending':
                raise Crash()
        with patch.object(Journal, 'save', crash_after_begin), self.assertRaises(Crash):
            self.receive()
        self.assertEqual(self.recover()['outcome'], 'absent')
        # A distinct attempt reaches the keyring then fails its account write.
        self.offer['request_id'] = '3' * 32
        with patch.object(self.store, 'save', side_effect=Crash()), self.assertRaises(Crash):
            self.receive()
        self.assertIn((self.instance.state_name, self.ship.url), self.keys.values)
        self.assertEqual(self.recover()['outcome'], 'absent')
        self.assertFalse(self.account_exists())

    def test_recovery_after_keyring_clear_and_after_account_removal(self):
        self.receive()
        original = self.keys.call
        def crash_clear(args, data):
            result = original(args, data)
            if args[1] == 'clear':
                raise Crash()
            return result
        with patch.object(self.keys, 'call', crash_clear), self.assertRaises(Crash):
            self.recover()
        self.assertTrue(self.account_exists())
        with patch.object(Journal, 'finish', side_effect=Crash()), self.assertRaises(Crash):
            self.recover()
        self.assertFalse(self.account_exists())
        self.assertEqual(self.recover()['status'], 'reconciled')

    def test_duplicate_ack_loss_never_erases_a_later_account(self):
        self.receive()
        self.recover()
        session = {'url': self.ship.url, 'ship': '~zod', 'cookieName': 'urbauth-~zod', 'cookieValue': '0v.new-standalone'}
        with self.store.locked():
            self.keyring.store(self.ship.url, session)
            record = self.store.load()
            record['ships'] = [dict(empty_ship(), id='f' * 64, url=self.ship.url, ship='~zod', fingerprint='')]
            self.store.save(record)
        previous = copy.deepcopy(self.keys.values)
        self.assertEqual(self.recover()['status'], 'reconciled')
        self.assertEqual(self.keys.values, previous)
        self.assertTrue(self.account_exists())

    def test_live_channel_prevents_receiver_credential_reconciliation(self):
        from client.transport_journal import TransportJournal
        from client.support import Failure
        self.receive();session=self.saved();journal=TransportJournal(self.store.root,self.instance)
        before=copy.deepcopy(self.keys.values)
        with journal.operation() as owner:
            row=journal.begin(owner,session,'watch')
            closing=journal.prepare_cleanup(owner,row['id'],session)
            journal.deleted(owner,row['id'],closing['cleanup_request'],204)
            with self.assertRaises(Failure):self.recover()
            self.assertTrue(self.account_exists());self.assertEqual(self.keys.values,before)
        self.assertEqual(self.recover()['outcome'],'removed')
        self.assertFalse(self.account_exists())

    def test_missing_receiver_credential_does_not_discard_open_channel(self):
        from client.transport_journal import TransportJournal
        from client.support import Failure
        self.receive();session=self.saved();journal=TransportJournal(self.store.root,self.instance)
        with journal.operation() as owner:journal.begin(owner,session,'watch')
        self.keys.values.pop((self.instance.state_name,self.ship.url))
        with self.assertRaises(Failure):self.recover()
        self.assertTrue(self.account_exists());self.assertEqual(journal.status()['channels'][0]['phase'],'open')


class SecretMetadataTests(unittest.TestCase):
    def test_locked_duplicate_missing_and_available_are_distinct(self):
        instance = type('Instance', (), {'state_name': 'fixture.auth-lab'})()
        for data, expected in [([[], []], False), ([['/item/one'], []], True),
                               ([[], ['/item/locked']], None), ([['/item/a', '/item/b'], []], None)]:
            def run(args, **kwargs):
                self.assertIn('SearchItems', args)
                self.assertNotIn('GetSecrets', args)
                return subprocess.CompletedProcess(args, 0, json.dumps({'type': 'aoao', 'data': data}).encode())
            if expected is None:
                with self.assertRaises(HandoffError):
                    secret_present(instance, 'https://example.test', runner=run)
            else:
                self.assertEqual(secret_present(instance, 'https://example.test', runner=run), expected)


if __name__ == '__main__':
    unittest.main()
