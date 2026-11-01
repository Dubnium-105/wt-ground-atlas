from pathlib import Path
import tempfile
import unittest
from test_forecast import settings
from wt_atlas.forecast.inventory import discover


class DiscoveryTests(unittest.TestCase):
    def test_exported_archive_is_found_without_extracting_or_claiming_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);out=root/'data/exports';out.mkdir(parents=True)
            archive=out/'analysis-sample-2.59.0.92.zip';archive.write_bytes(b'not validated')
            (out/'analysis-sample-directory.zip').mkdir()
            (out/'unrelated.zip').write_bytes(b'other')
            found=discover([root,root])
            self.assertEqual(found['sample_archives'],[str(archive.resolve())])
            self.assertEqual(found['samples'],[])
            self.assertEqual(archive.read_bytes(),b'not validated')

if __name__=='__main__':unittest.main()
