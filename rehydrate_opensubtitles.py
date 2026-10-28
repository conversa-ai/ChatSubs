#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Rehydrate bounded selections from the public esCorpiusDialog 1.3 manifest.

Default is a network-free dry run. --source api --execute enables official API
requests; --source local --execute uses only user-supplied files. All recovered
text, candidate mappings and state remain in --work-dir. No author mapping is
required. Historical and modern identifiers are never treated as interchangeable.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
from pathlib import Path
import re
import sys
import zlib

from hydration_common import (MIB, Network, Outcome, add_network_arguments,
    atomic_json, canonical, cli_error, jsonl, list_hash, load, sha_bytes,
    sha_file, workspace)

RECIPE_ID = 'chatsubs-retained-defaults/1'
SNAPSHOT_SHA256 = '9d8e7e545fefd39a8731a11a05dc41ba192bdc5875334aa0fd0b02e462903941'
NAMESPACE = 'opensubtitles.org.IDSubtitleFile'
LANGUAGES = {'es': 'spa', 'ca': 'cat', 'eu': 'baq', 'gl': 'glg'}
API_HOSTS = ('api.opensubtitles.com', 'vip-api.opensubtitles.com')


def metadata(entry):
    if (entry.get('source_namespace') != NAMESPACE
            or entry.get('schema_version') != 'opensubtitles-historical-file-manifest/2'
            or type(entry.get('native_file_id')) is not str
            or not re.fullmatch('[0-9]+', entry['native_file_id'])
            or not re.fullmatch('osf-[0-9a-f]{64}', entry.get('file_key', ''))
            or entry.get('published_language') not in LANGUAGES
            or entry.get('recipe_ref') != RECIPE_ID
            or type(entry.get('dialogue_count')) is not int or entry['dialogue_count'] < 0
            or not re.fullmatch('[0-9a-f]{64}', entry.get('dialogue_list_sha256', ''))):
        raise Outcome('error', 'unsupported_public_manifest_entry')
    values = entry.get('historical_metadata')
    if entry.get('historical_metadata_status') != 'resolved' or not isinstance(values, list) or len(values) != 1:
        raise Outcome('ambiguous', 'historical_metadata_not_uniquely_resolved')
    value = values[0]
    if value.get('SubLanguageID') != LANGUAGES[entry['published_language']]:
        raise Outcome('mismatch', 'historical_language_disagrees')
    if not re.fullmatch('[0-9]+', value.get('IDSubtitle', '')):
        raise Outcome('unavailable', 'historical_group_id_unavailable')
    return value


def external_id(value):
    # IMDb IDs are documented API query parameters. This conversion is NEVER
    # applied to native_file_id, historical IDSubtitle, or a download identifier.
    return int(value) if type(value) is str and value.isascii() and value.isdigit() and int(value) > 0 else None


def search_parameters(entry):
    value = metadata(entry)
    parameters = {'languages': entry['published_language'], 'ai_translated': 'include',
                  'machine_translated': 'include'}
    imdb = external_id(value.get('MovieImdbID'))
    parent = external_id(value.get('SeriesIMDBParent'))
    if imdb:
        parameters['imdb_id'] = imdb
    elif parent and str(value.get('SeriesSeason', '')).isdigit() and str(value.get('SeriesEpisode', '')).isdigit():
        parameters.update(parent_imdb_id=parent, season_number=int(value['SeriesSeason']),
                          episode_number=int(value['SeriesEpisode']))
    else:
        raise Outcome('unavailable', 'no_supported_public_feature_query')
    return parameters


def identifier(value):
    if type(value) is int and value > 0:
        return str(value)
    if type(value) is str and re.fullmatch('[0-9]+', value):
        return value
    return None


def resolve_files(entry, records, complete=True):
    """Resolve legacy group -> modern record -> modern file, without title fallback.

    All search pages must have been inspected. A supported counterpart mapping is
    evidence of identifier resolution, never proof of historical source bytes.
    """
    old = metadata(entry)
    if not complete:
        raise Outcome('ambiguous', 'search_page_bound_incomplete_candidates')
    groups = {}
    for record in records:
        attributes = record.get('attributes', {})
        if identifier(attributes.get('legacy_subtitle_id')) != old['IDSubtitle']:
            continue
        if attributes.get('language') != entry['published_language']:
            continue
        modern = identifier(record.get('id'))
        if not modern or identifier(attributes.get('subtitle_id', modern)) != modern:
            raise Outcome('ambiguous', 'modern_record_id_conflict')
        feature = attributes.get('feature_details') or {}
        if external_id(old.get('MovieImdbID')) and feature.get('imdb_id') is not None:
            if identifier(feature['imdb_id']) != str(external_id(old['MovieImdbID'])):
                continue
        total_cd = external_id(old.get('SubSumCD'))
        if total_cd and attributes.get('nb_cd') is not None and attributes['nb_cd'] != total_cd:
            continue
        projection = {'id': modern, 'files': attributes.get('files'), 'nb_cd': attributes.get('nb_cd')}
        if modern in groups and canonical(groups[modern]) != canonical(projection):
            raise Outcome('ambiguous', 'conflicting_duplicate_modern_record')
        groups[modern] = projection
    if not groups:
        raise Outcome('unavailable', 'historical_group_not_in_complete_feature_results')
    if len(groups) != 1:
        raise Outcome('ambiguous', 'multiple_modern_records_for_historical_group')
    group = next(iter(groups.values()))
    files = group['files']
    if not isinstance(files, list) or not files:
        raise Outcome('unavailable', 'modern_record_has_no_files')
    if any(type(f.get('file_id')) is not int or f['file_id'] <= 0 for f in files):
        raise Outcome('ambiguous', 'invalid_modern_file_identifier')
    disc = external_id(old.get('SubActualCD'))
    matches = [f for f in files if disc and type(f.get('cd_number')) is int and f['cd_number'] == disc]
    basis = 'exact_legacy_group_language_and_disc'
    if not matches and len(files) == 1 and disc in (None, 1) and external_id(old.get('SubSumCD')) in (None, 1):
        # A missing modern CD number is acceptable only for a known single-CD record.
        if files[0].get('cd_number') in (None, 1):
            matches = files; basis = 'exact_legacy_group_language_single_file'
    if len(matches) != 1:
        raise Outcome('ambiguous', 'historical_disc_not_uniquely_mapped_to_modern_file')
    return {'historical_IDSubtitleFile': entry['native_file_id'],
            'historical_IDSubtitle': old['IDSubtitle'], 'modern_subtitle_id': group['id'],
            'modern_file_id': matches[0]['file_id'], 'mapping_basis': basis,
            'historical_source_bytes_verified': False}


class API:
    def __init__(self, network, environment=None):
        self.network = network; self.environment = os.environ if environment is None else environment
        self.key = self.environment.get('OPENSUBTITLES_API_KEY')
        self.token = self.environment.get('OPENSUBTITLES_TOKEN')
        self.host = self.environment.get('OPENSUBTITLES_API_HOST', API_HOSTS[0])
        if self.host not in API_HOSTS:
            raise Outcome('error', 'unsupported_api_host')
        self.authenticated = False; self.quota_exhausted = False

    def authenticate(self):
        if self.authenticated:
            return
        if not self.key:
            raise Outcome('error', 'missing_environment_api_key')
        if not self.token:
            username = self.environment.get('OPENSUBTITLES_USERNAME')
            password = self.environment.get('OPENSUBTITLES_PASSWORD')
            if not username or not password:
                raise Outcome('error', 'missing_environment_user_credentials')
            response = self.network.json('https://' + self.host + '/api/v1/login',
                headers={'Api-Key': self.key}, body={'username': username, 'password': password}, api=True)
            token = response.get('token')
            host = response.get('base_url', self.host)
            if host not in API_HOSTS or type(token) is not str or not token:
                raise Outcome('error', 'invalid_login_response')
            self.host, self.token = host, token
        self.authenticated = True

    def request(self, endpoint, parameters=None, body=None):
        from urllib.parse import urlencode
        self.authenticate()
        url = 'https://' + self.host + '/api/v1/' + endpoint
        if parameters:
            url += '?' + urlencode(sorted(parameters.items()))
        return self.network.json(url, headers={'Api-Key': self.key, 'Authorization': 'Bearer ' + self.token},
                                 body=body, api=True)

    def search(self, parameters, maximum_pages):
        records = []
        for page in range(1, maximum_pages + 1):
            result = self.request('subtitles', {**parameters, 'page': page})
            if not isinstance(result.get('data'), list) or type(result.get('total_pages')) is not int:
                raise Outcome('error', 'invalid_search_response')
            if result.get('page', page) != page or result['total_pages'] < 0:
                raise Outcome('error', 'invalid_search_pagination')
            records.extend(result['data'])
            if page >= result['total_pages']:
                return records, True
        return records, False

    def download(self, mapping, path, maximum_bytes):
        # This argument is ONLY emitted by resolve_files(), never native_file_id.
        if self.quota_exhausted:
            raise Outcome('unavailable', 'provider_quota_exhausted')
        result = self.request('download', body={'file_id': mapping['modern_file_id']})
        if type(result.get('link')) is not str:
            raise Outcome('error', 'download_link_missing')
        if result.get('remaining') == 0:
            self.quota_exhausted = True
        return self.network.download(result['link'], path,
            {'modern_file_id': mapping['modern_file_id'], 'modern_subtitle_id': mapping['modern_subtitle_id']}, maximum_bytes)


def decode_input(raw, mode, maximum_expanded=16*MIB):
    if mode == 'historical-gzip-latin1':
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        value = inflater.decompress(raw, maximum_expanded + 1)
        if (len(value) > maximum_expanded or inflater.unconsumed_tail or not inflater.eof or inflater.unused_data
                or len(value) > max(1, len(raw)) * 200):
            raise Outcome('error', 'unsupported_gzip_expansion_or_members')
        encoding = 'iso-8859-1'
    elif mode == 'platform-utf8':
        value = raw; encoding = 'utf-8'
        if len(value) > maximum_expanded:
            raise Outcome('error', 'source_expansion_bound')
    else:
        raise Outcome('error', 'unsupported_input_adapter')
    with io.TextIOWrapper(io.BytesIO(value), encoding=encoding, errors='strict', newline=None) as stream:
        return stream.read()


def extract_dialogues(text, recipe, snapshot):
    if (recipe.get('recipe_id') != RECIPE_ID
            or recipe.get('extraction_function_snapshot_sha256', recipe.get('snapshot', {}).get('sha256')) != SNAPSHOT_SHA256
            or sha_file(snapshot) != SNAPSHOT_SHA256
            or recipe.get('parameters') != {'time_threshold_seconds': 1, 'comparison_operator': '>',
                                          'debug': False, 'final_credit_filter': True}):
        raise Outcome('error', 'pinned_historical_recipe_or_code_differs')
    namespace = {'__builtins__': __builtins__}
    exec(compile(Path(snapshot).read_bytes(), '<pinned-historical-subtitle-functions>', 'exec'), namespace)
    namespace['debug'] = False
    namespace['print'] = lambda *args, **kwargs: None
    dialogues = namespace['extract_dialog'](text)
    if dialogues:
        dialogues = namespace['delete_final_clause'](dialogues)
    list_hash(dialogues)
    return dialogues


def reconstruct(entry, path, recipe, snapshot, mode):
    raw = Path(path).read_bytes()
    dialogues = extract_dialogues(decode_input(raw, mode), recipe, snapshot)
    fingerprint = list_hash(dialogues)
    exact = len(dialogues) == entry['dialogue_count'] and fingerprint == entry['dialogue_list_sha256']
    return dialogues, {'file_key': entry['file_key'], 'outcome': 'exact-match' if exact else 'mismatch',
        'downloaded': False, 'reconstructed': True, 'exact_match': exact,
        'expected_dialogue_count': entry['dialogue_count'], 'actual_dialogue_count': len(dialogues),
        'expected_dialogue_list_sha256': entry['dialogue_list_sha256'], 'actual_dialogue_list_sha256': fingerprint,
        'source_sha256': sha_bytes(raw), 'input_adapter': mode, 'published_positions_assigned': exact,
        'historical_decoding_and_source_equivalence': 'Established for the complete ordered output only when exact-match; historical source-byte identity remains unproven.',
        'recipe_limits': recipe.get('unresolved_settings', [])}


def selected_entries(args):
    keys = set(args.file_key or [])
    selected = []; eligible = 0; seen = set()
    for entry in jsonl(args.manifest):
        if keys and entry.get('file_key') not in keys:
            continue
        if args.language and entry.get('published_language') != args.language:
            continue
        if eligible >= args.offset and len(selected) < args.max_files:
            metadata(entry)
            if entry['file_key'] in seen:
                raise Outcome('error', 'duplicate_selected_file_key')
            seen.add(entry['file_key']); selected.append(entry)
        eligible += 1
    if keys and keys - seen:
        raise Outcome('error', 'requested_keys_missing_filtered_or_outside_batch')
    return selected


def run(args):
    if not 1 <= args.max_files <= 1000 or args.offset < 0 or not 1 <= args.max_pages <= 100:
        raise Outcome('error', 'invalid_selection_bounds')
    entries = selected_entries(args)
    plan = {'schema_version': 'opensubtitles-hydration-plan/1', 'network_requests': 0,
            'source': args.source, 'files': [], 'selected_files': len(entries)}
    for entry in entries:
        item = {'file_key': entry['file_key'], 'published_language': entry['published_language'],
                'native_file_id_namespace': NAMESPACE, 'native_file_id': entry['native_file_id']}
        try:
            item['feature_query'] = search_parameters(entry)
            item['outcome'] = 'planned'
        except Outcome as error:
            item.update(cli_error(error))
        plan['files'].append(item)
    if not args.execute:
        return plan  # No credential read, directory creation, authentication or request.
    work = workspace(args.work_dir, [args.manifest.parent])
    recipe = load(args.recipe)
    snapshot = args.snapshot or Path(__file__).with_name('historical') / 'retained-functions.py'
    local_index = load(args.local_index) if args.local_index else {}
    net = Network(work, 'opensubtitles', args.max_requests, args.max_total_bytes, args.retries) if args.source == 'api' else None
    api = API(net) if net else None
    results = []; stop_reason = None
    for entry in entries:
        key = entry['file_key']; result_path = work / 'results' / (key + '.json')
        result = {'file_key': key, 'outcome': 'unavailable', 'downloaded': False,
                  'reconstructed': False, 'published_positions_assigned': False}
        try:
            if result_path.exists() and not args.retry_outcomes:
                previous = load(result_path)
                if previous.get('manifest_entry_sha256') != sha_bytes(canonical(entry).encode('utf-8')):
                    raise Outcome('error', 'resume_manifest_entry_changed')
                if previous.get('source_path') and sha_file(previous['source_path']) != previous.get('source_sha256'):
                    raise Outcome('error', 'resume_source_integrity_mismatch')
                if previous.get('candidate_path') and sha_file(previous['candidate_path']) != previous.get('candidate_file_sha256'):
                    raise Outcome('error', 'resume_candidate_integrity_mismatch')
                results.append(previous); continue
            if stop_reason:
                raise Outcome('unavailable', stop_reason)
            if args.source == 'local':
                item = local_index.get(key)
                if not isinstance(item, dict) or not item.get('path'):
                    raise Outcome('unavailable', 'local_source_not_supplied')
                path = Path(item['path']).expanduser()
                if not path.is_absolute():
                    path = args.local_index.parent / path
                if not path.is_file():
                    raise Outcome('unavailable', 'local_source_missing')
                if path.stat().st_size > args.max_file_bytes:
                    raise Outcome('error', 'local_file_byte_bound')
                mode = item.get('input_adapter', 'platform-utf8')
                if item.get('sha256') and sha_file(path) != item['sha256']:
                    raise Outcome('mismatch', 'local_source_fingerprint_differs')
            else:
                parameters = search_parameters(entry)
                cache_key = sha_bytes(canonical(parameters).encode('utf-8'))
                cache = work / 'search' / (cache_key + '.json')
                if cache.exists() and not args.refresh_metadata:
                    cached = load(cache)
                    if cached['parameters'] != parameters:
                        raise Outcome('error', 'search_cache_query_differs')
                else:
                    records, complete = api.search(parameters, args.max_pages)
                    cached = {'parameters': parameters, 'records': records, 'complete': complete,
                              'max_pages': args.max_pages}
                    atomic_json(cache, cached)
                mapping = resolve_files(entry, cached['records'], cached['complete'])
                result['mapping'] = mapping
                atomic_json(work / 'mappings' / (key + '.json'), mapping)
                path = work / 'sources' / (key + '.bin')
                identity = {'modern_file_id': mapping['modern_file_id'], 'modern_subtitle_id': mapping['modern_subtitle_id']}
                receipt = path.with_name(path.name + '.receipt.json')
                if path.exists() and receipt.exists():
                    registered = load(receipt)
                    if registered['identity'] != identity or registered['sha256'] != sha_file(path):
                        raise Outcome('error', 'cached_source_integrity_mismatch')
                    Network.check_download(path, {}, args.max_file_bytes)
                else:
                    api.download(mapping, path, args.max_file_bytes)
                result['downloaded'] = True; mode = 'platform-utf8'
            dialogues, reconstructed = reconstruct(entry, path, recipe, snapshot, mode)
            result.update(reconstructed, downloaded=result['downloaded'], source_path=str(path.resolve()))
            candidate = work / 'candidates' / (key + '.json')
            atomic_json(candidate, dialogues)
            result.update(candidate_path=str(candidate), candidate_file_sha256=sha_file(candidate))
            if result['exact_match']:
                atomic_json(work / 'verified' / (key + '.json'), {'file_key': key, 'dialogues': dialogues,
                    'dialogue_index_range': [0, len(dialogues)], 'range_end_exclusive': True,
                    'dialogue_list_sha256': result['actual_dialogue_list_sha256']})
            if api and api.quota_exhausted:
                stop_reason = 'provider_quota_exhausted'
        except (Outcome, OSError, ValueError, KeyError, UnicodeError, zlib.error) as error:
            result.update(cli_error(error))
            if isinstance(error, Outcome) and (error.reason.startswith(('authentication_', 'missing_environment_', 'cumulative_', 'rate_limited', 'http_429', 'provider_quota'))):
                stop_reason = error.reason
        result['manifest_entry_sha256'] = sha_bytes(canonical(entry).encode('utf-8'))
        atomic_json(result_path, result); results.append(result)
    summary = {'schema_version': 'opensubtitles-hydration-summary/1', 'selected': len(entries),
               'outcomes': dict(collections.Counter(r['outcome'] for r in results)),
               'downloaded': sum(bool(r.get('downloaded')) for r in results),
               'reconstructed': sum(bool(r.get('reconstructed')) for r in results),
               'exact_match': sum(bool(r.get('exact_match')) for r in results),
               'network': net.state if net else {'requests': 0, 'bytes': 0},
               'scope': 'This bounded selection; no corpus-wide availability or reconstruction claim.'}
    atomic_json(work / 'summary.json', summary)
    return summary


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--recipe', type=Path, required=True)
    p.add_argument('--snapshot', type=Path)
    p.add_argument('--language', choices=tuple(LANGUAGES))
    p.add_argument('--file-key', action='append')
    p.add_argument('--max-files', type=int, default=4)
    p.add_argument('--offset', type=int, default=0)
    p.add_argument('--max-pages', type=int, default=10)
    p.add_argument('--source', choices=('api', 'local'), default='api')
    p.add_argument('--local-index', type=Path, help='Private JSON: file_key -> {path, input_adapter, optional sha256}.')
    p.add_argument('--work-dir', type=Path, default=Path('local/opensubtitles'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--refresh-metadata', action='store_true')
    p.add_argument('--retry-outcomes', action='store_true', help='Explicitly retry cached outcomes; request ledger and byte bounds still apply.')
    add_network_arguments(p)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.execute and args.dry_run:
            raise Outcome('error', 'execute_and_dry_run_are_exclusive')
        if args.execute and args.source == 'local' and not args.local_index:
            raise Outcome('error', 'local_index_required')
        result = run(args)
        print(canonical(result)); return 0
    except (Outcome, OSError, ValueError, KeyError, UnicodeError) as error:
        print(canonical(cli_error(error)), file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
