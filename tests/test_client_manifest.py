from io import BytesIO
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.client_manifest import (VERSION_URL,MANIFEST_URL,audit,bdecode,capture,descriptor,parse_manifest,verify_capture)


def encode(value):
    if isinstance(value,bytes):return str(len(value)).encode()+b':'+value
    if type(value) is int:return b'i'+str(value).encode()+b'e'
    if isinstance(value,list):return b'l'+b''.join(encode(x) for x in value)+b'e'
    if isinstance(value,dict):return b'd'+b''.join(encode(k)+encode(value[k]) for k in sorted(value))+b'e'
    raise TypeError(value)


def manifest(files,version=b'2.59.0.92'):
    rows=[{b'path':[part.encode() for part in name.split('/')],b'length':len(body),b'sha1':hashlib.sha1(body).digest()}
          for name,body in files.items()]
    return encode({b'info':{b'version':version,b'files':rows},b'yup':{b'project':b'warthunder',b'version':version}})


class ClientManifestTests(unittest.TestCase):
    def test_bencode_rejects_ambiguous_or_truncated_data(self):
        self.assertEqual(bdecode(b'd1:ali1e1:xee'),{b'a':[1,b'x']})
        for raw in (b'i-0e',b'i01e',b'01:a',b'3:ab',b'l1:a',b'd1:ai1e1:ai2ee',b'i1ejunk'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):bdecode(raw)

    def test_manifest_rejects_bad_versions_duplicate_paths_and_traversal(self):
        with self.assertRaisesRegex(ValueError,'version'):
            parse_manifest(manifest({},b'invalid'))
        with self.assertRaisesRegex(ValueError,'file identity'):
            parse_manifest(manifest({'../escape.grp':b'bad'}))
        row={b'path':[b'same.grp'],b'length':1,b'sha1':b'x'*20}
        payload=encode({b'info':{b'version':b'2.59.0.92',b'files':[row,row]},
                        b'yup':{b'project':b'warthunder',b'version':b'2.59.0.92'}})
        with self.assertRaisesRegex(ValueError,'Duplicate updater file path'):
            parse_manifest(payload)

    def test_descriptor_rejects_external_or_mixed_manifest_identities(self):
        with self.assertRaises(ValueError):descriptor(b'https://example.test/' + b'a'*40 + b'.yup')
        with self.assertRaises(ValueError):descriptor(b'https://user@cdn.gaijinent.com/' + b'a'*40 + b'.yup')
        with self.assertRaises(ValueError):descriptor(b'https://a.gaijinent.com/' + b'a'*40 + b'.yup https://b.gaijinent.com/' + b'b'*40 + b'.yup')

    def fixture_capture(self,root,body):
        sha=hashlib.sha1(body).hexdigest();url='https://cdn.gaijinent.com/'+sha+'.yup'
        responses={VERSION_URL:b'2.59.0.92',MANIFEST_URL:('release\n'+url).encode(),url:body}
        def fetch(request,timeout):
            response=BytesIO(responses[request.full_url]);response.url=request.full_url;return response
        with patch('wt_atlas.client_manifest.urlopen',side_effect=fetch) as network:
            receipt=capture(root)
            self.assertEqual(network.call_count,5)
            self.assertEqual(capture(root,offline=True),receipt)
            self.assertEqual(network.call_count,5)
        return receipt

    def test_capture_replays_offline_and_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture_capture(root,manifest({}))
            self.assertEqual(verify_capture(root)[1]['version'],'2.59.0.92')
            (root/'version.txt').write_bytes(b'2.59.0.93')
            with self.assertRaisesRegex(ValueError,'capture changed'):verify_capture(root)

    def test_updater_change_during_capture_never_creates_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            body=manifest({});sha=hashlib.sha1(body).hexdigest();url='https://cdn.gaijinent.com/'+sha+'.yup';calls={}
            def fetch(request,timeout):
                key=request.full_url;calls[key]=calls.get(key,0)+1
                value=({VERSION_URL:b'2.59.0.92',MANIFEST_URL:url.encode(),url:body})[key]
                if key==VERSION_URL and calls[key]>1:value=b'2.59.0.93'
                response=BytesIO(value);response.url=key;return response
            with patch('wt_atlas.client_manifest.urlopen',side_effect=fetch),self.assertRaisesRegex(ValueError,'changed during capture'):
                capture(directory)
            self.assertFalse((Path(directory)/'capture.json').exists())

    def test_actual_bytes_are_matched_without_promoting_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=root/'client';repo=root/'repository';client.mkdir();repo.mkdir()
            packs={'aces.vromfs.bin':b'aces','char.vromfs.bin':b'char','gui.vromfs.bin':b'gui'}
            grp=b'physical grp';files={**packs,'content/base/res/tanks/fixture.grp':grp};body=manifest(files)
            source=root/'capture';self.fixture_capture(source,body)
            (client/'warthunder.yup').write_bytes(body)
            for name,data in packs.items():(client/name).write_bytes(data)
            grpfile=repo/'data/raw/fixture.grp';grpfile.parent.mkdir(parents=True);grpfile.write_bytes(grp)
            sha=hashlib.sha256(grp).hexdigest();inv={'client_version':'2.59.0.92','items':[{'vehicle_id':'one','grp_sha256':sha,'raw_path':'data/raw/fixture.grp'},
                {'vehicle_id':'two','grp_sha256':sha,'raw_path':'data/raw/fixture.grp'}]}
            result=audit(source,client,repo,inv)
            self.assertTrue(result['installed_manifest_matches'])
            self.assertTrue(result['inventory_version_matches'])
            self.assertEqual(result['grp_counts'],{'matched':1});self.assertEqual(result['vehicles_matching_manifest'],2)
            self.assertEqual([p['status'] for p in result['packs']],['matched']*3)
            self.assertFalse(result['promoted_release_status']);self.assertFalse(result['publishable'])
            inv['client_version']='2.59.0.94'
            self.assertFalse(audit(source,client,repo,inv)['inventory_version_matches'])
            grpfile.write_bytes(b'changed')
            self.assertEqual(audit(source,client,repo,inv)['grp_counts'],{'local_sha256_mismatch':1})
            grpfile.unlink()
            self.assertEqual(audit(source,client,repo,inv)['grp_counts'],{'missing_local_file':1})

    def test_identical_hash_in_multiple_manifest_paths_remains_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=root/'client';repo=root/'repo';client.mkdir();repo.mkdir();grp=b'physical grp'
            body=manifest({'content/a.grp':grp,'content/b.grp':grp});source=root/'capture';self.fixture_capture(source,body)
            (client/'warthunder.yup').write_bytes(body);(repo/'round.grp').write_bytes(grp)
            inv={'items':[{'vehicle_id':'one','grp_sha256':hashlib.sha256(grp).hexdigest(),'raw_path':'round.grp'}]}
            result=audit(source,client,repo,inv)
            self.assertEqual(result['grp_counts'],{'ambiguous_manifest_entry':1})
            self.assertEqual({p['status'] for p in result['packs']},{'missing_local_file'})

    def test_cli_refuses_to_write_into_client_or_source_repository(self):
        spec=importlib.util.spec_from_file_location('audit_client_manifest_cli',ROOT/'scripts/audit_client_manifest.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=root/'client';repo=root/'source'
            for capture_root,output in ((client/'capture',root/'report.json'),
                                        (root/'capture',repo/'report.json')):
                argv=['audit','--capture-root',str(capture_root),'--client-root',str(client),
                      '--repository',str(repo),'--inventory',str(root/'inventory.json'),'--out',str(output),'--fetch']
                with self.subTest(capture_root=capture_root,output=output),patch.object(sys,'argv',argv),self.assertRaisesRegex(ValueError,'separate'):
                    cli.main()


if __name__=='__main__':unittest.main()
