#!/usr/bin/env python3
"""Demonstrate that safety tests detect removed guards, without editing source."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
source = (HERE / 'retire.py').read_text()
mutations = [
    ('shard object', 'if not isinstance(data, dict):', 'if False:', 'test_nonobject_shard_is_controlled_failure'),
    ('index symlink', 'if index_path.is_symlink() or (index_path.exists() and not index_path.is_file()):', 'if False:', 'test_index_symlink_preserves_outside_file'),
    ('private origin', "if run([checker, url]).returncode:", 'if False:', 'test_private_and_missing_remotes_before_send'),
    ('process identity', "if self.identity() != self.state['identity']:", 'if False:', 'test_changed_identity_and_malformed_state'),
    ('exit gate', 'while not self.ended():', 'while False:', 'test_no_successor_before_exit'),
    ('safe shard', "if path.is_symlink() or not path.is_file() or path.resolve() != self.path.parent / (self.state['id'] + '.shard'):", 'if False:', 'test_symlink_shard_blocks_exit'),
    ('DONE provenance', r"r'^[●•] RETIRE-'", r"r'^(?:[●•] )?RETIRE-'", 'test_wrapped_user_done_does_not_complete'),
]
for label, before, after, test in mutations:
    assert before in source, label
    with tempfile.TemporaryDirectory(prefix='retire-mutation-') as directory:
        directory = Path(directory)
        (directory / 'retire.py').write_text(source.replace(before, after, 1))
        entry = directory / 'retire.sh'
        entry.write_bytes((HERE / 'retire.sh').read_bytes())
        entry.chmod(0o700)
        env = dict(os.environ, RETIRE_TEST_SCRIPT=str(entry))
        result = subprocess.run([sys.executable, str(HERE / 'test-retire.py'), 'Tests.' + test], env=env,
                                text=True, capture_output=True, timeout=30)
        if result.returncode == 0:
            raise SystemExit('SURVIVED: ' + label)
        if 'FAILED' not in result.stderr:
            raise SystemExit('Invalid mutation test failure: ' + label + '\n' + result.stderr)
        print('KILLED: ' + label)
