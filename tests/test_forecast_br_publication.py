import copy
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.forecast.br_publication import (parse_sheet, audit_catalog, publication_url,
    sheet_urls, capture_publication, verify_capture)


def sheet(domain, body, *, css='', split=None):
    header = ('<td rowspan="2"></td><td colspan="2">Arcade</td><td colspan="2">Air Realistic</td>'
        '<td colspan="2">Ground Realistic and Simulator</td><td colspan="2">Air Simulator</td><td rowspan="2">Comments</td>') if domain == 'air' else (
        '<td rowspan="2">Ground Forces</td><td colspan="2">Arcade</td><td colspan="2">Realistic</td><td rowspan="2">Comments</td>')
    rows = [header, '<td>was</td><td>now</td>' * (4 if domain == 'air' else 2)] + body
    rows = [f'<tr><th id="0R{i}">{i+1}</th>{row}</tr>' for i, row in enumerate(rows)]
    chunks = [rows] if split is None else [rows[:split], rows[split:]]
    return ('<html><head><style>' + css + '</style></head><body>' + ''.join(
        '<table class="waffle"><tbody>' + ''.join(chunk) + '</tbody></table>' for chunk in chunks) + '</body></html>').encode('utf-8')


def cells(*values):
    return ''.join('<td>' + value + '</td>' for value in values)


class PublishedBRTests(unittest.TestCase):
    def test_air_ground_column_is_distinct_from_air_rb(self):
        data = sheet('air', [cells('Fixture', '10.0', '10.3', '11.0', '11.3', '9.0', '9.7', '', '', '')])
        row = parse_sheet(data, 'air')['rows'][0]
        self.assertEqual(row['changes']['ground_rb'], {'old': 9., 'new': 9.7})
        self.assertEqual(row['changes']['air_rb']['new'], 11.3)

    def test_revised_value_ignores_struck_proposal_and_retains_raw_evidence(self):
        data = sheet('ground', [cells('Fixture', '', '', '12.0',
            '<span>12.7</span> <span style="text-decoration:line-through;color:red">12.3</span>', '')])
        row = parse_sheet(data, 'ground')['rows'][0]
        self.assertEqual(row['changes']['ground_rb']['new'], 12.7)
        self.assertTrue(row['revision_markup_present'])
        self.assertEqual(row['raw_cells'][4], '12.7 12.3')

    def test_css_strikethrough_and_tail_text_are_handled(self):
        data = sheet('ground', [cells('Fixture', '', '', '1.0', '<span class="removed">1.3</span> 1.7', '')],
            css='.waffle .removed{text-decoration:line-through;}')
        self.assertEqual(parse_sheet(data, 'ground')['rows'][0]['changes']['ground_rb']['new'], 1.7)

    def test_withdrawn_name_does_not_create_an_active_change(self):
        data = sheet('ground', [cells('<del>Fixture</del>', '', '', '<s>1.0</s>', '<s>1.3</s>', '')])
        self.assertEqual(parse_sheet(data, 'ground')['rows'][0]['kind'], 'withdrawn')

    def test_multiple_active_values_incomplete_pair_and_wrong_header_fail(self):
        for old, new in [('1.0', '1.3 1.7'), ('', '1.3'), ('1.0', '1.4')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                parse_sheet(sheet('ground', [cells('Fixture', '', '', old, new, '')]), 'ground')
        with self.assertRaisesRegex(ValueError, 'mode headers'):
            parse_sheet(sheet('ground', []).replace(b'Realistic', b'Simulator'), 'ground')

    def test_google_chunked_tables_must_cover_contiguous_rows(self):
        data = sheet('ground', [cells('One', '', '', '1.0', '1.3', ''), cells('Two', '', '', '2.0', '2.3', '')], split=3)
        result = parse_sheet(data, 'ground')
        self.assertEqual(result['source_chunk_count'], 2)
        self.assertEqual([r['name'] for r in result['rows']], ['One', 'Two'])
        for replacement in (b'0R4', b'1R3', b'0R2'):
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, 'published sheet rows'):
                parse_sheet(data.replace(b'0R3', replacement), 'ground')

    def test_non_br_weapon_changes_are_preserved_separately(self):
        data = sheet('ground', [cells('Fixture', '', '', '1.0', '1.3', ''),
            '<td colspan="6">Concomitant balance changes for ground vehicles without BR changes</td>',
            '<td>Fixture</td><td colspan="5">Round X replaced by round Y.</td>'])
        result = parse_sheet(data, 'ground')
        self.assertEqual(result['rows'][-1]['kind'], 'balance_note')
        self.assertEqual(result['rows'][-1]['text'], 'Round X replaced by round Y.')

    def test_utf8_names_and_whitespace_only_audit_never_modify_catalog(self):
        catalog = {'vehicles': [{'id': 'fixture', 'kind': 'ground', 'name': 'Танк\u00a0X', 'br': {'current': 1.3}}]}
        before = copy.deepcopy(catalog)
        result = audit_catalog(catalog, [parse_sheet(sheet('ground', [cells('Танк X', '', '', '1.0', '1.3', '')]), 'ground')])
        self.assertEqual(result['counts'], {'matches_current_rating': 1})
        self.assertEqual(catalog, before)
        self.assertFalse(result['promoted_release_status'])
        self.assertFalse(result['publishable'])

    def test_ambiguous_duplicate_and_unmatched_names_are_not_import_authority(self):
        catalog = {'vehicles': [{'id': str(i), 'kind': 'ground', 'name': 'Same', 'br': {'current': 1.3}} for i in range(2)]}
        data = sheet('ground', [cells(name, '', '', '1.0', '1.3', '') for name in ('Same', 'Missing', 'Twice', 'Twice')])
        result = audit_catalog(catalog, [parse_sheet(data, 'ground')])
        self.assertEqual(result['counts'], {'ambiguous_catalog_name': 1, 'unmatched_catalog_name': 1, 'duplicate_publication_name': 2})

    def test_air_rb_only_rows_do_not_imply_ground_change_for_helicopter(self):
        catalog = {'vehicles': [{'id': 'heli', 'kind': 'helicopter', 'name': 'Fixture', 'br': {'current': 8.}}]}
        data = sheet('air', [cells('Fixture', '', '', '8.0', '8.3', '', '', '', '', '')])
        self.assertEqual(audit_catalog(catalog, [parse_sheet(data, 'air')])['counts'], {'no_explicit_ground_rb_change': 1})

    def test_capture_follows_official_link_chain_and_replays_offline(self):
        announcement_url = 'https://warthunder.com/en/game/changelog/current/fixture'
        root_url = 'https://docs.google.com/spreadsheets/d/e/fixture/pubhtml'
        declaration = 'items.push({name: "%s", pageUrl: "%s/sheet?headers\\x3dfalse&gid=%s", gid: "%s"});'
        publication = ('<script>' + declaration % ('Aircraft', root_url, '1', '1') +
            declaration % ('Ground Forces', root_url, '0', '0') + '</script>').encode()
        payloads = {announcement_url: ('<a href="' + root_url + '">BR table</a>').encode(), root_url: publication,
            root_url + '/sheet?headers=false&gid=1': sheet('air', []),
            root_url + '/sheet?headers=false&gid=0': sheet('ground', [])}
        def fetch(request, timeout):
            response = BytesIO(payloads[request.full_url]); response.url = request.full_url
            return response
        with tempfile.TemporaryDirectory() as directory:
            with patch('wt_atlas.forecast.br_publication.urlopen', side_effect=fetch) as network:
                receipt = capture_publication(directory, announcement_url)
                self.assertEqual(network.call_count, 4)
                self.assertEqual(capture_publication(directory, None, offline=True), receipt)
                self.assertEqual(network.call_count, 4)
            (Path(directory) / 'ground.html').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'capture changed'):
                verify_capture(directory)

    def test_unlinked_or_conflicting_workbooks_and_sheet_urls_fail(self):
        with self.assertRaises(ValueError):
            publication_url(b'<a href="https://example.test/">not a publication</a>')
        root_url = 'https://docs.google.com/spreadsheets/d/e/fixture/pubhtml'
        publication = ('items.push({name: "Aircraft", pageUrl: "https://example.test/sheet?headers=false&gid=1", gid: "1"});').encode()
        with self.assertRaisesRegex(ValueError, 'external'):
            sheet_urls(publication, root_url)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'HTTPS announcement'):
                capture_publication(directory, 'https://example.test/announcement')


if __name__ == '__main__':
    unittest.main()
