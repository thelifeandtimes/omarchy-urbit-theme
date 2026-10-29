"""Independent Theme operations retain channels and unresolved settings across death."""
import copy
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from client import transport_journal as t
from client.instance import Instance
from client.support import Failure

SESSION={'url':'http://127.0.0.1:18081','ship':'~zod','cookieName':'urbauth-~zod','cookieValue':'private-synthetic-cookie'}
INTENT=t.intent('talon','ui-prefs','themes',{'themes':[],'privateFixture':'payload-not-stored'})

class TransportTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)/'state';self.j=t.TransportJournal(self.root)
    def row(self,n=-1):return self.j.status()['channels'][n]
    def raw(self):return self.j.path.read_bytes()
    def close(self,lease,row):
        row=self.j.prepare_cleanup(lease,row['id'],SESSION)
        return self.j.deleted(lease,row['id'],row['cleanup_request'],204)
    def test_observation_does_not_initialize(self):
        self.assertIsNone(self.j.status());self.assertFalse(self.root.exists())
    def test_channel_intent_is_saved_before_dispatch_without_secret_or_payload(self):
        with self.j.operation() as lease:
            row=self.j.begin(lease,SESSION,'poke',INTENT);self.assertEqual(row,self.row())
            self.assertEqual(row['outcome'],'uncertain');self.assertEqual(row['phase'],'open')
            for text in ('cookieValue',SESSION['cookieValue'],'payload-not-stored'):self.assertNotIn(text.encode(),self.raw())
            self.assertEqual(self.j.path.stat().st_mode&0o777,0o600)
        self.assertTrue(self.row()['owner_released'])
    def test_multiple_live_operations_share_one_session_without_replacement(self):
        with self.j.operation() as a,self.j.operation() as b:
            first=self.j.begin(a,SESSION,'watch');second=self.j.begin(b,SESSION,'poke',INTENT)
            self.assertEqual(len(self.j.status()['channels']),2);self.assertEqual(self.j.pending(SESSION),[])
            with self.assertRaises(Failure):self.j.prepare_cleanup(b,first['id'],SESSION)
            self.close(b,second);self.assertEqual(self.row(0),first)
            self.close(a,first)
        self.assertTrue(all(r['phase']=='closed' for r in self.j.status()['channels']))
    def test_concurrent_threads_serialize_only_journal_updates(self):
        barrier=threading.Barrier(3);errors=[]
        def work():
            try:
                with self.j.operation() as lease:
                    barrier.wait(2);row=self.j.begin(lease,SESSION,'watch');barrier.wait(2);self.close(lease,row)
            except BaseException as exc:errors.append(exc)
        workers=[threading.Thread(target=work) for _ in range(3)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(5);self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(len(self.j.status()['channels']),3)
        self.assertTrue(all(r['phase']=='closed' for r in self.j.status()['channels']))
    def test_released_operation_is_recoverable_while_original_process_lives(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'watch')
        with self.j.operation() as b:
            self.assertEqual([x['id'] for x in self.j.pending(SESSION)],[row['id']])
            with self.assertRaises(Failure):self.j.begin(b,SESSION,'watch')
            self.close(b,row);self.j.begin(b,SESSION,'watch')
    def test_foreign_session_cannot_hide_old_cleanup_or_delete_it(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'watch')
        with self.j.operation() as b:
            for changes in ({'cookieValue':'different'},{'ship':'~nec','cookieName':'urbauth-~nec'}):
                with self.assertRaises(Failure):self.j.pending({**SESSION,**changes})
                with self.assertRaises(Failure):self.j.prepare_cleanup(b,row['id'],{**SESSION,**changes})
                with self.assertRaises(Failure):self.j.begin(b,{**SESSION,**changes},'watch')
            self.assertEqual(self.row()['phase'],'open')
    def test_other_origin_remains_independent(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'watch')
        with self.j.operation() as b:
            other={**SESSION,'url':'http://127.0.0.1:18082'}
            self.assertEqual(self.j.pending(other),[]);self.j.begin(b,other,'watch');self.assertEqual(self.row(0)['id'],row['id'])
    def test_cleanup_request_survives_released_cleaner_and_response_replay(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'watch')
        with self.j.operation() as b:closing=self.j.prepare_cleanup(b,row['id'],SESSION)
        with self.j.operation() as c:
            again=self.j.prepare_cleanup(c,row['id'],SESSION);self.assertEqual(again['cleanup_request'],closing['cleanup_request'])
            self.assertNotEqual(again['cleanup_actor'],closing['cleanup_actor']);closed=self.j.deleted(c,row['id'],again['cleanup_request'],204)
            before=self.raw();self.assertEqual(self.j.deleted(c,row['id'],again['cleanup_request'],204),closed);self.assertEqual(self.raw(),before)
    def test_live_cleaner_prevents_new_channel_or_takeover(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'watch')
        with self.j.operation() as b,self.j.operation() as c:
            self.j.prepare_cleanup(b,row['id'],SESSION)
            with self.assertRaises(Failure):self.j.prepare_cleanup(c,row['id'],SESSION)
            with self.assertRaises(Failure):self.j.pending(SESSION)
            with self.assertRaises(Failure):self.j.begin(c,SESSION,'watch')
    def test_released_flag_with_held_lease_is_not_cleanup_authority(self):
        with self.j.operation() as a,self.j.operation() as b:
            row=self.j.begin(a,SESSION,'watch')
            with self.j.transaction():
                value=self.j.read();value['channels'][0]['owner_released']=True;self.j.write(value)
            with self.assertRaises(Failure):self.j.prepare_cleanup(b,row['id'],SESSION)
            with self.assertRaises(Failure):self.j.begin(b,SESSION,'watch')
            with self.assertRaises(Failure):self.j.pending(SESSION)
    def test_missing_owner_file_or_unknown_namespace_refuses_intact(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'watch')
        with self.j.operation() as b:
            p=self.j.root/'owners'/row['owner']['id'];saved=p.read_bytes();p.unlink()
            before=self.raw()
            with self.assertRaises(FileNotFoundError):self.j.prepare_cleanup(b,row['id'],SESSION)
            self.assertEqual(self.raw(),before);p.write_bytes(saved);p.chmod(0o600)
            with self.j.transaction():
                value=self.j.read();value['channels'][0]['owner_released']=False;value['channels'][0]['owner']['namespace']='pid:[1]';self.j.write(value)
            before=self.raw()
            with self.assertRaises(Failure):self.j.pending(SESSION)
            with self.assertRaises(Failure):self.j.begin(b,SESSION,'watch')
            self.assertEqual(self.raw(),before)
    def test_cleanup_receipt_requires_exact_request_actor_and_status(self):
        with self.j.operation() as a:
            row=self.j.begin(a,SESSION,'watch');closing=self.j.prepare_cleanup(a,row['id'],SESSION);before=self.raw()
            for request,status in [('a'*32,204),(closing['cleanup_request'],True),(closing['cleanup_request'],202)]:
                with self.assertRaises(Failure):self.j.deleted(a,row['id'],request,status)
                self.assertEqual(self.raw(),before)
            with self.j.operation() as b:
                with self.assertRaises(Failure):self.j.deleted(b,row['id'],closing['cleanup_request'],204)
    def test_transport_closed_does_not_resolve_uncertain_write_or_allow_resend(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'poke',INTENT);self.close(a,row)
        with self.j.operation() as b:
            with self.assertRaises(Failure):self.j.begin(b,SESSION,'poke',INTENT)
            with self.assertRaises(Failure):self.j.begin(b,SESSION,'poke',{**INTENT,'value':'a'*64})
            watch=self.j.begin(b,SESSION,'watch');self.close(b,watch)
            other=t.intent('talon','ui-prefs','accent',{'enabled':False});self.j.begin(b,SESSION,'poke',other)
        self.assertEqual(self.row(0)['outcome'],'uncertain')
    def test_exact_authenticated_readback_records_state_confirmation_not_ack(self):
        with self.j.operation() as a:row=self.j.begin(a,SESSION,'poke',INTENT);self.close(a,row)
        with self.j.operation() as b:
            for candidate in ({**INTENT,'entry':'different'},{**INTENT,'value':'a'*64}):
                with self.assertRaises(Failure):self.j.observe(b,row['id'],SESSION,candidate)
            with self.assertRaises(Failure):self.j.observe(b,row['id'],{**SESSION,'cookieValue':'different'},INTENT)
            observed=self.j.observe(b,row['id'],SESSION,INTENT);self.assertEqual(observed['outcome'],'state-confirmed')
            before=self.raw();self.j.observe(b,row['id'],SESSION,INTENT);self.assertEqual(before,self.raw())
            self.j.begin(b,SESSION,'poke',INTENT)
    def test_live_write_cannot_be_observed_as_finished(self):
        with self.j.operation() as a,self.j.operation() as b:
            row=self.j.begin(a,SESSION,'poke',INTENT);self.close(a,row)
            with self.assertRaises(Failure):self.j.observe(b,row['id'],SESSION,INTENT)
    def test_only_original_live_owner_records_application_ack_or_rejection(self):
        with self.j.operation() as a,self.j.operation() as b:
            row=self.j.begin(a,SESSION,'poke',INTENT)
            with self.assertRaises(Failure):self.j.receipt(b,row['id'],'acknowledged')
            self.j.receipt(a,row['id'],'acknowledged');before=self.raw();self.j.receipt(a,row['id'],'acknowledged');self.assertEqual(self.raw(),before)
            with self.assertRaises(Failure):self.j.receipt(a,row['id'],'rejected')
            self.close(a,row)
        with self.assertRaises(Failure):self.j.receipt(a,row['id'],'acknowledged')
    def child(self,mode):
        code='''import json,sys,time
from client.transport_journal import TransportJournal
j=TransportJournal(sys.argv[1]);session=json.loads(sys.argv[2])
with j.operation() as lease:
 if sys.argv[3]=='open':row=j.begin(lease,session,'watch')
 else:row=j.prepare_cleanup(lease,j.status()['channels'][0]['id'],session)
 print(json.dumps(row),flush=True)
 time.sleep(60)
'''
        child=subprocess.Popen([sys.executable,'-B','-c',code,str(self.root),json.dumps(SESSION),mode],stdout=subprocess.PIPE,text=True)
        def cleanup():
            if child.poll() is None:child.kill()
            child.wait(5);child.stdout.close()
        self.addCleanup(cleanup);self.assertTrue(select.select([child.stdout],[],[],4)[0]);row=json.loads(child.stdout.readline());return child,row
    def test_actual_owner_and_cleaner_sigkill_preserve_exact_cleanup_identity(self):
        owner,first=self.child('open');owner.kill();self.assertEqual(owner.wait(5),-signal.SIGKILL)
        cleaner,closing=self.child('closing');cleaner.kill();self.assertEqual(cleaner.wait(5),-signal.SIGKILL)
        with self.j.operation() as lease:
            again=self.j.prepare_cleanup(lease,first['id'],SESSION)
            self.assertEqual(again['cleanup_request'],closing['cleanup_request']);self.assertEqual(again['owner'],first['owner']);self.close(lease,again)
    def test_real_competing_process_remains_active_and_is_not_deleted(self):
        child,first=self.child('open')
        with self.j.operation() as lease:
            self.assertEqual(self.j.pending(SESSION),[]);self.j.begin(lease,SESSION,'poke',INTENT)
            with self.assertRaises(Failure):self.j.prepare_cleanup(lease,first['id'],SESSION)
        self.assertIsNone(child.poll())
    def test_missing_existing_or_future_format_never_reinitializes(self):
        with self.j.operation():pass
        self.j.path.unlink()
        with self.assertRaises(Failure):
            with self.j.operation():pass
        self.assertFalse(self.j.path.exists())
        self.j.path.write_text('{"version":99}');self.j.path.chmod(0o600);before=self.raw()
        with self.assertRaises(Failure):
            with self.j.operation():pass
        self.assertEqual(self.raw(),before)
    def test_wrong_profile_and_duplicate_json_keys_refuse_unchanged(self):
        with self.j.operation():pass
        before=self.raw();other=t.TransportJournal(self.root,Instance('thelifeandtimes.urbit-theme.edge','edge','unused','unused'))
        with self.assertRaises(Failure):other.status()
        self.assertEqual(self.raw(),before);self.j.path.write_text('{"version":1,"version":1}')
        with self.assertRaises(Failure):self.j.status()
    def test_symlinks_hardlinks_and_permissions_refuse_without_repair(self):
        with self.j.operation():pass
        self.j.path.chmod(0o644)
        with self.assertRaises(Failure):self.j.status()
        self.assertEqual(self.j.path.stat().st_mode&0o777,0o644);self.j.path.chmod(0o600)
        other=self.j.root/'linked';os.link(self.j.path,other)
        with self.assertRaises(Failure):self.j.status()
        other.unlink();raw=self.raw();self.j.path.unlink();other.write_bytes(raw);other.chmod(0o600);self.j.path.symlink_to(other)
        with self.assertRaises(OSError):self.j.status()
    def test_failed_replace_retains_authoritative_state_and_temporary(self):
        with self.j.operation() as lease:
            before=self.raw()
            with patch.object(t.os,'replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):self.j.begin(lease,SESSION,'watch')
            self.assertEqual(self.raw(),before);self.assertEqual(len(list(self.j.root.glob('.pending-*'))),1)
    def test_lost_prepare_fsync_ack_reuses_request(self):
        with self.j.operation() as lease:
            row=self.j.begin(lease,SESSION,'watch');sync=self.j.sync
            def fail(path):sync(path);raise OSError('lost directory acknowledgement')
            with patch.object(self.j,'sync',side_effect=fail):
                with self.assertRaises(OSError):self.j.prepare_cleanup(lease,row['id'],SESSION)
            saved=self.row();again=self.j.prepare_cleanup(lease,row['id'],SESSION);self.assertEqual(saved,again)
    def test_full_history_preserves_all_records(self):
        with self.j.operation() as lease:
            row=self.j.begin(lease,SESSION,'watch');self.close(lease,row);before=self.raw()
            with patch.object(t,'MAX_CHANNELS',1):
                with self.assertRaises(Failure):self.j.begin(lease,SESSION,'watch')
            self.assertEqual(self.raw(),before)
        with patch.object(t,'MAX_OWNERS',1):
            with self.assertRaises(Failure):
                with self.j.operation():pass

if __name__=='__main__':unittest.main()
