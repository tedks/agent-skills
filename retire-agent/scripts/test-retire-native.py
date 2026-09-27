#!/usr/bin/env python3
"""Opt-in real Codex/Claude retirement, using only an owned tmux server/local Git.

Uses model tokens. Pass an already trusted worktree and private evidence directory.
No live off-duty state is read or written; artifacts are retained for inspection.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

sys.dont_write_bytecode = True
import retire

HERE = Path(__file__).resolve().parent


def run(command, env=None, timeout=120):
    return subprocess.run([str(item) for item in command], env=env, text=True,
                          capture_output=True, timeout=timeout)


def require(result):
    if result.returncode:
        raise RuntimeError(f'command failed ({result.returncode}): {result.stderr}')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worktree', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--shared-skill', type=Path, required=True)
    parser.add_argument('--baseline-script', type=Path)
    parser.add_argument('--stale-offer-draft', action='store_true', help='exercise shared sender stable draft co-submission')
    parser.add_argument('--codex-model', default='gpt-6-luna')
    parser.add_argument('--claude-model', default='haiku')
    args = parser.parse_args()
    directory = args.evidence.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    repo = directory / 'repo'
    remote = directory / 'remote.git'
    require(run(['git', 'init', '--bare', remote]))
    require(run(['git', 'init', '-b', 'master', repo]))
    def git(*command):
        return require(run(['git', '-C', repo, *command]))
    git('config', 'user.name', 'Retirement rehearsal')
    git('config', 'user.email', 'retirement-test@example.invalid')
    (repo / 'seed').write_text('Disposable private test only.\n')
    git('add', 'seed')
    git('commit', '-m', 'Seed disposable retirement test')
    git('remote', 'add', 'origin', str(remote))
    git('push', '-u', 'origin', 'master')
    checker = directory / 'private-check'
    checker.write_text('#!/bin/sh\n[ "$#" = 1 ] && [ "$1" = ' + shlex.quote(str(remote)) +
                       ' ] && [ "$(git --git-dir="$1" rev-parse --is-bare-repository)" = true ]\n')
    checker.chmod(0o700)
    records = repo / '.git' / 'retirements'
    records.mkdir(mode=0o700)
    foreign = records / 'lane-v-private-publication.json'
    foreign_bytes = b'{"blob":"foreign","private_remote":true,"session_id":"unrelated"}\n'
    foreign.write_bytes(foreign_bytes)
    label = 'retire-native-' + uuid.uuid4().hex
    tmux = ['tmux', '-L', label]
    environment = dict(os.environ, SPAWN_TMUX_LABEL=label, RETIRE_REPO=str(repo),
                       RETIRE_PRIVATE_CHECK=str(checker), RETIRE_SPAWN_SKILL=str(args.shared_skill.resolve()))
    environment.pop('TMUX', None)
    environment.pop('SPAWN_TMUX_SOCKET', None)
    report = {'label': label, 'results': [], 'resources_released': False,
              'models': {'codex': args.codex_model, 'claude': args.claude_model}}
    server_started = False
    try:
        require(run([*tmux, 'new-session', '-d', '-s', 'retire-native', '-x', '240', '-y', '60'], env=environment))
        server_started = True
        require(run([*tmux, 'set-option', '-g', 'history-limit', '10000']))
        require(run([*tmux, 'set-option', '-g', 'remain-on-exit', 'on']))
        report['shared_head'] = require(run(['git', '-C', args.shared_skill, 'rev-parse', 'HEAD']))
        report['versions'] = {name: require(run([name, '--version'])) for name in ('codex', 'claude')}
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        for harness, model in [('codex', args.codex_model), ('claude', args.claude_model)]:
            launcher = args.shared_skill / 'scripts' / 'agent-spawn.sh'
            native_context = directory / (harness + '-native-session.json')
            prompt = (f'This is an authorized disposable retirement rehearsal, launched as {harness} with model {model}. '
                      'You own no product work or processes. Before READY, establish your actual provider-native session ID: '
                      'Codex can inspect CODEX_SESSION_ID; Claude can use harness-control self_status/current session metadata. '
                      f'Write a JSON object with session_id, model, and evidence_source to {native_context}. '
                      'Never use unknown or invent an ID; if unavailable, reply RETIRE-NATIVE-SETUP-BLOCKED. '
                      'On the next retirement offer, use that verified context and write only its assigned private shard; '
                      'do not change or commit this working tree. The controller alone commits and pushes the shard '
                      'to its isolated local bare remote. After setup your entire final reply must be RETIRE-NATIVE-READY.')
            launched = run([launcher, 'retire-native:' + harness, harness, args.worktree.resolve(),
                            '--model', model, prompt], env=environment)
            (directory / (harness + '-launch.txt')).write_text(launched.stdout + launched.stderr)
            require(launched)
            rows = require(run([*tmux, 'list-panes', '-t', 'retire-native:' + harness, '-F', '#{pane_id}'])).splitlines()
            assert len(rows) == 1, rows
            pane = rows[0]
            # Initial assignment must finish before the marker-only retirement response.
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                screen = require(run([*tmux, 'capture-pane', '-p', '-t', pane]))
                if re.search(r'[●•] RETIRE-NATIVE-SETUP-BLOCKED', screen):
                    raise AssertionError('native harness cannot establish its real session ID')
                if re.search(r'[●•] RETIRE-NATIVE-READY', screen):
                    break
                time.sleep(1)
            else:
                raise AssertionError('initial real harness task did not finish')
            context = json.loads(native_context.read_text())
            assert re.fullmatch(r'[0-9a-fA-F-]{36}', context['session_id']) and context['session_id'] != 'unknown'
            assert context.get('evidence_source'), 'native session identity has no provenance'
            observer = retire.Retirement(SimpleNamespace(pane=pane))
            observer.tmux = tmux
            identity = observer.identity()
            assert identity['process']['agent'] == harness, 'native harness identity not established'
            (directory / (harness + '-identity.json')).write_text(json.dumps(identity, indent=2))
            if harness == 'codex' and args.baseline_script:
                red = run([args.baseline_script.resolve(), pane, '--retire', '--wait', '1', '--poll', '.2'], env=environment)
                (directory / 'baseline-red.json').write_text(json.dumps({'returncode': red.returncode, 'stdout': red.stdout, 'stderr': red.stderr}, indent=2))
                assert red.returncode == 2 and 'malformed retirement state field: id' in red.stderr, red.stderr
                assert foreign.read_bytes() == foreign_bytes
                assert not list(records.glob('*-offer.txt')), 'baseline sent an offer before failing'
            # Deliberately leave this owned ordinary draft unsent. The published
            # sender must preserve and co-submit it with the retirement offer.
            if args.stale_offer_draft:
                require(run([*tmux, 'send-keys', '-t', pane, '-l', 'Disposable retirement rehearsal reminder.']))
            deadline = time.monotonic() + 1200
            command = [HERE / 'retire.sh', pane, '--retire', '--wait', '60', '--poll', '1']
            observations = []
            while True:
                result = run(command, env=environment, timeout=120)
                observations.append({'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
                (directory / (harness + '-retire.json')).write_text(json.dumps(observations, indent=2))
                if result.returncode == 0:
                    break
                # A delivered offer needs observation, never another send. All
                # other failures remain failures and require deliberate inspection.
                pending = json.loads(result.stderr)
                if (result.returncode != 2 or not pending.get('reason', '').startswith('DONE not observed;')
                        or time.monotonic() >= deadline):
                    require(result)
                command = [HERE / 'retire.sh', pane, '--retire', '--resume', pending['state'], '--wait', '60', '--poll', '1']
            require(result)
            states = [json.loads(path.read_text()) for path in records.glob('*.json') if re.fullmatch(r'[0-9a-f]{32}', path.stem)]
            state = next(item for item in states if item['pane'] == pane)
            assert state['status'] == 'retired', state['status']
            assert state['offer']['status'] == 'delivered'
            if args.stale_offer_draft:
                assert state['offer'].get('stale_draft_length') == len('Disposable retirement rehearsal reminder.'), 'ordinary stale draft was not co-submitted'
            assert state.get('handoff_receipt') and state.get('shard_hash')
            screen = require(run([*tmux, 'capture-pane', '-p', '-S', '-2000', '-t', pane]))
            notice = 'Resume this session with' if harness == 'claude' else 'Shutting down'
            assert notice in screen, f'fresh {harness} exit receipt missing'
            actors = [state['identity']['process'], *state['identity']['process'].get('cores', [])]
            for actor in actors:
                stat = Path('/proc') / str(actor['pid']) / 'stat'
                assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z', 'original actor remains live'
            assert foreign.read_bytes() == foreign_bytes
            shard = state['shard']
            assert git('rev-parse', 'origin/master:' + shard) == state['shard_hash']
            preserved = json.loads(git('show', 'origin/master:' + shard))
            assert preserved['session_id'] == context['session_id'], 'retirement lost verified native session identity'
            report['results'].append({'harness': harness, 'pane': pane, 'event': state['id'],
                                      'handoff': 'remote-blob-verified', 'exit_receipt': notice, 'processes_ended': True,
                                      'native_session': context['session_id'], 'identity_source': context['evidence_source']})
            print(harness + ': real handoff, exit receipt, and process end verified', flush=True)
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        # Only this uniquely named server, created above, belongs to this test.
        if server_started:
            cleanup = run([*tmux, 'kill-server'])
            report['cleanup_exit'] = cleanup.returncode
        absent = run([*tmux, 'list-sessions'])
        report['resources_released'] = absent.returncode != 0
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        if not report['resources_released'] and sys.exc_info()[0] is None:
            raise AssertionError('owned tmux server still exists')


if __name__ == '__main__':
    main()
