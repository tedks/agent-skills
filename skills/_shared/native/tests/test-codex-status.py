#!/usr/bin/env python3
"""Offline fixtures are synthetic; never read actual session content."""
from datetime import datetime, timezone
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = Path(os.environ.get('CODEX_STATUS_MODULE', ROOT / 'codex_status.py'))
spec = importlib.util.spec_from_file_location('codex_status', MODULE)
status = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True
spec.loader.exec_module(status)
SID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
STAMP = '2026-01-01T00:00:00Z'
NOW = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

def parse(lines, expected_session_id=None, **kwargs):
    kwargs.setdefault('now', NOW)
    return status.parse_rollout(lines, expected_session_id, **kwargs)

def event(kind, payload, stamp=STAMP):
    return json.dumps({'type': kind, 'timestamp': stamp, 'payload': payload}) + '\n'

def meta(sid=SID, source='cli'):
    return event('session_meta', {'id': sid, 'source': source, 'cwd': '/synthetic', 'model_provider': 'openai'})

def tokens(bucket='codex', used=25):
    return event('event_msg', {'type': 'token_count', 'info': {
        'total_token_usage': {'total_tokens': 9000},
        'last_token_usage': {'input_tokens': 100, 'total_tokens': 120},
        'model_context_window': 200000}, 'rate_limits': {
        'limit_id': bucket, 'primary': {'used_percent': used, 'window_minutes': 300, 'resets_at': 2000000000},
        'secondary': None, 'credits': {'balance': 'PRIVATE'}}})

class ParserTests(unittest.TestCase):
    def test_projection(self):
        result = parse([meta(), event('turn_context', {'model': 'example-model', 'cwd': '/next', 'user_instructions': 'PRIVATE'}), tokens()], SID)
        self.assertEqual(result['state'], 'observed')
        self.assertEqual((result['session_id'], result['model'], result['cwd']), (SID, 'example-model', '/next'))
        self.assertEqual(result['model_observed_at'], STAMP)
        self.assertEqual(result['token_usage']['last']['total_tokens'], 120)
        self.assertEqual(result['token_usage']['total']['total_tokens'], 9000)
        self.assertEqual(result['token_usage']['context_window'], 200000)
        self.assertIsNone(result['context_remaining'])
        quota = result['quota_buckets']['codex']
        self.assertEqual((quota['scope'], quota['provider'], quota['observed_at']), ('account', 'openai', STAMP))
        self.assertEqual(quota['primary'], {'state': 'observed', 'historical_used_percent': 25, 'used_percent': 25, 'remaining_percent': 75, 'window_minutes': 300, 'resets_at': 2000000000})
        self.assertIsNone(quota['secondary'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_missing_and_bad_identity(self):
        for lines, expected in [([], SID), ([meta()], OTHER), ([meta('bad')], None), ([tokens()], None), ([meta()], '../bad')]:
            with self.subTest(lines=lines, expected=expected):
                result = parse(lines, expected)
                self.assertEqual(result['state'], 'unsupported')
                self.assertNotIn('session_id', result)
        self.assertEqual(parse([meta()], SID)['session_id'], SID)

    def test_inherited_metadata_keeps_cli_root(self):
        result = parse([meta(), meta(OTHER, 'exec'), tokens()], SID, expected_source='cli')
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['session_id'], SID)
        self.assertEqual(result['token_usage']['last']['total_tokens'], 120)
        self.assertEqual(parse([meta(SID, 'exec'), meta(OTHER)])['state'], 'unsupported')

    def test_incomplete_and_malformed(self):
        self.assertEqual(parse([meta(), '{unfinished'])['state'], 'observed')
        for tail in [['{broken\n'], ['{}\n'], ['[]\n'], ['{unfinished', tokens()]]:
            self.assertEqual(parse([meta()] + tail)['state'], 'unsupported')

    def test_compaction(self):
        compact = event('compacted', {'message': 'PRIVATE', 'replacement_history': ['PRIVATE']})
        result = parse([meta(), tokens(), compact])
        self.assertEqual(result['compaction'], {'event_count': 1, 'last_timestamp': STAMP})
        self.assertIsNone(result['token_usage'])
        self.assertIn('codex', result['quota_buckets'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(parse([meta(), compact, tokens()])['token_usage']['last']['total_tokens'], 120)

    def test_quota_updates_and_unknowns(self):
        result = parse([meta(), tokens(), tokens('other', 80), event('event_msg', {'type': 'token_count', 'info': None, 'rate_limits': None})])
        self.assertEqual(set(result['quota_buckets']), {'codex', 'other'})
        self.assertEqual(result['quota_buckets']['other']['primary']['remaining_percent'], 20)
        self.assertEqual(result['token_usage']['total']['total_tokens'], 9000)
        self.assertEqual(parse([meta()])['quota_buckets'], {})
        for invalid in [True, -1, 101, float('nan'), '25']:
            self.assertIsNone(parse([meta(), tokens(used=invalid)])['quota_buckets']['codex']['primary']['remaining_percent'])

    def test_adversarial_numbers_and_expected_id(self):
        self.assertEqual(parse([meta()], SID)['state'], 'observed')
        for expected in [1, True, [], {}, b'bytes']:
            try:
                result = parse([meta()], expected)
            except Exception as exc:
                self.fail('Malformed expected identity raised instead of failing closed: ' + type(exc).__name__)
            self.assertEqual(result['reason'], 'invalid_expected_session_id')
        try:
            result = parse([meta(), tokens(used=10**1000)])
        except Exception as exc:
            self.fail('JSON integer raised instead of becoming unavailable: ' + type(exc).__name__)
        window = result['quota_buckets']['codex']['primary']
        self.assertEqual(window['state'], 'unavailable')
        self.assertIsNone(window['remaining_percent'])

    def test_expired_quota_is_historical(self):
        reset_time = datetime.fromtimestamp(2000000000, timezone.utc)
        for now in [reset_time, datetime(2034, 1, 1, tzinfo=timezone.utc)]:
            result = parse([meta(), tokens()], now=now)
            window = result['quota_buckets']['codex']['primary']
            self.assertEqual(window['state'], 'expired')
            self.assertEqual(window['historical_used_percent'], 25)
            self.assertIsNone(window['remaining_percent'])
            self.assertIsNone(window['used_percent'])
            self.assertEqual(result['quota_buckets']['codex']['evaluated_at'], now.isoformat())
        self.assertEqual(parse([meta(), tokens()])['quota_buckets']['codex']['primary']['remaining_percent'], 75)

    def test_unknown_quota_metadata(self):
        for key, value in [('resets_at', None), ('resets_at', True), ('resets_at', 10**1000),
                           ('window_minutes', None), ('window_minutes', 0), ('window_minutes', True)]:
            record = json.loads(tokens())
            record['payload']['rate_limits']['primary'][key] = value
            window = parse([meta(), json.dumps(record)+'\n'])['quota_buckets']['codex']['primary']
            self.assertEqual(window['state'], 'unavailable')
            self.assertIsNone(window['remaining_percent'])
        for stamp in [None, 'invalid', '2026-01-01', '2027-01-01T00:00:00Z']:
            record = json.loads(tokens())
            record['timestamp'] = stamp
            window = parse([meta(), json.dumps(record)+'\n'])['quota_buckets']['codex']['primary']
            self.assertEqual(window['state'], 'unavailable')
            self.assertIsNone(window['remaining_percent'])
        self.assertEqual(parse([meta(), tokens()])['quota_buckets']['codex']['primary']['state'], 'observed')

    def test_invalid_typed_values(self):
        result = parse([meta(), event('turn_context', {'model': {}, 'cwd': []}, 'no-time'), event('event_msg', {'type': 'token_count', 'info': {'model_context_window': True, 'last_token_usage': {'total_tokens': -1}}})])
        self.assertIsNone(result['model'])
        self.assertIsNone(result['model_observed_at'])
        self.assertIsNone(result['token_usage']['context_window'])
        self.assertIsNone(result['token_usage']['last'])

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sessions = self.root / 'home/.codex/sessions'
        self.fds = self.root / 'proc/123/fd'
        self.sessions.mkdir(parents=True)
        self.fds.mkdir(parents=True)
        (self.fds.parent / 'fdinfo').mkdir()
        (self.fds.parent / 'environ').write_bytes(b'')

    def add(self, number, content):
        path = self.sessions / f'rollout-{number}.jsonl'
        path.write_text(content)
        (self.fds / str(number)).symlink_to(path)
        (self.fds.parent / 'fdinfo' / str(number)).write_text('flags:\t0102001\n')
        return path

    def read(self, **kwargs):
        return status.read_codex_status(123, self.root / 'home', proc=self.root / 'proc', **kwargs)

    def test_unique_cli_ignores_subagent(self):
        path = self.add(1, meta() + tokens())
        self.add(2, meta(OTHER, {'subagent': {}}))
        result = self.read()
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['session_id'], SID)
        self.assertEqual(result['rollout_path'], str(path))
        (self.fds / '3').symlink_to(path)
        (self.fds.parent / 'fdinfo/3').write_text('flags:\t0102001\n')
        self.assertEqual(self.read()['session_id'], SID)  # duplicate descriptor, same inode

    def test_recycled_descriptor_rejected(self):
        self.add(1, meta() + tokens())
        replacement = self.sessions / 'replacement.jsonl'
        replacement.write_text(meta() + tokens())  # identical ID, source and size
        self.assertEqual(self.read()['state'], 'observed')
        original_open = Path.open
        opens = 0
        def swap(path, *args, **kwargs):
            nonlocal opens
            if path == self.fds / '1':
                opens += 1
                if opens == 2:
                    path.unlink()
                    path.symlink_to(replacement)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', swap):
            self.assertEqual(self.read().get('reason'), 'rollout_descriptor_changed')

    def test_source_rechecked_in_opened_stream(self):
        rollout = self.add(1, meta() + tokens())
        self.assertEqual(self.read()['state'], 'observed')
        original_open = Path.open
        opens = 0
        def rewrite(path, *args, **kwargs):
            nonlocal opens
            if path == self.fds / '1':
                opens += 1
                if opens == 2:
                    rollout.write_text(meta(source='api') + tokens())  # same inode and size
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', rewrite):
            self.assertEqual(self.read().get('reason'), 'session_source_mismatch')

    def test_ambiguity_and_missing(self):
        self.assertEqual(self.read()['state'], 'unsupported')
        self.add(1, meta())
        self.assertEqual(self.read()['state'], 'observed')
        self.add(2, meta(OTHER))
        self.assertEqual(self.read()['state'], 'unsupported')

    def test_bounds(self):
        self.add(1, meta() + tokens())
        self.assertEqual(self.read()['state'], 'observed')
        bounded = self.read(max_bytes=10)
        self.assertEqual(bounded['state'], 'observed')
        self.assertIsNone(bounded['token_usage'])
        self.assertFalse(bounded['history_complete'])
        self.assertEqual(bounded['compaction']['count_scope'], 'scanned_window')
        self.assertEqual(self.read(max_line_bytes=10)['state'], 'unsupported')

    def test_partial_utf8_is_not_a_record(self):
        path = self.add(1, meta() + tokens())
        with path.open('ab') as stream:
            stream.write(b'{"incomplete":"\xe2\x82')
        result = self.read()
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['token_usage']['last']['total_tokens'], 120)
        with path.open('ab') as stream:
            stream.write(b'\n')
        self.assertEqual(self.read()['state'], 'unsupported')

    def test_pinned_codex_home(self):
        self.sessions = self.root / 'custom-codex/sessions'
        self.sessions.mkdir(parents=True)
        path = self.add(1, meta() + tokens())
        (self.fds.parent / 'environ').write_bytes(
            b'CODEX_HOME=' + os.fsencode(self.sessions.parent) + b'\0')
        with patch.dict(os.environ, CODEX_HOME='/unrelated-caller-home'):
            result = self.read()
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['rollout_path'], str(path))
        (self.fds.parent / 'environ').write_bytes(b'CODEX_HOME=relative\0')
        self.assertEqual(self.read()['reason'], 'relative_codex_home_unsupported')

    def test_default_home_is_resolved_at_call_time(self):
        self.add(1, meta())
        with patch.dict(os.environ, HOME=str(self.root / 'home')):
            result = status.read_codex_status(123, proc=self.root / 'proc')
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['session_id'], SID)

    def test_pinned_home_fallback(self):
        self.sessions = self.root / 'harness-home/.codex/sessions'
        self.sessions.mkdir(parents=True)
        self.add(1, meta())
        (self.fds.parent / 'environ').write_bytes(
            b'HOME=' + os.fsencode(self.root / 'harness-home') + b'\0')
        with patch.object(Path, 'home', side_effect=RuntimeError('caller home unavailable')):
            result = self.read()
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['session_id'], SID)

    def test_unavailable_home_is_typed(self):
        self.add(1, meta())
        with patch.object(Path, 'home', side_effect=RuntimeError('caller home unavailable')):
            try:
                result = status.read_codex_status(123, proc=self.root / 'proc')
            except RuntimeError:
                self.fail('Path.home failure escaped instead of returning unsupported')
        self.assertEqual(result['state'], 'unsupported')
        self.assertEqual(self.read()['state'], 'observed')

    def test_closed_descriptor_does_not_poison_live_root(self):
        self.add(1, meta())
        self.add(2, meta(OTHER, {'subagent': {}}))
        self.assertEqual(self.read()['state'], 'observed')
        for method, target in [('read_text', self.fds.parent / 'fdinfo/2'),
                               ('stat', self.fds / '2'), ('open', self.fds / '2')]:
            original = getattr(Path, method)
            def closed(path, *args, **kwargs):
                if path == target:
                    raise FileNotFoundError('descriptor closed')
                return original(path, *args, **kwargs)
            with self.subTest(method=method), patch.object(Path, method, closed):
                result = self.read()
            self.assertEqual(result['state'], 'observed')
            self.assertEqual(result['session_id'], SID)
        original = Path.read_text
        def denied(path, *args, **kwargs):
            if path == self.fds.parent / 'fdinfo/2':
                raise PermissionError('unknown access failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', denied):
            self.assertEqual(self.read()['state'], 'unsupported')

    def test_readonly_picker_is_not_owner(self):
        self.add(1, meta())
        self.add(2, meta(OTHER))
        (self.fds.parent / 'fdinfo/2').write_text('flags:\t0100000\n')
        result = self.read()
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['session_id'], SID)
        (self.fds.parent / 'fdinfo/1').write_text('flags:\t0100000\n')
        self.assertEqual(self.read()['state'], 'unsupported')

    def test_empty_writer_is_temporarily_unclassified(self):
        self.add(1, meta())
        child = self.add(2, '')
        self.assertEqual(self.read().get('reason'), 'unclassified_empty_writable_rollout')
        child.write_text(meta(OTHER, {'subagent': {}}))
        self.assertEqual(self.read()['state'], 'observed')

    def test_large_history_uses_bounded_tail(self):
        latest = (event('turn_context', {'model': 'latest-model', 'cwd': '/last'})
                  + tokens()
                  + event('event_msg', {'type': 'token_count', 'info': None,
                      'rate_limits': {'limit_id': 'other', 'primary': {
                          'used_percent': 50, 'window_minutes': 60, 'resets_at': 2000000000}}}))
        large = event('response_item', {'type': 'message', 'content': 'x' * 5000})
        self.add(1, meta() + event('compacted', {}) + large + latest)
        result = self.read(max_bytes=len(latest.encode()) + 100, max_line_bytes=700)
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(result['model'], 'latest-model')
        self.assertEqual(result['token_usage']['last']['total_tokens'], 120)
        self.assertEqual(set(result['quota_buckets']), {'codex', 'other'})
        self.assertFalse(result['history_complete'])
        self.assertEqual(result['compaction']['event_count'], 0)
        self.assertEqual(result['compaction']['count_scope'], 'scanned_window')
        self.assertLessEqual(result['read_window']['tail_bytes'], len(latest.encode()) + 100)
        # Even within budget, a huge unrelated record must not poison later fields.
        full = self.read(max_line_bytes=700)
        self.assertEqual(full['state'], 'observed')
        self.assertEqual(full['model'], 'latest-model')
        self.assertFalse(full['history_complete'])
        self.assertEqual(full['compaction']['event_count'], 1)

    def test_skipped_record_invalidates_token_accounting(self):
        large = event('compacted', {'replacement_history': 'x' * 5000})
        path = self.add(1, meta() + tokens() + large)
        result = self.read(max_line_bytes=700)
        self.assertEqual(result['state'], 'observed')
        self.assertIsNone(result['token_usage'])
        self.assertEqual(result['compaction']['event_count'], 0)
        self.assertIsNone(result['compaction']['last_timestamp'])
        self.assertFalse(result['history_complete'])
        with path.open('a') as stream:
            stream.write(tokens())
        restored = self.read(max_line_bytes=700)
        self.assertEqual(restored['state'], 'observed')
        self.assertEqual(restored['token_usage']['last']['total_tokens'], 120)
        self.assertEqual(restored['compaction']['event_count'], 0)

    def test_invalid_metadata(self):
        path = self.add(1, meta())
        self.assertEqual(self.read()['state'], 'observed')
        for content in ['{broken\n', meta('bad'), tokens(), '[]\n', meta().rstrip()]:
            path.write_text(content)
            self.assertEqual(self.read()['state'], 'unsupported')

    def test_nonregular_rollout(self):
        path = self.sessions / 'rollout-directory.jsonl'
        path.mkdir()
        (self.fds / '1').symlink_to(path)
        (self.fds.parent / 'fdinfo/1').write_text('flags:\t0102001\n')
        self.assertEqual(self.read()['reason'], 'invalid_rollout_file')

    def test_outside_home_and_pid(self):
        path = self.root / 'rollout-outside.jsonl'
        path.write_text(meta())
        (self.fds / '1').symlink_to(path)
        (self.fds.parent / 'fdinfo/1').write_text('flags:\t0102001\n')
        self.assertEqual(self.read()['state'], 'unsupported')
        self.add(2, meta())
        self.assertEqual(self.read()['state'], 'observed')
        self.assertEqual(status.read_codex_status(True)['state'], 'unsupported')

def mutation_check():
    """Baseline first; every mutant must be valid Python and fail assertions.

    Only temporary module copies are changed. An ERROR, crash, timeout, import
    failure or surviving mutant fails the checker instead of counting as a kill.
    """
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
        return 1
    mutations = [
        ('remaining', '100 - used if state == "observed" else None',
         'used if state == "observed" else None', 'ParserTests.test_projection'),
        ('huge-integer', 'or not 0 <= used <= 100 or not math.isfinite(used)',
         'or not math.isfinite(used) or not 0 <= used <= 100', 'ParserTests.test_adversarial_numbers_and_expected_id'),
        ('id-type', 'not isinstance(expected_session_id, str) or not _ID.fullmatch(expected_session_id)',
         'not _ID.fullmatch(expected_session_id)', 'ParserTests.test_adversarial_numbers_and_expected_id'),
        ('identity', 'sid.lower() != expected_session_id.lower()', 'False', 'ParserTests.test_missing_and_bad_identity'),
        ('fork-metadata', 'if initial_source != "cli":', 'if True:', 'ParserTests.test_inherited_metadata_keeps_cli_root'),
        ('partial-json', 'return failure("incomplete_nonfinal_record")', 'break', 'ParserTests.test_incomplete_and_malformed'),
        ('compaction', 'result["compaction"]["event_count"] += 1',
         'result["compaction"]["event_count"] += 0', 'ParserTests.test_compaction'),
        ('typed-numbers', 'type(value) is int and value >= 0',
         'isinstance(value, int) and value >= 0', 'ParserTests.test_invalid_typed_values'),
        ('expiry', 'state = "expired" if reset <= now else "observed"',
         'state = "observed"', 'ParserTests.test_expired_quota_is_historical'),
        ('expiry-boundary', 'reset <= now', 'reset < now', 'ParserTests.test_expired_quota_is_historical'),
        ('unknown-window', 'minutes is not None', 'True', 'ParserTests.test_unknown_quota_metadata'),
        ('future-observation', 'observed <= now', 'True', 'ParserTests.test_unknown_quota_metadata'),
        ('unknown-observation', 'if observed_at is not None else None',
         'if observed_at is not None else now', 'ParserTests.test_unknown_quota_metadata'),
        ('fd-recycling', 'if (opened.st_dev, opened.st_ino) != selected_identity:',
         'if False:', 'ReaderTests.test_recycled_descriptor_rejected'),
        ('source-recheck', 'expected_source="cli"', 'expected_source=None', 'ReaderTests.test_source_rechecked_in_opened_stream'),
        ('root-only', 'if payload.get("source") != "cli":', 'if False:', 'ReaderTests.test_unique_cli_ignores_subagent'),
        ('ambiguity', 'if len(candidates) != 1:', 'if not candidates:', 'ReaderTests.test_ambiguity_and_missing'),
        ('partial-utf8', 'break  # partial trailing UTF-8 is not yet a JSONL record',
         'pass  # mutation: decode uncommitted bytes', 'ReaderTests.test_partial_utf8_is_not_a_record'),
        ('pinned-home', 'configured = environment.get(b"CODEX_HOME")', 'configured = None', 'ReaderTests.test_pinned_codex_home'),
        ('default-home', 'home = Path.home() if home is None else home',
         'home = Path("/nonexistent") if home is None else home', 'ReaderTests.test_default_home_is_resolved_at_call_time'),
        ('pinned-fallback-home', 'pinned_home = environment.get(b"HOME")',
         'pinned_home = None', 'ReaderTests.test_pinned_home_fallback'),
        ('unavailable-home', 'except (OSError, ValueError, UnicodeError, KeyError, RuntimeError):',
         'except (OSError, ValueError, UnicodeError, KeyError):', 'ReaderTests.test_unavailable_home_is_typed'),
        ('closed-fd', 'continue  # this descriptor demonstrably closed during discovery',
         'return failure("descriptor_closed")', 'ReaderTests.test_closed_descriptor_does_not_poison_live_root'),
        ('picker', 'if flags & os.O_ACCMODE == os.O_RDONLY:', 'if False:', 'ReaderTests.test_readonly_picker_is_not_owner'),
        ('empty-writer', 'return failure("unclassified_empty_writable_rollout")',
         'continue', 'ReaderTests.test_empty_writer_is_temporarily_unclassified'),
        ('tail-budget', 'start = max(len(header), initial_size - max_bytes)',
         'start = len(header)', 'ReaderTests.test_large_history_uses_bounded_tail'),
        ('oversized-row', 'if skipping or len(raw) > max_line_bytes:',
         'if skipping:', 'ReaderTests.test_large_history_uses_bounded_tail'),
        ('skipped-compaction', 'yield None  # the skipped record may have been compaction',
         'pass  # mutation: retain stale precompaction tokens', 'ReaderTests.test_skipped_record_invalidates_token_accounting'),
        ('history-scope', 'result["history_complete"] = complete_history and not skipped_records',
         'result["history_complete"] = True', 'ReaderTests.test_large_history_uses_bounded_tail'),
        ('home-boundary', 'if not path.resolve().is_relative_to(sessions):',
         'if False:', 'ReaderTests.test_outside_home_and_pid'),
    ]
    source = MODULE.read_text()
    failed = []
    with tempfile.TemporaryDirectory(prefix='codex-status-mutations-') as temp:
        for name, old, new, test in mutations:
            if source.count(old) != 1:
                raise AssertionError('Mutation anchor drift: ' + name)
            changed = source.replace(old, new, 1)
            ast.parse(changed)  # syntax errors never count as a kill
            path = Path(temp) / (name + '.py')
            path.write_text(changed)
            run = subprocess.run([sys.executable, str(Path(__file__).resolve()), test],
                                 env=dict(os.environ, CODEX_STATUS_MODULE=str(path)),
                                 capture_output=True, text=True, timeout=30)
            output = run.stdout + run.stderr
            killed = run.returncode == 1 and re.search(r'FAILED \(failures=[1-9][0-9]*\)', output) and 'ERROR:' not in output
            print(name + ': ' + ('assertion failed as expected' if killed else 'INVALID OR SURVIVED'))
            if not killed:
                failed.append(name)
                print(output)
    print(f'{len(mutations) - len(failed)}/{len(mutations)} valid mutants caught by assertions')
    return int(bool(failed))


if __name__ == '__main__':
    if sys.argv[1:] == ['--mutation-check']:
        raise SystemExit(mutation_check())
    unittest.main()
