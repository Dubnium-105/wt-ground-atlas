"""Observed Windows reader locks must not abort a resumable physics job."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from wt_atlas.common import atomic_write


class AtomicProgressTests(unittest.TestCase):
    def test_transient_reader_locks_preserve_old_file_until_atomic_success(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'progress.json';dest.write_bytes(b'old')
            original=Path.replace;calls=[]
            def replace(source,target):
                calls.append((source,target))
                self.assertEqual(dest.read_bytes(),b'old')
                self.assertEqual(source.read_bytes(),b'complete new progress')
                if len(calls)<3:
                    error=PermissionError('reader lock');error.winerror=5 if len(calls)==1 else 32
                    raise error
                return original(source,target)
            with patch.object(Path,'replace',replace),patch('wt_atlas.common.time.sleep') as sleep:
                atomic_write(dest,b'complete new progress')
            self.assertEqual(len(calls),3);self.assertEqual(sleep.call_count,2)
            self.assertEqual(dest.read_bytes(),b'complete new progress')
            self.assertFalse(dest.with_name(dest.name+'.part').exists())

    def test_persistent_lock_is_bounded_and_keeps_previous_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'manifest.json';dest.write_bytes(b'old')
            error=PermissionError('locked');error.winerror=33
            with patch.object(Path,'replace',side_effect=error) as replace,patch('wt_atlas.common.time.sleep') as sleep:
                with self.assertRaises(PermissionError):atomic_write(dest,b'new')
            self.assertEqual(replace.call_count,6);self.assertEqual(sleep.call_count,5)
            self.assertEqual(dest.read_bytes(),b'old')
            self.assertEqual(dest.with_name(dest.name+'.part').read_bytes(),b'new')

    def test_unrelated_permission_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'progress.json'
            with patch.object(Path,'replace',side_effect=PermissionError('not a Windows lock')) as replace,patch('wt_atlas.common.time.sleep') as sleep:
                with self.assertRaises(PermissionError):atomic_write(dest,b'new')
            self.assertEqual(replace.call_count,1);sleep.assert_not_called()
