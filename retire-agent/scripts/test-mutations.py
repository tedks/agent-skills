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
    ('remote host', "'https://github.com/' + match[1]", 'match[1]', 'test_gh_privacy_pins_remote_host'),
    ('session transaction', "with locked(self.common / 'retirement.lock'):  # Unique session publication.", "with locked(self.common / (self.state['id'] + '.transaction.lock')):", 'test_duplicate_session_transaction_race'),
    ('independent exit', "self.state['exit'] = {'status': 'not-needed', 'reason': 'original-already-ended'}", "raise Stop('already ended')", 'test_independent_exit_after_handoff_allows_successor'),
    ('literal Claude exit', "control = ['--literal-exit'] if phase == 'exit' and self.state['identity']['process']['agent'] == 'claude' else []", 'control = []', 'test_claude_literal_exit_real_terminal'),
    ('loaded object', 'if not isinstance(record, dict):', 'if False:', 'test_malformed_state_and_history_preserved'),
    ('receipt before exit', ["self.publish_locked(['receipts/' + receipt.name], index=True)", "record = receipt_record(self.git('show', 'origin/master:' + self.state['handoff_receipt']))"], ['pass  # Withhold verified receipt publication until after exit.', "record = receipt_record((self.repo / self.state['handoff_receipt']).read_text())"], 'test_retire_exit_to_shell_and_path_only_commit'),
    ('shard object', 'if not isinstance(data, dict):', 'if False:', 'test_nonobject_shard_is_controlled_failure'),
    ('index symlink', 'if index_path.is_symlink() or (index_path.exists() and not index_path.is_file()):', 'if False:', 'test_index_symlink_preserves_outside_file'),
    ('private origin', "if run([checker, url]).returncode:", 'if False:', 'test_private_and_missing_remotes_before_send'),
    ('process identity', "if self.identity() != self.state['identity']:", 'if False:', 'test_changed_identity_and_malformed_state'),
    ('exit gate', 'while not self.ended():', 'while False:', 'test_no_successor_before_exit'),
    ('safe shard', ["if path.is_symlink() or not path.is_file() or path.resolve() != self.path.parent / (self.state['id'] + '.shard'):", 'os.O_RDONLY | os.O_NOFOLLOW'], ['if False:', 'os.O_RDONLY'], 'test_symlink_shard_blocks_exit'),
    ('DONE provenance', [r"r'^[●•] '", r"r'[●•] RETIRE-(DONE|DECLINED)-'"], [r"r'^(?:[●•] )?RETIRE-'", r"r'(?:[●•] )?RETIRE-(DONE|DECLINED)-'"], 'test_wrapped_user_done_does_not_complete'),
    ('validated snapshot', 'atomic_bytes(canonical, content)', 'atomic_bytes(canonical, shard.read_bytes())', 'test_validated_snapshot_ignores_later_symlink_swap'),
    ('lingering core', "for core in original.get('cores', []):", 'for core in []:', 'test_wrapper_exit_waits_for_lingering_core'),
    ('pinned socket', "self.tmux = ['tmux', '-S', state['socket']]", 'pass  # Caller environment selects the wrong server.', 'test_resume_uses_pinned_socket'),
    ('server failure', 'except PaneAbsent:', 'except Stop:', 'test_socket_failure_is_not_pane_disappearance'),
    ('steward index ownership', '(tracked and outside_section(original) != outside_section(baseline) and not owned_write)', 'False', 'test_steward_dirty_index_is_not_published'),
    ('commit ownership', '        self.require_owned_commits()', '        pass  # Push unowned commits.', 'test_unowned_local_commit_is_not_pushed'),
    ('composer exclusion', "rows = self.screen.read_response_rows(self.args.pane, self.state['socket'])", "rows = checked(self.tmux + ['capture-pane', '-p', '-t', self.args.pane]).splitlines()", 'test_claude_composer_done_does_not_complete'),
    ('last polling interval', 'disposition = self.observe()  # Include the final polling interval.', 'pass', 'test_last_poll_interval_is_observed'),
    ('recorded DONE', "if self.state.get('disposition') != 'DONE':", 'if True:', 'test_saved_done_survives_cleared_screen'),
    ('initial remote receipt', "self.publish(['receipts/' + receipt.name])  # Local presence is not a remote receipt.", 'pass', 'test_failed_initial_push_is_verified_before_resumed_offer'),
    ('immutable successor brief', "preserved = self.git('show', self.state['shard_hash'], raw=True)", "preserved = (self.repo / self.state['shard']).read_text()", 'test_handoff_brief_reads_verified_blob'),
    ('nonblocking shard snapshot', 'os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK', 'os.O_RDONLY | os.O_NOFOLLOW', 'test_pending_fifo_swap_never_blocks'),
    ('receipt handoff status', "if record.get('status') not in ('handoff-verified', 'pending-exit', 'retired'):", 'if False:', 'test_remote_requested_receipt_cannot_authorize_successor'),
    ('owned atomic temporary', "descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)", "descriptor, name = os.open(path.with_suffix('.tmp'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), str(path.with_suffix('.tmp'))", 'test_foreign_adjacent_temp_preserved'),
    ('locked resume reread', 'self.load_resume()  # Another controller may have advanced state before this lock.', 'pass  # Trust stale pre-lock state.', 'test_resume_rereads_state_after_event_lock'),
    ('completion suffix', "if any(row.strip() and not (len(row.strip()) >= 3 and set(row.strip()) == {'─'}) for row in rows[position + 1:]):", 'if False:', 'test_codex_marker_suffix_is_not_completion'),
    ('pending evidence authority', "if self.state.get('handoff_receipt') and not getattr(self, 'evidence_verified', False):", 'if False:', 'test_remote_requested_receipt_cannot_authorize_successor'),
    ('index preflight ordering', ['self.publication_preflight()  # Preconditions before any INDEX write.', "        if self.git('diff', 'HEAD', '--', *paths)"], ['pass  # Preconditions delayed until after index write.', "        self.publication_preflight()\n        if self.git('diff', 'HEAD', '--', *paths)"], 'test_index_preflight_failure_preserves_legacy_then_resumes'),
    ('owned tracked index retry', "self.state['index_outside'] = hashlib.sha256(outside_section(updated).encode()).hexdigest()", 'pass  # Lose ownership of our tracked legacy index write.', 'test_own_legacy_index_write_recovers_after_commit_hook_failure'),
    ('tracked index deletion', 'if tracked and not index_path.exists():', 'if False:', 'test_tracked_deleted_index_is_not_recreated'),
    ('padded completion marker', 'rows[position].rstrip()', 'rows[position]', 'test_padded_final_marker_completes'),
    ('receipt missing path', ['if evidence.returncode:', "if evidence.stdout.strip() != self.state['shard_hash']:"], ['if False:', 'if False:'], 'test_remote_bad_commit_receipt_never_self_heals'),
    ('receipt wrong blob', "if evidence.stdout.strip() != self.state['shard_hash']:", 'if False:', 'test_wrong_blob_receipt_never_self_heals'),
    ('corrupt index receipt', "                claim = object_record(receipt_text, 'retirement receipt')", '                try:\n                    claim = json.loads(receipt_text)\n                except ValueError:\n                    continue\n                if not isinstance(claim, dict):\n                    continue', 'test_corrupt_other_receipt_preserves_generated_index'),
]
for label, before, after, test in mutations:
    replacements = list(zip(before, after)) if isinstance(before, list) else [(before, after)]
    mutated = source
    for before, after in replacements:
        assert before in mutated, label
        mutated = mutated.replace(before, after, 2 if label == 'commit ownership' else 1)
    with tempfile.TemporaryDirectory(prefix='retire-mutation-') as directory:
        directory = Path(directory)
        (directory / 'retire.py').write_text(mutated)
        entry = directory / 'retire.sh'
        entry.write_bytes((HERE / 'retire.sh').read_bytes())
        entry.chmod(0o700)
        env = dict(os.environ, RETIRE_TEST_SCRIPT=str(entry))
        result = subprocess.run([sys.executable, str(HERE / 'test-retire.py'), 'Tests.' + test], env=env,
                                text=True, capture_output=True, timeout=30)
        if result.returncode == 0:
            raise SystemExit('SURVIVED: ' + label)
        if 'INVALID-INSTRUMENTATION:' in result.stderr:
            raise SystemExit('Invalid mutation instrumentation: ' + label + '\n' + result.stderr)
        if 'FAILED' not in result.stderr:
            raise SystemExit('Invalid mutation test failure: ' + label + '\n' + result.stderr)
        print('KILLED: ' + label)
