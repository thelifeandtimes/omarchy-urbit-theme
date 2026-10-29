"""Real HTTP adapters keep journal evidence separate from current settings state."""
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from client.eyre import Eyre
from client.transport_journal import TransportJournal
from client.support import Failure
from test_backend import FakeEyre
from test_sync import LiveHub,DESK

class EyreJournalTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.j=TransportJournal(tmp.name)
        self.server=FakeEyre();self.addCleanup(self.server.close)
    def client(self,session=None):return Eyre(self.server.url,session or self.server.session(),timeout=.2,ack_timeout=.2,journal=self.j)
    def row(self):return self.j.status()['channels'][-1]
    def test_unknown_write_cannot_repeat_until_exact_readback_and_read_does_not_poke(self):
        self.server.mode='timeout';client=self.client();value={'themes':[]}
        with self.assertRaises(Failure):client.poke('themes',value)
        self.assertEqual((self.row()['phase'],self.row()['outcome']),('closed','uncertain'))
        messages=list(self.server.messages)
        with self.assertRaises(Failure):self.client().poke('themes',value)
        self.assertEqual(self.server.messages,messages)
        self.client().scry();self.assertEqual(self.row()['outcome'],'state-confirmed');self.assertEqual(self.server.messages,messages)
    def test_absent_or_wrong_session_readback_never_resolves_and_new_value_is_blocked(self):
        self.server.mode='timeout-no-write'
        with self.assertRaises(Failure):self.client().poke('themes',{'themes':[]})
        self.client().scry();self.assertEqual(self.row()['outcome'],'uncertain')
        messages=list(self.server.messages)
        with self.assertRaises(Failure):self.client().poke('themes',{'themes':[],'new':True})
        self.assertEqual(self.server.messages,messages)
        self.server.body['desk']['ui-prefs']['themes']='{"themes":[]}'
        self.client({**self.server.session(),'cookieValue':'0v.other-client-session'}).scry()
        self.assertEqual(self.row()['outcome'],'uncertain')
    def test_ack_and_explicit_nack_are_saved_before_transport_closure(self):
        self.client().poke('themes',{'themes':[]});self.assertEqual(self.row()['outcome'],'acknowledged');self.assertEqual(self.row()['phase'],'closed')
        self.server.mode='nack'
        with self.assertRaises(Failure):self.client().poke('accent',{'enabled':False})
        self.assertEqual(self.row()['outcome'],'rejected');self.assertEqual(self.row()['phase'],'closed')
    def test_delete_reply_loss_is_recovered_exactly_before_next_poke(self):
        client=self.client();put=client.put
        def lose(path,messages):
            value=put(path,messages)
            if messages[0]['action']=='delete':raise Failure('network','Lost delete reply',True)
            return value
        with patch.object(client,'put',side_effect=lose):client.poke('themes',{'themes':[]})
        row=self.row();self.assertEqual(row['phase'],'closing');self.assertEqual(row['outcome'],'acknowledged')
        self.client().poke('accent',{'enabled':False});prior=self.j.status()['channels'][0]
        self.assertEqual(prior['phase'],'closed');self.assertEqual(prior['cleanup_request'],row['cleanup_request'])
        puts=[(path,body) for method,path,body,_ in self.server.requests if method=='PUT']
        old='/~/channel/'+row['channel'];self.assertEqual(sum(path==old and b'delete' in body for path,body in puts),2)
    def test_current_origin_cannot_redirect_saved_cleanup(self):
        client=self.client()
        with self.j.operation() as lease:
            row=self.j.begin(lease,client.session,'watch');client.url='http://127.0.0.1:1'
            before=len(self.server.requests)
            with self.assertRaises(Failure):client.cleanup_channel(lease,row)
            self.assertEqual(len(self.server.requests),before)
    def test_live_watch_and_independent_publisher_keep_separate_channels(self):
        server=LiveHub();self.addCleanup(server.close)
        session=server.client().session;watch=Eyre(server.url,session,timeout=.3,journal=self.j);writer=Eyre(server.url,session,timeout=.3,journal=self.j)
        stop=threading.Event();changed=threading.Event();ready=threading.Event();errors=[]
        def run():
            try:watch.watch(DESK,stop,changed,ready)
            except Exception as e:errors.append(e)
        worker=threading.Thread(target=run);worker.start()
        try:
            self.assertTrue(ready.wait(2));first=self.row()
            writer.poke('themes',{'themes':[]});rows=self.j.status()['channels']
            self.assertEqual(rows[0]['phase'],'open');self.assertEqual(rows[1]['phase'],'closed');self.assertEqual(rows[1]['outcome'],'acknowledged')
            self.assertFalse(server.channels['/~/channel/'+first['channel']]['deleted'])
        finally:stop.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[]);self.assertTrue(watch.close_watches(time.monotonic()+2))
        self.assertTrue(all(row['phase']=='closed' for row in self.j.status()['channels']))

if __name__=='__main__':unittest.main()
