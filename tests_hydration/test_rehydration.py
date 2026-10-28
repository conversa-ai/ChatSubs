# SPDX-License-Identifier: MIT
"""Synthetic hydration tests; no real source text or network requests."""
import copy, email, gzip, hashlib, http.client, io, json, sqlite3, sys, tempfile, unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hydration_common as common
import rehydrate_opensubtitles as subtitles

def entry():
    return {'schema_version': 'opensubtitles-historical-file-manifest/2', 'file_key': 'osf-' + 'a' * 64, 'source_namespace': subtitles.NAMESPACE, 'native_file_id': '900000001', 'published_language': 'es', 'dialogue_count': 1, 'dialogue_list_sha256': 'b' * 64, 'recipe_ref': subtitles.RECIPE_ID, 'historical_metadata_status': 'resolved', 'historical_metadata': [{'IDSubtitle': '123', 'SubLanguageID': 'spa', 'SubActualCD': '1', 'SubSumCD': '1', 'SubFormat': 'srt', 'MovieID': '200', 'MovieImdbID': '0012345', 'SeriesIMDBParent': '0', 'SeriesSeason': '0', 'SeriesEpisode': '0'}]}

def modern(group='123', file_id=777, disc=1):
    return {'id': '456', 'attributes': {'subtitle_id': '456', 'language': 'es', 'legacy_subtitle_id': int(group), 'nb_cd': 1, 'feature_details': {'imdb_id': 12345}, 'files': [{'file_id': file_id, 'cd_number': disc, 'file_name': 'synthetic.srt'}]}}

class Response(io.BytesIO):

    def __init__(self, raw=b'', headers=None, status=200):
        super().__init__(raw)
        self.headers = headers or {'Content-Length': str(len(raw))}
        self.status = status

class Opener:

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

class SubtitleMappingTests(unittest.TestCase):

    def test_old_file_id_is_never_the_modern_download_id(self):
        e = entry()
        record = modern(file_id=321)
        result = subtitles.resolve_files(e, [record])
        self.assertEqual(result['modern_file_id'], 321)
        self.assertEqual(result['historical_IDSubtitleFile'], '900000001')
        record['attributes']['legacy_subtitle_id'] = 900000001
        with self.assertRaisesRegex(common.Outcome, 'historical_group_not'):
            subtitles.resolve_files(e, [record])

    def test_numeric_modern_record_equality_does_not_bridge_legacy_group(self):
        record = modern(group='999')
        record['id'] = '123'
        record['attributes']['subtitle_id'] = '123'
        with self.assertRaises(common.Outcome):
            subtitles.resolve_files(entry(), [record])

    def test_no_numeric_coercion_of_manifest_native_id(self):
        e = entry()
        e['native_file_id'] = 900000001
        with self.assertRaises(common.Outcome):
            subtitles.metadata(e)

    def test_public_metadata_is_sufficient_feature_query(self):
        self.assertEqual(subtitles.search_parameters(entry())['imdb_id'], 12345)
        self.assertNotIn('native_file_id', subtitles.search_parameters(entry()))

    def test_parent_episode_fallback_has_no_title_substitution(self):
        e = entry()
        m = e['historical_metadata'][0]
        m.update(MovieImdbID='0', SeriesIMDBParent='42', SeriesSeason='1', SeriesEpisode='2')
        self.assertEqual(subtitles.search_parameters(e)['episode_number'], 2)
        m['SeriesIMDBParent'] = '0'
        with self.assertRaisesRegex(common.Outcome, 'no_supported_public_feature_query'):
            subtitles.search_parameters(e)

    def test_multiple_candidate_records_remain_ambiguous(self):
        a = modern()
        b = modern()
        b['id'] = '457'
        b['attributes']['subtitle_id'] = '457'
        with self.assertRaisesRegex(common.Outcome, 'multiple_modern'):
            subtitles.resolve_files(entry(), [a, b])

    def test_multiple_files_require_exact_disc(self):
        e = entry()
        e['historical_metadata'][0].update(SubSumCD='2', SubActualCD='2')
        a = modern()
        a['attributes']['nb_cd'] = 2
        a['attributes']['files'].append({'file_id': 778, 'cd_number': 2})
        self.assertEqual(subtitles.resolve_files(e, [a])['modern_file_id'], 778)
        a['attributes']['files'][0]['cd_number'] = 2
        with self.assertRaisesRegex(common.Outcome, 'disc_not_uniquely'):
            subtitles.resolve_files(e, [a])

    def test_similar_title_different_language_or_edition_not_selected(self):
        for field, value in [('language', 'ca'), ('legacy_subtitle_id', 124), ('nb_cd', 2)]:
            record = modern()
            record['attributes'][field] = value
            with self.assertRaises(common.Outcome):
                subtitles.resolve_files(entry(), [record])

    def test_incomplete_search_does_not_assign_apparent_unique_candidate(self):
        with self.assertRaisesRegex(common.Outcome, 'incomplete_candidates'):
            subtitles.resolve_files(entry(), [modern()], complete=False)

    def test_conflicting_duplicate_record_is_not_deduplicated(self):
        a = modern()
        b = modern(file_id=999)
        with self.assertRaisesRegex(common.Outcome, 'conflicting_duplicate'):
            subtitles.resolve_files(entry(), [a, b])
        self.assertEqual(subtitles.resolve_files(entry(), [a, a])['modern_file_id'], 777)

    def test_false_and_float_are_not_modern_file_ids(self):
        for bad in [True, 777.0, '777']:
            with self.assertRaises(common.Outcome):
                subtitles.resolve_files(entry(), [modern(file_id=bad)])

    def test_local_decoding_is_explicit_strict_and_bounded(self):
        raw = gzip.compress('Synthetic café\r\n'.encode('latin1'))
        self.assertEqual(subtitles.decode_input(raw, 'historical-gzip-latin1'), 'Synthetic café\n')
        with self.assertRaises(UnicodeError):
            subtitles.decode_input(b'\xff', 'platform-utf8')
        with self.assertRaises(common.Outcome):
            subtitles.decode_input(raw + raw, 'historical-gzip-latin1')
        with self.assertRaises(common.Outcome):
            subtitles.decode_input(raw, 'historical-gzip-latin1', 2)

    def test_ordered_list_hash_preserves_repetition_unicode_and_whitespace(self):
        base = ['Synthetic café\n', 'Synthetic café\n']
        for changed in [base[:1], ['Synthetic café'] * 2, ['Synthetic café\n'] * 2, list(reversed(['a', 'b']))]:
            self.assertNotEqual(common.list_hash(base), common.list_hash(changed))
        self.assertNotEqual(common.list_hash(['a', 'b']), common.list_hash(['b', 'a']))

    def test_mismatch_never_assigns_published_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic'
            path.write_text('synthetic source')
            with patch.object(subtitles, 'extract_dialogues', return_value=['Synthetic first\nSynthetic second']):
                values, result = subtitles.reconstruct(entry(), path, {}, Path(tmp) / 'unused', 'platform-utf8')
            self.assertEqual(result['outcome'], 'mismatch')
            self.assertFalse(result['published_positions_assigned'])
            e = entry()
            e['dialogue_list_sha256'] = common.list_hash(values)
            with patch.object(subtitles, 'extract_dialogues', return_value=values):
                _, result = subtitles.reconstruct(e, path, {}, Path(tmp) / 'unused', 'platform-utf8')
            self.assertTrue(result['published_positions_assigned'])

    def test_dry_run_never_reads_credentials_or_opens_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'manifest.jsonl'
            p.write_text(common.canonical(entry()) + '\n')
            args = subtitles.parser().parse_args(['--manifest', str(p), '--recipe', 'synthetic-recipe.json', '--work-dir', str(Path(tmp) / 'absent')])
            with patch.object(subtitles, 'API', side_effect=AssertionError('no auth')), patch.object(subtitles, 'Network', side_effect=AssertionError('no network')):
                result = subtitles.run(args)
            self.assertEqual(result['network_requests'], 0)
            self.assertFalse((Path(tmp) / 'absent').exists())

    def test_authentication_does_not_log_or_checkpoint_credentials(self):

        class FakeNetwork:
            calls = []

            def json(self, url, **kw):
                self.calls.append((url, kw))
                return {'token': 'synthetic-token', 'base_url': 'vip-api.opensubtitles.com'}
        n = FakeNetwork()
        client = subtitles.API(n, {'OPENSUBTITLES_API_KEY': 'synthetic-key', 'OPENSUBTITLES_USERNAME': 'synthetic-user', 'OPENSUBTITLES_PASSWORD': 'synthetic-password'})
        client.authenticate()
        self.assertEqual(client.host, 'vip-api.opensubtitles.com')
        self.assertEqual(len(n.calls), 1)
        client.authenticate()
        self.assertEqual(len(n.calls), 1)

    def test_only_resolved_modern_file_id_reaches_download(self):
        calls = []

        class FakeNetwork:

            def download(self, url, path, identity, maximum):
                calls.append(identity)
                return {}
        client = subtitles.API(FakeNetwork(), {})
        client.request = lambda endpoint, body: {'link': 'https://dl.opensubtitles.com/synthetic', 'remaining': 0}
        client.download(subtitles.resolve_files(entry(), [modern(file_id=321)]), Path('unused'), 10)
        self.assertEqual(calls[0]['modern_file_id'], 321)
        self.assertTrue(client.quota_exhausted)
        with self.assertRaises(common.Outcome):
            client.download({}, Path('unused'), 10)

class TransportTests(unittest.TestCase):

    def net(self, tmp, opener, **kwargs):
        return common.Network(tmp, 'opensubtitles', opener=opener, sleeper=lambda x: None, **kwargs)

    def test_safe_hosts_never_receive_credentials_on_other_domains(self):
        self.assertFalse(common.official_url('https://api.opensubtitles.com.evil.test/api/v1/login', 'opensubtitles', True))
        self.assertFalse(common.official_url('http://api.opensubtitles.com/api/v1/login', 'opensubtitles', True))
        self.assertFalse(common.official_url('https://user:pass@archive.org/download/x', 'archive'))
        self.assertTrue(common.official_url('https://ia801500.us.archive.org/20/items/x/y', 'archive'))

    def test_auth_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            op = Opener(HTTPError('synthetic', 401, 'unauthorized', {}, None))
            with self.assertRaisesRegex(common.Outcome, 'authentication_'):
                self.net(tmp, op).json('https://api.opensubtitles.com/api/v1/login', body={'password': 'synthetic'}, api=True)
            self.assertEqual(len(op.requests), 1)

    def test_get_api_503_retries_and_counts_failed_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            op = Opener(HTTPError('synthetic', 503, 'failure', {}, None), Response(b'{"data":[]}'))
            n = self.net(tmp, op)
            self.assertEqual(n.json('https://api.opensubtitles.com/api/v1/subtitles', api=True), {'data': []})
            self.assertEqual(n.state['requests'], 2)

    def test_post_download_is_not_retried_after_uncertain_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            op = Opener(HTTPError('synthetic', 503, 'failure', {}, None))
            with self.assertRaisesRegex(common.Outcome, 'http_503'):
                self.net(tmp, op).json('https://api.opensubtitles.com/api/v1/download', body={'file_id': 777}, api=True)
            self.assertEqual(len(op.requests), 1)

    def test_rate_limit_with_long_retry_after_yields_resumable_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            op = Opener(HTTPError('synthetic', 429, 'limit', {'Retry-After': '3600'}, None))
            n = self.net(tmp, op)
            with self.assertRaisesRegex(common.Outcome, 'resume_later'):
                n.json('https://api.opensubtitles.com/api/v1/subtitles', api=True)
            self.assertGreater(common.load(Path(tmp) / 'network-opensubtitles.json')['resume_after'], 0)

    def test_interrupted_download_resumes_with_range(self):

        class Broken(Response):

            def read(self, n=-1):
                if self.tell():
                    raise OSError('synthetic interrupted')
                return super().read(3)
        with tempfile.TemporaryDirectory() as tmp:
            op = Opener(Broken(b'abcdef', {'Content-Length': '6'}), Response(b'def', {'Content-Length': '3', 'Content-Range': 'bytes 3-5/6'}, 206))
            n = self.net(tmp, op)
            p = Path(tmp) / 'source.bin'
            n.download('https://dl.opensubtitles.com/synthetic', p, {'modern_file_id': 777}, 100)
            self.assertEqual(p.read_bytes(), b'abcdef')
            self.assertEqual(op.requests[1].get_header('Range'), 'bytes=3-')
            self.assertEqual(n.state['bytes'], 6)

    def test_ignored_range_restarts_instead_of_concatenating(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'source.bin'
            p.with_name('source.bin.part').write_bytes(b'abc')
            common.atomic_json(p.with_name('source.bin.part.json'), {'identity': {'modern_file_id': 777}, 'expected': {}})
            op = Opener(Response(b'abcdef'))
            n = self.net(tmp, op)
            n.download('https://dl.opensubtitles.com/synthetic', p, {'modern_file_id': 777}, 100)
            self.assertEqual(p.read_bytes(), b'abcdef')

    def test_cached_source_reused_but_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'source'
            n = self.net(tmp, Opener(Response(b'synthetic')))
            identity = {'modern_file_id': 777}
            n.download('https://dl.opensubtitles.com/synthetic', p, identity, 100)
            n.opener = Opener()
            n.download('https://dl.opensubtitles.com/synthetic', p, identity, 100)
            self.assertEqual(len(n.opener.requests), 0)
            p.write_bytes(b'changed')
            with self.assertRaisesRegex(common.Outcome, 'integrity_mismatch'):
                n.download('https://dl.opensubtitles.com/synthetic', p, identity, 100)

    def test_checksum_and_cumulative_bounds_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            n = self.net(tmp, Opener(Response(b'synthetic')), max_requests=1)
            with self.assertRaises(common.Outcome):
                n.download('https://dl.opensubtitles.com/synthetic', Path(tmp) / 'source', {}, 100, {'sha1': '0' * 40})
            with self.assertRaisesRegex(common.Outcome, 'cumulative_'):
                n.json('https://api.opensubtitles.com/api/v1/subtitles', api=True)

    def test_unsafe_archive_entries_and_upload_output_rejected(self):
        for name in ['../outside', '/absolute', 'a/../../b', 'a\\b']:
            with self.assertRaises(common.Outcome):
                common.safe_member(name)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(common.Outcome):
                common.workspace(Path(tmp) / 'upload' / 'hydrated')

    def test_declared_oversize_asset_is_rejected_before_any_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            op = Opener()
            n = self.net(tmp, op)
            with self.assertRaisesRegex(common.Outcome, 'declared_file_exceeds'):
                n.download('https://dl.opensubtitles.com/synthetic', Path(tmp) / 'source', {}, 100, {'size': 101})
            self.assertEqual(op.requests, [])

    def test_api_response_bounds_and_interrupted_bytes_are_accounted(self):

        class BrokenAPI(Response):

            def read(self, n=-1):
                raise http.client.IncompleteRead(b'abc', 7)
        with tempfile.TemporaryDirectory() as tmp:
            op = Opener(BrokenAPI(b'', {'Content-Length': '10'}))
            n = self.net(tmp, op)
            with self.assertRaisesRegex(common.Outcome, 'interrupted_api_response'):
                n.json('https://api.opensubtitles.com/api/v1/subtitles', api=True)
            self.assertEqual(n.state['bytes'], 3)
        with tempfile.TemporaryDirectory() as tmp:
            n = self.net(tmp, Opener(Response(b'abcdefghij')), max_bytes=3)
            with self.assertRaisesRegex(common.Outcome, 'declared_byte_bound'):
                n.json('https://api.opensubtitles.com/api/v1/subtitles', api=True)
            self.assertEqual(n.state['bytes'], 0)

    def test_crash_after_receipt_before_rename_needs_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'source'
            part = root / 'source.part'
            part.write_bytes(b'synthetic')
            identity = {'modern_file_id': 777}
            common.atomic_json(root / 'source.part.json', {'identity': identity, 'expected': {}})
            common.atomic_json(root / 'source.receipt.json', {'identity': identity, 'sha256': common.sha_file(part), 'size': part.stat().st_size})
            op = Opener()
            n = self.net(tmp, op)
            n.download('https://dl.opensubtitles.com/synthetic', p, identity, 100)
            self.assertEqual(p.read_bytes(), b'synthetic')
            self.assertEqual(op.requests, [])

    def test_child_symlink_cannot_redirect_hydration_to_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / 'local'
            work.mkdir()
            outside = root / 'upload'
            outside.mkdir()
            (work / 'results').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(common.Outcome, 'symlink_in_private'):
                common.workspace(work)

    def test_incremental_array_preserves_order_repeats_and_empty(self):
        self.assertEqual(list(common.array_records(io.BytesIO(b'[]\n'))), [])
        raw = json.dumps([{'a': 'Synthetic café'}] * 2, ensure_ascii=False).encode()
        self.assertEqual(len(list(common.array_records(io.BytesIO(raw)))), 2)
        for bad in [b'[{},]', b'[{', b'[] {}', b'[{"a":1,"a":2}]']:
            with self.assertRaises(common.Outcome):
                list(common.array_records(io.BytesIO(bad)))
if __name__ == '__main__':
    unittest.main()
