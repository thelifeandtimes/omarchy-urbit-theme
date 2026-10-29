"""An unchanged M276 writer reads its populated fixture and refuses its successor."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from client.instance import Instance
from client.support import Failure
from client.transport_journal import TransportJournal,process_identity

class UpgradeTests(unittest.TestCase):
    def test_original_writer_fixture_roundtrip_and_downgrade_refusal(self):
        fixture=Path(__file__).parent/'fixtures/theme-transport-v1'
        provenance=json.loads((fixture/'provenance.json').read_text())
        for name,digest in provenance['sha256'].items():
            self.assertEqual(hashlib.sha256((fixture/name).read_bytes()).hexdigest(),digest)
        spec=importlib.util.spec_from_file_location('client.original_transport_v1',fixture/'writer.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        value=json.loads((fixture/'channels.json').read_text())
        instance=Instance(value['plugin_id'],value['profile'],'unused','unused')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o700);(root/'transport').mkdir(mode=0o700)
            shutil.copy2(fixture/'channels.json',root/'transport/channels.json');(root/'transport/channels.json').chmod(0o600)
            shutil.copytree(fixture/'owners',root/'transport/owners');(root/'transport/owners').chmod(0o700)
            for p in (root/'transport/owners').iterdir():p.chmod(0o600)
            legacy=old.TransportJournal(root,instance);self.assertEqual(legacy.status(),value)
            current=TransportJournal(root,instance)
            original=current.path.read_bytes()
            # Historical PID namespaces may differ or have been reused. Do not
            # rely on this test namespace coincidentally matching the fixture.
            # Unknown same-boot lineage must refuse; a modeled later boot makes
            # prior owners dead without editing original fixture bytes.
            unknown={**process_identity(),"boot":value["channels"][0]["owner"]["boot"],"namespace":"pid:[0]"}
            with patch("client.transport_journal.process_identity",return_value=unknown):
                with self.assertRaises(Failure):
                    with current.operation():pass
            self.assertEqual(current.path.read_bytes(),original)
            later={**process_identity(),"boot":"00000000-0000-0000-0000-000000000001"}
            self.assertNotIn(later["boot"],{row["owner"]["boot"] for row in value["channels"]})
            with patch("client.transport_journal.process_identity",return_value=later):
                with current.operation():pass
            migrated=current.status();self.assertEqual(migrated['version'],2);self.assertEqual(migrated['channels'],value['channels'])
            original=current.path.read_bytes()
            with self.assertRaises(Failure):legacy.status()
            with self.assertRaises(Failure):
                with legacy.operation():pass
            self.assertEqual(current.path.read_bytes(),original)
            with current.operation():pass
            self.assertEqual(current.path.read_bytes(),original)

if __name__=='__main__':unittest.main()
