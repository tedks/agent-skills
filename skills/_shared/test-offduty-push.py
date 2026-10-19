#!/usr/bin/env python3
"""Offline publication tests against an isolated bare repository."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).with_name('offduty-push.sh')
INDEX_BUILDER = Path(__file__).resolve().parents[1] / 'retire-agent/scripts/build-index.py'
REAL_GIT = shutil.which('git')


class PushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='offduty-push-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.shared = self.root / 'shared'
        self.racer = self.root / 'racer'
        self.source = self.root / 'source'
        self.source.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null',
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid',
                        TMPDIR=str(self.root))
        self.env.pop('XDG_RUNTIME_DIR', None)
        self.git('init', '--bare', '-b', 'master', self.remote)
        seed = self.root / 'seed'
        self.git('clone', self.remote, seed)
        (seed / 'README').write_text('seed\n')
        self.git('-C', seed, 'add', 'README')
        self.git('-C', seed, 'commit', '-m', 'seed')
        self.git('-C', seed, 'push', 'origin', 'HEAD:master')
        self.git('clone', self.remote, self.shared)
        self.git('clone', self.remote, self.racer)

    def git(self, *args):
        return subprocess.run([REAL_GIT, *map(str, args)], env=self.env, text=True,
                              capture_output=True, check=True).stdout.strip()

    def publish(self, *paths, env=None, extra=()):
        return subprocess.run([str(HELPER), '--repo', str(self.shared), '--source-root',
                               str(self.source), *extra, *paths, '-m', 'Preserve fixture'],
                              env=env or self.env, text=True, capture_output=True)

    def test_pushes_bytes_without_modifying_shared_checkout(self):
        (self.source / 'waiting').mkdir()
        (self.source / 'waiting' / 'agent.md').write_bytes(b'private shard\n')
        (self.shared / 'README').write_text('uncommitted shared edit\n')
        (self.shared / 'untracked').write_text('leave me alone\n')
        (self.shared / 'stray').write_text('local commit\n')
        self.git('-C', self.shared, 'add', 'stray')
        self.git('-C', self.shared, 'commit', '-m', 'stray local commit')
        before = self.git('-C', self.shared, 'status', '--porcelain=v1')
        head = self.git('-C', self.shared, 'rev-parse', 'HEAD')
        result = self.publish('waiting/agent.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        pushed = result.stdout.strip()
        self.assertEqual(pushed, self.git('--git-dir', self.remote, 'rev-parse', 'master'))
        self.assertEqual(self.git('--git-dir', self.remote, 'show', 'master:waiting/agent.md'), 'private shard')
        self.assertEqual(self.git('-C', self.shared, 'rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('-C', self.shared, 'status', '--porcelain=v1'), before)
        self.assertFalse((self.shared / 'waiting' / 'agent.md').exists())
        self.assertEqual((self.shared / 'README').read_text(), 'uncommitted shared edit\n')
        self.assertEqual((self.shared / 'untracked').read_text(), 'leave me alone\n')

    def test_rejects_unapproved_and_escaping_paths(self):
        for path in ('other/a', '../waiting/a', 'waiting/../other/a', '/waiting/a'):
            with self.subTest(path=path):
                result = self.publish(path)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('--git-dir', self.remote, 'rev-list', '--count', 'master'), '1')

    def test_allow_path_is_explicit_and_source_symlink_is_rejected(self):
        (self.source / 'other').mkdir()
        (self.source / 'other' / 'note').write_text('approved exception\n')
        self.assertNotEqual(self.publish('other/note').returncode, 0)
        self.assertEqual(self.publish('other/note', extra=('--allow-path',)).returncode, 0)
        (self.source / 'waiting').mkdir()
        (self.source / 'waiting' / 'link').symlink_to(self.source / 'other' / 'note')
        self.assertNotEqual(self.publish('waiting/link').returncode, 0)

    def test_existing_remote_symlink_does_not_redirect_copy(self):
        outside = self.root / 'outside'
        outside.write_text('untouched\n')
        (self.shared / 'waiting').mkdir()
        (self.shared / 'waiting' / 'agent.md').symlink_to(outside)
        self.git('-C', self.shared, 'add', 'waiting/agent.md')
        self.git('-C', self.shared, 'commit', '-m', 'remote link')
        self.git('-C', self.shared, 'push', 'origin', 'HEAD:master')
        (self.source / 'waiting').mkdir()
        (self.source / 'waiting' / 'agent.md').write_text('new bytes\n')
        self.assertNotEqual(self.publish('waiting/agent.md').returncode, 0)
        self.assertEqual(outside.read_text(), 'untouched\n')

    def test_retries_non_fast_forward_on_latest_remote_commit(self):
        (self.source / 'receipts').mkdir()
        (self.source / 'waiting').mkdir()
        (self.source / 'waiting' / 'agent.md').write_text('{"event":"' + 'a' * 32 + '","session_id":"agent"}\n')
        (self.source / 'receipts' / ('a' * 32 + '.json')).write_text('{"event":"' + 'a' * 32 + '","shard":"waiting/agent.md","status":"retired"}\n')
        (self.racer / 'racer.txt').write_text('other writer\n')
        (self.racer / 'waiting').mkdir()
        (self.racer / 'receipts').mkdir()
        (self.racer / 'waiting' / 'other.md').write_text('{"event":"' + 'b' * 32 + '","session_id":"other"}\n')
        (self.racer / 'receipts' / ('b' * 32 + '.json')).write_text('{"event":"' + 'b' * 32 + '","shard":"waiting/other.md","status":"retired"}\n')
        self.git('-C', self.racer, 'add', 'racer.txt', 'waiting', 'receipts')
        self.git('-C', self.racer, 'commit', '-m', 'race')
        wrapper = self.root / 'bin'
        wrapper.mkdir()
        marker = self.root / 'raced'
        git_script = wrapper / 'git'
        git_script.write_text('#!/bin/sh\n'
                              'for arg in "$@"; do [ "$arg" = push ] && is_push=1; done\n'
                              'if [ "${is_push:-}" = 1 ] && [ ! -e "$RACE_MARKER" ]; then\n'
                              '  : > "$RACE_MARKER"\n'
                              '  "$REAL_GIT" -C "$RACE_CLONE" push origin HEAD:master >&2 || exit $?\n'
                              'fi\n'
                              'exec "$REAL_GIT" "$@"\n')
        git_script.chmod(0o755)
        env = dict(self.env, PATH=str(wrapper) + os.pathsep + self.env['PATH'],
                   REAL_GIT=REAL_GIT, RACE_MARKER=str(marker), RACE_CLONE=str(self.racer))
        result = self.publish('receipts/' + 'a' * 32 + '.json', 'waiting/agent.md', env=env,
                              extra=('--build-index', str(INDEX_BUILDER)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists())
        self.assertEqual(self.git('--git-dir', self.remote, 'show', 'master:racer.txt'), 'other writer')
        index = self.git('--git-dir', self.remote, 'show', 'master:waiting/INDEX.md')
        self.assertIn('[agent.md]', index)
        self.assertIn('[other.md]', index)
        self.assertEqual(self.git('--git-dir', self.remote, 'rev-list', '--count', 'master'), '3')


if __name__ == '__main__':
    unittest.main()
