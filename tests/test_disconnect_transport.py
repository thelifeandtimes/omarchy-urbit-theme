"""Disconnect fences new work and retains the exact credential on cleanup failure."""
import contextlib
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from client.eyre import Eyre
from client.main import Helper
from client.support import Failure,StateStore
from client import transport_journal as t
import test_backend as b
import test_sync as sync

class DisconnectTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=StateStore(Path(tmp.name)/'state');self.j=t.TransportJournal(self.store.root)
        self.hub=b.FakeEyre();self.addCleanup(self.hub.close);self.keys=b.MemoryKeyring()
        self.helper=Helper(self.store,self.keys,lambda:b.PALETTE,self.hub.transport)
        result=self.helper.run('login',{'url':self.hub.url,'code':b.CODE});self.assertTrue(result['ok'],result)
        self.expected={k:result['state']['ships'][0][k] for k in ('id','url','ship')}
        self.session=self.keys.sessions[self.hub.url]
    def client(self):return Eyre(self.hub.url,self.session,timeout=.2,ack_timeout=.2,journal=self.j)
    def disconnect(self):return self.helper.run('disconnect',{'expectedAccount':self.expected})
    def raw(self):return self.j.path.read_bytes()
    def unresolved(self):
        with self.j.operation() as lease:return self.j.begin(lease,self.session,'poke',t.intent('talon','ui-prefs','themes',{'private':'value'}))
    def test_retirement_survives_restart_and_blocks_old_reads_and_writes(self):
        client=self.client();client.scry()
        with client.disconnecting():pass
        before=len(self.hub.requests)
        for action in (lambda:self.client().scry(),lambda:self.client().poke('themes',{})):
            with self.assertRaises(Failure):action()
        self.assertEqual(len(self.hub.requests),before)
        self.assertTrue(t.TransportJournal(self.store.root).status()['sessions'][0]['retired'])
        fresh={**self.session,'cookieValue':'0v.other-client-session'}
        Eyre(self.hub.url,fresh,journal=self.j).scry()
    def test_inflight_read_blocks_retirement_even_without_a_channel(self):
        with self.j.admitted(self.session):
            with self.assertRaises(Failure) as caught:
                with self.client().disconnecting(timeout=0):pass
            self.assertEqual(caught.exception.code,'transport-busy')
            self.assertEqual(self.j.status()['channels'],[])
            with self.assertRaises(Failure):
                with self.j.admitted(self.session):pass
        with self.client().disconnecting(timeout=0):pass
    def test_failure_preserves_account_secret_and_uncertain_write_for_exact_retry(self):
        row=self.unresolved();before=copy.deepcopy(self.keys.sessions);original=Eyre.put
        def fail(client,path,messages):
            if messages==[{'action':'delete'}]:raise Failure('network','No deletion response',True)
            return original(client,path,messages)
        with patch.object(Eyre,'put',fail):result=self.disconnect()
        self.assertFalse(result['ok']);self.assertEqual(self.keys.sessions,before)
        saved=self.store.load()['ships'][0];self.assertFalse(saved['automatic']);self.assertFalse(saved['pending'])
        channel=self.j.status()['channels'][0];self.assertEqual(channel['phase'],'closing');self.assertEqual(channel['outcome'],'uncertain')
        self.assertFalse(any(r[1]=='/~/logout' for r in self.hub.requests));request=channel['cleanup_request']
        result=self.disconnect();self.assertTrue(result['ok'],result);self.assertEqual(result['state'],{'ships':[]})
        final=self.j.status()['channels'][0];self.assertEqual(final['cleanup_request'],request);self.assertEqual(final['phase'],'closed');self.assertEqual(final['outcome'],'uncertain')
        self.assertEqual(self.keys.sessions,{});self.assertFalse(any(m['action']=='poke' for m in self.hub.messages))
    def test_missing_credential_with_abandoned_channel_refuses_account_removal(self):
        self.unresolved();self.keys.sessions.clear();result=self.disconnect()
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'transport-state')
        self.assertEqual(len(result['state']['ships']),1);self.assertNotIn('clear',self.keys.calls)
    def test_held_closed_owner_does_not_authorize_forgetting(self):
        with self.j.operation() as lease:
            row=self.j.begin(lease,self.session,'watch');row=self.j.prepare_cleanup(lease,row['id'],self.session)
            self.j.deleted(lease,row['id'],row['cleanup_request'],204)
            result=self.disconnect();self.assertFalse(result['ok']);self.assertIn(self.hub.url,self.keys.sessions)
        self.assertTrue(self.disconnect()['ok'])
    def test_expired_credential_is_used_only_for_cleanup(self):
        self.unresolved();self.session['expires']='2000-01-01T00:00:00Z'
        before=len(self.hub.requests)
        with self.assertRaises(Failure):self.client().scry()
        self.assertEqual(len(self.hub.requests),before)
        result=self.disconnect();self.assertTrue(result['ok'],result)
        self.assertEqual(self.j.status()['channels'][0]['phase'],'closed')
    def test_unknown_or_missing_journal_preserves_account_and_credential(self):
        self.client().scry();self.j.path.unlink();result=self.disconnect()
        self.assertFalse(result['ok']);self.assertEqual(len(result['state']['ships']),1)
        self.assertIn(self.hub.url,self.keys.sessions);self.assertFalse(self.j.path.exists())
    def test_failed_retirement_write_never_clears_account_or_secret(self):
        self.client().scry();before=self.raw()
        with patch.object(t.os,'replace',side_effect=OSError('write failed')):result=self.disconnect()
        self.assertFalse(result['ok']);self.assertEqual(self.raw(),before)
        self.assertIn(self.hub.url,self.keys.sessions)
    def test_uncommitted_account_removal_keeps_fence_and_credential(self):
        self.client().scry();save=self.store.save
        def fail(record):
            if not record['ships']:raise OSError('state write failed')
            save(record)
        with patch.object(self.store,'save',fail):result=self.disconnect()
        self.assertFalse(result['ok']);self.assertIn(self.hub.url,self.keys.sessions)
        self.assertTrue(self.j.status()['sessions'][0]['retired'])
        self.assertFalse(any(r[1]=='/~/logout' for r in self.hub.requests))
        self.assertTrue(self.disconnect()['ok'])
    def test_live_watch_stops_before_cleanup_and_other_origin_is_independent(self):
        server=sync.LiveHub();self.addCleanup(server.close)
        session=server.client().session;watch=Eyre(server.url,session,timeout=.3,journal=self.j)
        stop=threading.Event();ready=threading.Event();errors=[]
        def run():
            try:watch.watch(sync.DESK,stop,threading.Event(),ready)
            except Failure as exc:errors.append(exc.code)
        worker=threading.Thread(target=run);worker.start()
        try:
            self.assertTrue(ready.wait(2))
            with Eyre(server.url,session,timeout=.3,journal=self.j).disconnecting(timeout=3):
                self.assertFalse(worker.is_alive());self.assertTrue(all(row['phase']=='closed' for row in self.j.status()['channels']))
                self.client().scry()
            self.assertEqual(errors,['transport-state'])
        finally:stop.set();worker.join(3)
    def test_format1_migration_preserves_channel_evidence_and_fences_prior_reader(self):
        self.unresolved();v=self.j.status();old=copy.deepcopy(v);old['version']=1;old.pop('sessions')
        self.j.path.write_bytes(t.canonical(old));self.j.path.chmod(0o600);before=self.raw()
        self.assertEqual(self.j.status(),old);self.assertEqual(self.raw(),before)
        with self.j.operation():pass
        migrated=self.j.status();self.assertEqual(migrated['version'],2);self.assertEqual(migrated['channels'],old['channels'])
        self.assertEqual(migrated['sessions'],[{'origin':self.hub.url,'ship':self.session['ship'],'session_ref':t.session_ref(self.session),'retired':False}])
    def test_format1_live_owner_refuses_migration_intact(self):
        with self.j.operation() as lease:
            self.j.begin(lease,self.session,'watch');v=self.j.status();v['version']=1;v.pop('sessions');self.j.path.write_bytes(t.canonical(v));self.j.path.chmod(0o600);before=self.raw()
            with self.assertRaises(Failure):
                with self.j.operation():pass
            self.assertEqual(self.raw(),before)
    def test_session_history_limit_preserves_prior_fences(self):
        self.client().scry()
        with self.client().disconnecting():pass
        before=self.raw()
        with patch.object(t,'MAX_SESSIONS',1),self.assertRaises(Failure):
            with self.j.admitted({**self.session,'cookieValue':'new'}):pass
        self.assertEqual(self.raw(),before)

if __name__=='__main__':unittest.main()
