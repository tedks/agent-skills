#!/usr/bin/env python3
"""Behavioral tests; isolated tmux server, local bare private Git, no models/network."""
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys
import tempfile
import termios
import time
import tty
import unittest
import uuid

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[1] / 'spawn-agent' / 'scripts'


def shard_data(event, identity):
    return {'event': event, 'identity': identity, 'author': 'agent',
            'date': '2026-09-26', 'session_id': 'test-session-id', 'harness': identity['process']['agent'],
            'model': 'test-model', 'working_directory': identity['directory'], 'tmux_window': 'private-test:0',
            'role': 'Utility', 'landed_work': 'none', 'lessons': 'Record receipts before exit.', 'provenance': 'fixture-agent',
            'recovery': {'mission': 'Mission authorized', 'authorization': 'fixture caller', 'pauses': 'none',
                         'worktree': identity['directory'], 'branch': 'master', 'heads': 'fixture seed',
                         'tested_vs_merged': 'none', 'evidence': 'local records', 'limits': 'fixture only',
                         'failures': 'none', 'open_decisions': [], 'outstanding_sends': 'none', 'next_action': 'read artifacts'},
            'resources': []}


def terminal(directory, mode):
    directory = Path(directory)
    old = termios.tcgetattr(0)
    tty.setraw(0)
    draft = b'human unfinished draft' if mode == 'draft' else b''
    pending = b''
    pasting = False
    pastes = enters = 0
    history = ''
    event = None
    children = []
    if mode in ('node-core', 'extra-core'):
        core = str(directory / '@openai/codex/node_modules/@openai/codex-linux-x64/vendor/test/bin/codex')
        for name in (core, core if mode == 'extra-core' else 'node'):
            children.append(subprocess.Popen(['bash', '-c', 'exec -a ' + shlex.quote(name) + ' sleep 360']))
    def draw():
        collapsed = f'[Pasted Content {len(draft.decode(errors="replace"))} chars]' if draft and mode != 'draft' else draft.decode(errors='replace')
        body = history + '\r\n› ' + collapsed + '\r\n\r\n  Test model · branch\r\n'
        if mode == 'modal':
            body = 'Do you trust the contents of this directory?\r\n  1. Yes\r\n  2. No\r\n'
        sys.stdout.write('\x1b[?2004h\x1b[2J\x1b[H' + body)
        row = history.count('\n') + 2
        sys.stdout.write(f'\x1b[{row};{3 + len(collapsed)}H')
        sys.stdout.flush()
        (directory / 'terminal.json').write_text(json.dumps({'pastes': pastes, 'enters': enters, 'event': event}))
    try:
        draw()
        while True:
            if mode == 'draft' and (directory / 'human-cleared-draft').exists():
                draft = b''
                mode = 'done'
                draw()
            if not select.select([0], [], [], .1)[0]:
                continue
            pending += os.read(0, 65536)
            while pending:
                if pending.startswith(b'\x1b[200~'):
                    pastes += 1
                    pasting = True
                    pending = pending[6:]
                elif pending.startswith(b'\x1b[201~'):
                    pasting = False
                    pending = pending[6:]
                elif pending.startswith(b'\x1b') and len(pending) < 6:
                    break
                else:
                    char, pending = pending[:1], pending[1:]
                    if not pasting and char == b'\r':
                        enters += 1
                        payload = draft.decode()
                        if payload == '/exit':
                            # Independently prove order from the remote, before exit.
                            remote = directory / 'remote.git'
                            receipt = subprocess.run(['git', '--git-dir', str(remote), 'show', 'master:receipts/' + event + '.json'], capture_output=True, text=True)
                            (directory / 'exit-order.json').write_text(json.dumps({'receipt_before_exit': receipt.returncode == 0}))
                            if mode != 'hold':
                                return
                        else:
                            match = re.search(r'Event ([0-9a-f]{32})\.', payload)
                            event = match[1]
                            identity = json.JSONDecoder().raw_decode(payload.split(', identity ', 1)[1])[0]
                            shard = directory / 'repo' / '.git' / 'retirements' / (event + '.shard')
                            data = shard_data(event, identity)
                            if mode != 'waiting-bad':
                                data['waiting'] = {'decision': 'Choose scope', 'owner': 'Ted'}
                            if mode == 'resource':
                                data['resources'] = [{'name': 'lease', 'status': 'pending'}]
                            if mode == 'path':
                                target = directory / 'outside.json'
                                target.write_text(json.dumps(data))
                                shard.symlink_to(target)
                            elif mode != 'decline':
                                shard.write_text(json.dumps(data))
                        history = '› [Pasted Content ' + str(len(payload)) + ' chars]\r\n'
                        if mode == 'ambiguous':
                            history = 'Assistant continues without acceptance evidence.\r\n'
                        else:
                            history += '• Accepted\r\n'
                            if payload != '/exit' and mode != 'composer':
                                disposition = 'DECLINED' if mode == 'decline' else 'DONE'
                                if mode == 'quoted':
                                    history += 'RETIRE-DONE-' + event + '\r\n'
                                elif mode == 'old':
                                    history += '● RETIRE-DONE-' + ('0' * 32) + '\r\n'
                                else:
                                    history += '● RETIRE-' + disposition + '-' + event + '\r\n'
                        draft = ('RETIRE-DONE-' + event).encode() if mode == 'composer' else b''
                    else:
                        draft += char
                draw()
    finally:
        for child in children:
            child.terminate()
            child.wait()
        termios.tcsetattr(0, termios.TCSADRAIN, old)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.label = 'retire-test-' + uuid.uuid4().hex
        self.tmux = ['tmux', '-L', self.label]
        self.repo = self.directory / 'repo'
        self.command('git', 'init', '--bare', str(self.directory / 'remote.git'))
        self.command('git', 'init', '-b', 'master', str(self.repo))
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        (self.repo / 'seed').write_text('private test\n')
        self.git('add', 'seed')
        self.git('commit', '-m', 'seed')
        self.git('remote', 'add', 'origin', str(self.directory / 'remote.git'))
        self.git('push', '-u', 'origin', 'master')
        shared = self.directory / 'shared' / 'scripts'
        shared.mkdir(parents=True)
        # Timing-only wrapper; real shared sender, real terminal, no transport stubs.
        sender = shared / 'tmux-message.sh'
        sender.write_text('#!/usr/bin/env bash\nphase=$1; pane=$2; shift 2\nexec ' + shlex.quote(str(SHARED / 'tmux-message.sh')) + ' "$phase" "$pane" "$@" --stable-wait .05 --max-wait .15 --submit-delay .05 --render-wait .15 --receipt-wait .2 --retry-wait .05 --poll-interval .02\n')
        # Options after --prompt-file are accepted by argparse in shared sender.
        sender.chmod(0o700)
        spawn = shared / 'agent-spawn.sh'
        spawn.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\nbrief=Path(args[args.index("--prompt-file")+1]).read_text()\nPath(' + repr(str(self.directory / 'spawn.json')) + ').write_text(json.dumps({"args":args,"brief":brief}))\n')
        spawn.chmod(0o700)
        checker = self.directory / 'private-check'
        checker.write_text('#!/bin/sh\n[ "$1" = ' + shlex.quote(str(self.directory / 'remote.git')) + ' ]\n')
        checker.chmod(0o700)
        self.roles = self.directory / 'ROLES.md'
        self.roles.write_text('Common to every hat: first record before action.\n')
        self.env = dict(os.environ, SPAWN_TMUX_LABEL=self.label, RETIRE_REPO=str(self.repo), RETIRE_PRIVATE_CHECK=str(checker), RETIRE_SPAWN_SKILL=str(shared.parent))
        self.env.pop('SPAWN_TMUX_SOCKET', None)
        self.env.pop('TMUX', None)

    def tearDown(self):
        subprocess.run(self.tmux + ['kill-server'], capture_output=True)
        self.temp.cleanup()

    def command(self, *args):
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()

    def git(self, *args):
        return self.command('git', '-C', str(self.repo), *args)

    def actor(self, mode='done', node=False):
        fixture = Path(__file__).resolve()
        if node:
            fixture = self.directory / '@openai' / 'codex' / 'bin' / 'codex.js'
            fixture.parent.mkdir(parents=True)
            fixture.write_bytes(Path(__file__).read_bytes())
            link = self.directory / 'codex'
            link.symlink_to(fixture)
            fixture = link
        command = shlex.join([sys.executable, str(fixture), '--terminal', str(self.directory), mode])
        # argv[0] is the harness identity; outer shell survives process exit.
        inner = 'exec -a ' + ('node' if node else 'codex') + ' ' + command
        outer = shlex.join(['bash', '-c', inner]) + '; exec bash --norc'
        pane = self.command(*self.tmux, 'new-session', '-d', '-P', '-F', '#{pane_id}', '-x', '240', '-y', '40', outer)
        self.pane = pane
        self.wait_for(lambda: (self.directory / 'terminal.json').exists())
        return pane

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)

    def invoke(self, *args):
        return subprocess.run([os.environ.get('RETIRE_TEST_SCRIPT', str(HERE / 'retire.sh')), self.pane, '--wait', '.4', '--poll', '.02', *args], env=self.env, text=True, capture_output=True, timeout=12)

    def state(self):
        paths = list((self.repo / '.git' / 'retirements').glob('*.json'))
        self.assertEqual(len(paths), 1)
        return paths[0], json.loads(paths[0].read_text())

    def observed(self):
        return json.loads((self.directory / 'terminal.json').read_text())

    def test_retire_exit_to_shell_and_path_only_commit(self):
        self.actor()
        (self.repo / 'unrelated').write_text('preserve staged content')
        self.git('add', 'unrelated')
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads((self.directory / 'exit-order.json').read_text())['receipt_before_exit'])
        self.assertIn('bash', self.command(*self.tmux, 'list-panes', '-a', '-F', '#{pane_current_command}'))
        self.assertIn('A  unrelated', self.git('status', '--short'))
        self.assertNotIn('unrelated', self.git('ls-tree', '-r', '--name-only', 'origin/master'))
        path, state = self.state()
        ledger = [json.loads(line) for line in path.with_suffix('.jsonl').read_text().splitlines()]
        actions = [record['action'] for record in ledger]
        self.assertLess(actions.index('handoff-receipt'), actions.index('original-exit-confirmed'))
        self.assertTrue(any(record.get('receipt_type') == 'submitted' for record in ledger))
        self.assertFalse(any('payload' in record for record in ledger))
        index = (self.repo / 'waiting' / 'INDEX.md').read_text()
        self.assertIn('Session | Provider | Role | State | Last verified', index)
        self.assertIn('test-session-id | OpenAI | Utility | retired', index)

    def test_draft_modal_positive_twin_and_deliberate_retry(self):
        for mode in ('draft', 'modal'):
            with self.subTest(mode=mode):
                self.actor(mode)
                result = self.invoke()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.observed()['pastes'], 0)
                self.assertFalse((self.directory / 'exit-order.json').exists())
                path, state = self.state()
                self.assertEqual(state['offer']['status'], 'deferred')
                # Fixture is active: the target process and exact unsent human draft survive.
                self.assertTrue(Path('/proc/' + str(state['identity']['process']['pid'])).exists())
                if mode == 'draft':
                    self.assertIn('human unfinished draft', self.command(*self.tmux, 'capture-pane', '-p', '-t', self.pane))
                break  # modal gets its own isolated test below

    def test_modal(self):
        self.actor('modal')
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.observed()['pastes'], 0)
        self.assertIn('trust the contents', self.command(*self.tmux, 'capture-pane', '-p', '-t', self.pane))

    def test_ambiguous_never_repastes(self):
        self.actor('ambiguous')
        self.assertEqual(self.invoke().returncode, 2)
        path, state = self.state()
        self.assertEqual(state['offer']['status'], 'ambiguous')
        count = self.observed()['pastes']
        self.assertEqual(count, 1)
        self.assertEqual(self.invoke('--resume', str(path)).returncode, 2)
        self.assertEqual(self.invoke('--resume', str(path), '--retry-deferred', 'offer').returncode, 2)
        self.assertEqual(self.observed()['pastes'], count)

    def test_private_and_missing_remotes_before_send(self):
        self.actor()
        public = self.directory / 'public.git'
        self.command('git', 'clone', '--bare', str(self.directory / 'remote.git'), str(public))
        self.git('remote', 'set-url', 'origin', str(public))
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.observed()['pastes'], 0)
        self.git('remote', 'remove', 'origin')
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.observed()['pastes'], 0)

    def test_decline_caller_continuation(self):
        self.actor('decline')
        self.assertEqual(self.invoke().returncode, 2)
        path, state = self.state()
        self.assertEqual(state['disposition'], 'DECLINED')
        shard = path.parent / (state['id'] + '.shard')
        data = shard_data(state['id'], state['identity'])
        data.update(author='caller', declined=True, provenance='caller from committed artifacts; agent declined')
        shard.write_text(json.dumps(data))
        result = self.invoke('--resume', str(path), '--caller-shard')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.observed()['pastes'], 2)  # one offer and one bare exit
        self.assertEqual(json.loads(shard.read_text())['author'], 'caller')

    def test_waiting_requires_decision_owner(self):
        self.actor('waiting-bad')
        result = self.invoke('--waiting')
        self.assertEqual(result.returncode, 2)
        self.assertIn('pending decision', result.stderr)
        path, state = self.state()
        shard = path.parent / (state['id'] + '.shard')
        data = json.loads(shard.read_text())
        data['waiting'] = {'decision': 'Human scope decision', 'owner': 'Ted'}
        shard.write_text(json.dumps(data))
        self.assertEqual(self.invoke('--waiting', '--resume', str(path)).returncode, 0)

    def test_resource_pending_blocks_exit(self):
        self.actor('resource')
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn('resources', result.stderr)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_symlink_shard_blocks_exit(self):
        self.actor('path')
        self.assertEqual(self.invoke().returncode, 2)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_handoff_brief_model_after_exit(self):
        self.actor()
        result = self.invoke('--handoff', 'codex', 'test-model', '--roles', str(self.roles))
        self.assertEqual(result.returncode, 0, result.stderr)
        spawned = json.loads((self.directory / 'spawn.json').read_text())
        self.assertIn('test-model', spawned['args'])
        self.assertIn('Cold-start audit before action', spawned['brief'])
        self.assertIn('Common to every hat', spawned['brief'])
        self.assertIn('Mission authorized', spawned['brief'])
        path, state = self.state()
        self.assertFalse(Path('/proc/' + str(state['identity']['process']['pid'])).exists())
        self.assertEqual(self.invoke('--handoff', 'codex', 'test-model', '--roles', str(self.roles), '--resume', str(path)).returncode, 2)

    def test_no_successor_before_exit(self):
        self.actor('hold')
        result = self.invoke('--handoff', 'codex', 'test-model', '--roles', str(self.roles))
        self.assertEqual(result.returncode, 2)
        self.assertTrue((self.directory / 'exit-order.json').exists())
        self.assertFalse((self.directory / 'spawn.json').exists())

    def test_changed_identity_and_malformed_state(self):
        self.actor('decline')
        self.invoke()
        path, state = self.state()
        state['identity']['process']['start'] = 'wrong'
        path.write_text(json.dumps(state))
        result = self.invoke('--resume', str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn('identity changed', result.stderr)
        state['id'] = '../../escape'
        path.write_text(json.dumps(state))
        self.assertIn('invalid retirement event', self.invoke('--resume', str(path)).stderr)

    def test_push_failure_blocks_exit(self):
        self.actor()
        hook = self.directory / 'remote.git' / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        self.assertEqual(self.invoke().returncode, 2)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_old_done_does_not_complete(self):
        self.actor('old')
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.observed()['pastes'], 1)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_wrapped_user_done_does_not_complete(self):
        self.actor('quoted')
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.observed()['pastes'], 1)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_composer_done_does_not_complete(self):
        self.actor('composer')
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.observed()['pastes'], 1)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_agy_and_missing_launcher_preflight(self):
        self.actor()
        self.assertEqual(self.invoke('--handoff', 'agy', 'model', '--roles', str(self.roles)).returncode, 2)
        self.assertEqual(self.observed()['pastes'], 0)
        (self.directory / 'shared' / 'scripts' / 'agent-spawn.sh').unlink()
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.observed()['pastes'], 0)

    def test_preserved_shard_mutation_blocks_resumed_exit(self):
        self.actor('hold')
        self.invoke()
        path, state = self.state()
        canonical = self.repo / state['shard']
        canonical.write_text(canonical.read_text() + ' ')
        before = self.observed()['pastes']
        result = self.invoke('--resume', str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn('shard changed', result.stderr)
        self.assertEqual(self.observed()['pastes'], before)

    def test_real_node_entrypoint_and_claude_successor_model(self):
        self.actor('node-core', node=True)
        result = self.invoke('--handoff', 'claude', 'opus', '--roles', str(self.roles))
        self.assertEqual(result.returncode, 0, result.stderr)
        spawned = json.loads((self.directory / 'spawn.json').read_text())
        self.assertIn('claude', spawned['args'])
        self.assertIn('opus', spawned['args'])

    def test_required_recovery_fields(self):
        self.actor('waiting-bad')
        self.invoke('--waiting')
        path, state = self.state()
        shard = path.parent / (state['id'] + '.shard')
        data = json.loads(shard.read_text())
        data['waiting'] = {'decision': 'Human decision', 'owner': 'Ted'}
        del data['recovery']['heads']
        shard.write_text(json.dumps(data))
        self.assertIn('recovery requires heads', self.invoke('--waiting', '--resume', str(path)).stderr)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_extra_native_core_is_not_collapsed(self):
        self.actor('extra-core', node=True)
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn('exactly one', result.stderr)
        self.assertEqual(self.observed()['pastes'], 0)

    def test_explicit_no_paste_deferral_retry(self):
        self.actor('draft')
        self.assertEqual(self.invoke().returncode, 2)
        path, state = self.state()
        self.assertFalse(state['offer']['paste_attempted'])
        (self.directory / 'human-cleared-draft').touch()  # fixture human clears its own draft
        self.wait_for(lambda: 'human unfinished draft' not in self.command(*self.tmux, 'capture-pane', '-p', '-t', self.pane))
        result = self.invoke('--resume', str(path), '--retry-deferred', 'offer')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.observed()['pastes'], 2)

    def test_legacy_index_preserved_and_duplicate_refused(self):
        waiting = self.repo / 'waiting'
        waiting.mkdir()
        original = '# Steward index\n\n| Session | Provider | Role | State | Last verified |\n| legacy | Anthropic | Lead | parked by Ted | yesterday |\n\nException: keep decision owner.\n'
        (waiting / 'INDEX.md').write_text(original)
        (waiting / '2026-09-25-deadbeef-lead.md').write_text('Legacy shard and its evidence.\n')
        self.git('add', 'waiting')
        self.git('commit', '-m', 'legacy records')
        self.git('push')
        self.actor()
        self.assertEqual(self.invoke().returncode, 0)
        self.assertTrue((waiting / 'INDEX.md').read_text().startswith(original))
        self.assertIn('retire-agent:begin', (waiting / 'INDEX.md').read_text())

    def test_legacy_session_prefix_blocks_duplicate(self):
        waiting = self.repo / 'waiting'
        waiting.mkdir()
        (waiting / '2026-09-25-test-ses-lead.md').write_text('Existing recovery for this session.\n')
        self.actor()
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn('legacy session shard', result.stderr)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_nonobject_shard_is_controlled_failure(self):
        self.actor('waiting-bad')
        self.invoke('--waiting')
        path, state = self.state()
        shard = path.with_suffix('.shard')
        shard.write_text('[]')
        before = self.observed()['pastes']
        result = self.invoke('--waiting', '--resume', str(path))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('shard must be a JSON object', result.stderr)
        self.assertEqual(self.observed()['pastes'], before)
        self.assertFalse((self.directory / 'exit-order.json').exists())

    def test_index_symlink_preserves_outside_file(self):
        waiting = self.repo / 'waiting'
        waiting.mkdir()
        outside = self.directory / 'human-index'
        original = 'Unrelated human record.\n'
        outside.write_text(original)
        (waiting / 'INDEX.md').symlink_to(outside)
        self.actor()
        result = self.invoke()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('unsafe index path', result.stderr)
        self.assertEqual(outside.read_text(), original)
        self.assertFalse((self.directory / 'exit-order.json').exists())


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--terminal':
        terminal(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
