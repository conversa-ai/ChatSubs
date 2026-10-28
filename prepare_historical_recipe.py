#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the exact pinned function snapshot from user-supplied ChatSubs code.

No network request or legacy entry point runs. Historical source licensing is
separate from this MIT helper. Only the inspected functions and constants are
emitted; the required output SHA-256 is fixed by the public data recipe.
"""
import argparse
import ast
import hashlib
from pathlib import Path
import sys

from hydration_common import Outcome, canonical, cli_error, sha_file

FUNCTIONS = {'is_time_line', 'ignore_line', 'get_time_limits', 'get_time_difference',
             'clean_textual_line_breaks', 'clean_dialog_segment', 'extract_dialog',
             'test_valid_final_clause', 'delete_final_clause'}
CONSTANTS = {'processed_languages', 'timeThreshold', 'HTML_TAGS', 'MULTIPLE_CLRF',
             'DIALOG_CONTINUATION_SUSPENSIVE_POINTS', 'DIALOG_CONTINUATION',
             'BEGIN_DIALOG1', 'BEGIN_DIALOG2', 'MULTI_SPACES', 'debug'}
EXPECTED_SOURCE = 'b34240986711050565cb46d34ce3da5686df50988a8e835c73cd9daa3d08cf90'
EXPECTED_AST = '3bcbab9dc11df43c2426b0136b8a740285980f2fe1591aa395cec6b11b0dccd6'
EXPECTED_SNAPSHOT = '9d8e7e545fefd39a8731a11a05dc41ba192bdc5875334aa0fd0b02e462903941'


def prepare(source, output):
    if sha_file(source) != EXPECTED_SOURCE:
        raise Outcome('error', 'upstream_source_fingerprint_differs')
    tree = ast.parse(Path(source).read_text(encoding='utf-8'))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            selected.append(node)
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id in CONSTANTS for t in node.targets):
            selected.append(node)
    digest = hashlib.sha256(ast.dump(ast.Module(body=selected, type_ignores=[]), include_attributes=False).encode()).hexdigest()
    if digest != EXPECTED_AST:
        raise Outcome('error', 'upstream_function_ast_differs')
    raw = ('"""Isolated retained functions/constants; generated without running legacy entry points."""\n'
           'import re\nfrom datetime import datetime, timedelta\n\n'
           + '\n\n'.join(ast.unparse(node) for node in selected) + '\n').encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != EXPECTED_SNAPSHOT:
        raise Outcome('error', 'snapshot_serialization_differs_use_cpython_3_11')
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if sha_file(output) != EXPECTED_SNAPSHOT:
            raise Outcome('error', 'existing_snapshot_preserved')
    else:
        output.write_bytes(raw)
    return {'outcome':'prepared', 'snapshot_sha256':EXPECTED_SNAPSHOT,
            'scope':'Historical code retains its separately authorised licence; helper is MIT.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        print(canonical(prepare(a.source,a.output)));return 0
    except (Outcome,OSError,ValueError,SyntaxError) as error:
        print(canonical(cli_error(error)),file=sys.stderr);return 2


if __name__=='__main__':
    raise SystemExit(main())
