import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from wt_atlas.aviation_source_audit import audit,article_claims,ARTICLE,SHIP_LEGEND,UNUSED_LEGEND


class AviationSourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.unit=b'<html><body><p>The KOR-2 was introduced in Update 2.49 as a catapult aircraft.</p></body></html>'
        (self.root/'unit_kor_2.html.gz').write_bytes(gzip.compress(self.unit))
        self.article=('<html><body><table><tr><td><a href="/unit/kor_2">KOR-2</a>\u2021</td></tr></table>'
                      '<p>'+SHIP_LEGEND+'</p><p>'+UNUSED_LEGEND+'</p></body></html>').encode('utf8')
        self.article_path=self.root/'shipboard.html';self.article_path.write_bytes(self.article)
        self.article_sha=hashlib.sha256(self.article).hexdigest()
        self.catalog={'snapshot':{'wiki_collected_at':'test'},'vehicles':[{'id':'kor_2','name':'KOR-2',
            'wiki_url':'https://wiki.warthunder.com/unit/kor_2','provenance':[{'url':'https://wiki.warthunder.com/unit/kor_2',
            'sha256':hashlib.sha256(self.unit).hexdigest()}]}]}
        self.gaps={'rating_and_eligibility':{'unknown_ground_rb_rating_ids':['kor_2'],
            'historical_official_evidence':[{'url':ARTICLE,'sha256':self.article_sha}]}}
    def tearDown(self):self.temp.cleanup()
    def run_audit(self):return audit(self.catalog,self.gaps,self.article_path,self.article_sha,self.root)

    def test_contradiction_is_reported_without_excluding_the_unit(self):
        result=self.run_audit();self.assertEqual(result['conflicting_unit_ids'],['kor_2'])
        row=result['claims'][0];self.assertEqual(row['article_claim'],'unused_in_game')
        self.assertEqual(row['explicit_unit_introductions'][0]['update'],'2.49')
        self.assertEqual(row['ground_rb_eligibility'],'unresolved')
        self.assertFalse(result['ground_rb_eligibility_promoted']);self.assertFalse(result['publishable'])

    def test_source_or_identity_changes_are_rejected(self):
        self.article_path.write_bytes(self.article+b'changed')
        with self.assertRaisesRegex(ValueError,'article checksum'):self.run_audit()
        self.article_path.write_bytes(self.article)
        self.gaps['rating_and_eligibility']['historical_official_evidence'][0]['url']='https://example.invalid'
        with self.assertRaisesRegex(ValueError,'source receipt'):self.run_audit()
        self.gaps['rating_and_eligibility']['historical_official_evidence'][0]['url']=ARTICLE
        (self.root/'unit_kor_2.html.gz').write_bytes(gzip.compress(self.unit+b'changed'))
        with self.assertRaisesRegex(ValueError,'unit Wiki checksum|Unit Wiki checksum'):self.run_audit()
        (self.root/'unit_kor_2.html.gz').write_bytes(gzip.compress(self.unit))
        self.catalog['vehicles'][0]['wiki_url']='https://wiki.warthunder.com/unit/other'
        with self.assertRaisesRegex(ValueError,'URL mismatch'):self.run_audit()

    def test_unrelated_introduction_does_not_resolve_or_create_conflict(self):
        for raw in (b'<html><body><p>KOR-2 was not introduced in Update 2.49.</p></body></html>',
                    b'<html><body><p>KOR-2 is comparable. Another aircraft was introduced in Update 2.49.</p></body></html>',
                    b'<html><body><p>Other-KOR-2 was introduced in Update 2.49.</p></body></html>'):
            (self.root/'unit_kor_2.html.gz').write_bytes(gzip.compress(raw))
            self.catalog['vehicles'][0]['provenance'][0]['sha256']=hashlib.sha256(raw).hexdigest()
            result=self.run_audit();self.assertEqual(result['conflicting_unit_ids'],[])
            self.assertEqual(result['claims'][0]['ground_rb_eligibility'],'unresolved')

    def test_changed_legend_and_duplicate_source_identity_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'legend changed'):article_claims(b'<html><p>no legend</p></html>')
        duplicated=self.article.replace(b'</table>',b'<tr><td><a href="/unit/kor_2">KOR-2</a>\xe2\x80\xa1</td></tr></table>')
        with self.assertRaisesRegex(ValueError,'Ambiguous'):article_claims(duplicated)


if __name__=='__main__':unittest.main()
