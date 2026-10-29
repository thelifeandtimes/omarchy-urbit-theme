"""Explicit transport drain verifies remote deletion and retains failed cleanup."""
import threading
import time
import unittest
from unittest.mock import patch
from client.sync import Bridge, Watch, stop_workers
from client.support import Failure
from test_sync import LiveHub, DESK


class StopTransportTests(unittest.TestCase):
    def setUp(self):
        self.server=LiveHub();self.addCleanup(self.server.close)
        self.watch=Watch(self.server.url,self.server.client().session)
        self.bridge=Bridge(object())
        self.addCleanup(self.cleanup)
        self.assertTrue(self.watch.ready.wait(2))

    def cleanup(self):
        self.watch.stop.set();self.bridge.stop.set();self.bridge.changed.set()
        self.watch.thread.join(3);self.bridge.thread.join(3)

    def test_exact_channel_deleted_and_both_workers_exit(self):
        receipt=stop_workers(self.bridge,[self.watch],timeout=3)
        self.assertTrue(receipt['closed']);self.assertFalse(receipt['collectionReady'])
        self.assertFalse(self.watch.thread.is_alive());self.assertFalse(self.bridge.thread.is_alive())
        self.assertTrue(self.server.channels)
        self.assertTrue(all(c['deleted'] for c in self.server.channels.values()))
        self.assertEqual(self.watch.eyre.pending_watch_channels,set())

    def test_failed_delete_is_retained_and_only_cleanup_is_retried(self):
        put=self.watch.eyre.put;deletes=[]
        def fail_delete(path,messages):
            if messages[0]['action']=='delete':
                deletes.append(path);raise Failure('network','synthetic lost cleanup',True)
            return put(path,messages)
        with patch.object(self.watch.eyre,'put',side_effect=fail_delete):
            receipt=stop_workers(self.bridge,[self.watch],timeout=3)
        self.assertFalse(receipt['closed']);self.assertEqual(receipt['reason'],'channel-cleanup-unknown')
        self.assertEqual(len(self.watch.eyre.pending_watch_channels),1)
        self.assertEqual(len(set(deletes)),1)
        before=set(self.server.channels)
        self.assertTrue(stop_workers(self.bridge,[self.watch],timeout=3)['closed'])
        self.assertEqual(set(self.server.channels),before)
        self.assertTrue(all(c['deleted'] for c in self.server.channels.values()))

    def test_live_worker_prevents_false_cleanup_receipt(self):
        release=threading.Event();worker=threading.Thread(target=release.wait);worker.start()
        original=self.bridge.thread
        self.bridge.stop.set();self.bridge.changed.set();original.join(2)
        self.bridge.thread=worker
        try:
            with patch.object(self.watch.eyre,'close_watches',wraps=self.watch.eyre.close_watches) as close:
                receipt=stop_workers(self.bridge,[self.watch],timeout=.01)
                self.assertFalse(receipt['closed']);self.assertEqual(receipt['reason'],'workers-still-running')
                close.assert_not_called()
        finally:release.set();worker.join(2)
        self.assertTrue(stop_workers(self.bridge,[self.watch],timeout=3)['closed'])

    def test_previous_failed_watch_channels_are_not_forgotten(self):
        self.assertTrue(stop_workers(self.bridge,[self.watch],timeout=3)['closed'])
        eyre=self.watch.eyre
        with eyre.journal.operation() as lease:
            saved=eyre.journal.begin(lease,eyre.session,'watch')
            old='/~/channel/'+saved['channel']
            eyre.pending_watch_channels.add(old)
            # The original writer records intent before the synthetic subscribe.
            eyre.put(old,[{'id':1,'action':'subscribe','ship':'zod','app':'settings','path':'/desk/'+DESK}])
        self.assertTrue(stop_workers(self.bridge,[self.watch],timeout=3)['closed'])
        self.assertTrue(self.server.channels[old]['deleted'])
        self.assertFalse(self.watch.eyre.pending_watch_channels)

if __name__=='__main__':unittest.main()
