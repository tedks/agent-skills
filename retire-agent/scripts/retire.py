#!/usr/bin/env python3
"""Polite retirement; no model calls, terminal replay, or process killing."""
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


class Stop(Exception):
    pass


@contextmanager
def locked(path):
    with path.open('a') as handle:
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Stop('retirement transaction busy')
                time.sleep(.1)
        yield


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], text=True, capture_output=True,
                          timeout=45, **kwargs)


def checked(args):
    result = run(args)
    if result.returncode:
        raise Stop('command failed: ' + str(args[0]))
    return result.stdout.strip()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def proc(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        agent = Path(argv[0].decode()).name
        if agent == 'node' and len(argv) > 1:
            entry = str(Path(argv[1].decode()).resolve())
            if entry.endswith('/@openai/codex/bin/codex.js') or entry.endswith('/@anthropic-ai/claude-code/cli.js'):
                agent = 'codex' if '@openai/codex/' in entry else 'claude'
        return {'pid': int(pid), 'parent': int(text[1]), 'start': text[19],
                'agent': agent, 'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'wrapper': Path(argv[0].decode()).name == 'node' and agent in ('codex', 'claude'),
                'native': '/@openai/codex/' in argv[0].decode() and '/vendor/' in argv[0].decode()}
    except (OSError, ValueError, UnicodeError):
        return None


class Retirement:
    def __init__(self, args):
        self.args = args
        self.repo = Path(os.environ.get('RETIRE_REPO', '~/Projects/off-duty')).expanduser().resolve()
        sibling = Path(__file__).resolve().parents[2] / 'spawn-agent'
        self.shared = Path(os.environ.get('RETIRE_SPAWN_SKILL', str(sibling))).expanduser() / 'scripts'
        self.tmux = ['tmux']
        if os.environ.get('SPAWN_TMUX_SOCKET'):
            self.tmux += ['-S', os.environ['SPAWN_TMUX_SOCKET']]
        elif os.environ.get('SPAWN_TMUX_LABEL'):
            self.tmux += ['-L', os.environ['SPAWN_TMUX_LABEL']]
        elif os.environ.get('TMUX'):
            self.tmux += ['-S', os.environ['TMUX'].split(',')[0]]
        self.state = None
        self.path = None

    def git(self, *args):
        return checked(['git', '-C', self.repo, *args])

    def private(self):
        urls = set(self.git('remote', 'get-url', '--all', 'origin').splitlines())
        urls.update(self.git('remote', 'get-url', '--push', '--all', 'origin').splitlines())
        for url in urls:
            checker = os.environ.get('RETIRE_PRIVATE_CHECK')
            if checker:
                if run([checker, url]).returncode:
                    raise Stop('origin privacy not verified')
            else:
                match = re.fullmatch(r'(?:git@github\.com:|https://github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?', url)
                if not match:
                    raise Stop('unknown origin; privacy not verified')
                if checked(['gh', 'repo', 'view', match[1], '--json', 'isPrivate', '--jq', '.isPrivate']) != 'true':
                    raise Stop('origin is not private')

    def pane(self):
        rows = checked(self.tmux + ['list-panes', '-a', '-F', '#{pane_id}\t#{pane_pid}\t#{session_name}\t#{pane_current_path}'])
        for row in rows.splitlines():
            fields = row.split('\t')
            if fields[0] == self.args.pane:
                return fields
        raise Stop('pane disappeared')

    def actors(self, root):
        processes = [p for path in Path('/proc').iterdir() if path.name.isdigit() and (p := proc(path.name))]
        descendants = {int(root)}
        while True:
            new = {p['pid'] for p in processes if p['parent'] in descendants}
            if new <= descendants:
                break
            descendants |= new
        actors = [p for p in processes if p['pid'] in descendants and p['agent'] in ('claude', 'codex', 'agy')]
        removed = set()
        for wrapper in actors:
            if not wrapper['wrapper']:
                continue
            cores = [p for p in actors if p['parent'] == wrapper['pid'] and p['agent'] == wrapper['agent'] and p['native']]
            if len(cores) <= 1:
                wrapper['cores'] = cores
                removed.update(p['pid'] for p in cores)
        return [p for p in actors if p['pid'] not in removed]

    def identity(self):
        fields = self.pane()
        actors = self.actors(fields[1])
        if len(actors) != 1:
            raise Stop('expected exactly one identifiable harness process')
        return {'process': actors[0], 'pane_pid': fields[1], 'session': fields[2], 'directory': fields[3]}

    def verify_identity(self):
        if self.identity() != self.state['identity']:
            raise Stop('pane/process identity changed')

    def persist(self):
        save(self.path, self.state)

    def audit(self, action, **fields):
        record = {'event': self.state['id'], 'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'pane': self.args.pane, 'target': self.args.pane, 'marker': self.state['id'], 'action': action, 'transport_exit': None, 'receipt_type': action,
                  'shard_path': self.state.get('shard'), 'exit_confirmed': self.state.get('status') == 'retired', **fields}
        path = self.path.with_suffix('.jsonl')
        with path.open('a') as ledger:
            path.chmod(0o600)
            ledger.write(json.dumps(record) + '\n')
            ledger.flush()
            os.fsync(ledger.fileno())

    def send(self, phase, payload):
        self.verify_identity()
        attempt = self.state.get(phase)
        if attempt:
            if attempt['status'] == 'delivered':
                return
            raise Stop(f'{phase} has an unresolved send; inspect receipt, never replay')
        payload_path = self.path.parent / (self.state['id'] + '-' + phase + '.txt')
        payload_path.write_text(payload)
        payload_path.chmod(0o600)
        self.state[phase] = {'status': 'attempting', 'payload': str(payload_path)}
        self.persist()  # A crash from here is ambiguous, never a resend.
        self.audit('send-attempt', phase=phase)
        result = run([self.shared / 'tmux-message.sh', 'send', self.args.pane,
                      '--backoff', '--max-wait', '10', '--prompt-file', payload_path])
        try:
            report = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        except ValueError:
            report = {'status': 'ambiguous', 'reason': 'sender did not return JSON'}
        # Sender reports never include raw terminal text. Preserve pending_file.
        self.state[phase].update(report)
        if result.returncode == 0 and report.get('status') == 'delivered' and report.get('receipt') in ('submitted', 'queued', 'command'):
            self.state[phase]['status'] = 'delivered'
        elif result.returncode == 2 and not report.get('paste_attempted', True):
            self.state[phase]['status'] = 'deferred'
        else:
            self.state[phase]['status'] = 'ambiguous'
        self.persist()
        self.audit('sender-result', phase=phase, transport_exit=result.returncode,
                   receipt_type=report.get('receipt'), status=self.state[phase]['status'])
        if self.state[phase]['status'] != 'delivered':
            raise Stop(f'{phase} {self.state[phase]["status"]}; exact payload preserved')

    def observe(self):
        self.verify_identity()
        result = checked(self.tmux + ['capture-pane', '-p', '-t', self.args.pane, '-S', '-2000'])
        event = self.state['id']
        # Require an assistant row marker, above the final live composer. Bare
        # wrapped user text and live input cannot satisfy completion.
        lines = result.splitlines()
        composers = [i for i, line in enumerate(lines) if re.match(r'^\s*[›❯](?:\s|$)', line)]
        result = '\n'.join(lines[:composers[-1]]) if composers else ''
        for kind in ('DONE', 'DECLINED'):
            if re.search(r'^[●•] RETIRE-' + kind + '-' + event + r'\s*$', result, re.M):
                return kind
        return None

    def validate_shard(self, path):
        if path.is_symlink() or not path.is_file() or path.resolve() != self.path.parent / (self.state['id'] + '.shard'):
            raise Stop('missing or unsafe unique shard')
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise Stop('shard must be a JSON object')
        if data.get('event') != self.state['id'] or data.get('identity') != self.state['identity']:
            raise Stop('shard event/identity mismatch')
        expected = 'caller' if self.args.caller_shard else 'agent'
        if data.get('author') != expected or not isinstance(data.get('recovery'), dict):
            raise Stop('shard must have honest author and operational recovery')
        if expected == 'caller' and data.get('declined') is not True:
            raise Stop('caller shard must record declined=true; do not invent agent thanks')
        for key in ('date', 'session_id', 'harness', 'model', 'working_directory', 'tmux_window', 'role', 'landed_work', 'lessons', 'provenance'):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise Stop('shard requires ' + key)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', data['date']) or not re.fullmatch(r'[\w.-]{1,128}', data['session_id']):
            raise Stop('invalid date/session identity')
        datetime.date.fromisoformat(data['date'])
        if not 1 <= len(data['lessons'].splitlines()) <= 5:
            raise Stop('lessons must be one to five lines')
        if data['session_id'] == 'unknown' and expected != 'caller':
            raise Stop('agent must establish its own session identity')
        for previous in (self.repo / 'waiting').glob('*.md'):
            if previous.name == 'INDEX.md':
                continue
            try:
                prior = json.loads(previous.read_text())
                if not isinstance(prior, dict):
                    raise ValueError('legacy non-object record')
            except ValueError:
                if len(data['session_id']) >= 8 and data['session_id'][:8].lower() in previous.name.lower():
                    raise Stop('legacy session shard exists; reconcile deliberately')
                continue
            if data['session_id'] != 'unknown' and prior.get('session_id') == data['session_id'] and prior.get('event') != data['event']:
                raise Stop('session already has a preserved shard')
        if data['harness'] != self.state['identity']['process']['agent'] or data['working_directory'] != self.state['identity']['directory']:
            raise Stop('shard harness/worktree identity differs')
        recovery = data['recovery']
        for key in ('mission', 'authorization', 'pauses', 'worktree', 'branch', 'heads', 'tested_vs_merged', 'evidence', 'limits', 'failures', 'open_decisions', 'outstanding_sends', 'next_action'):
            if key not in recovery or recovery[key] is None or recovery[key] == '':
                raise Stop('recovery requires ' + key + ' (explicit none allowed)')
        if not isinstance(recovery['open_decisions'], list) or any(not isinstance(d, dict) or not d.get('decision') or not d.get('owner') for d in recovery['open_decisions']):
            raise Stop('open decisions need named owners')
        if 'exact_resume_command' in data and list(data)[-1] != 'exact_resume_command':
            raise Stop('optional native resume command must come last')
        if data.get('personal_thanks') and not data.get('thanks_provenance'):
            raise Stop('personal thanks require provenance')
        if expected == 'caller' and data.get('personal_thanks') and data.get('thanks_provenance') != 'quoted-agent':
            raise Stop('caller cannot invent agent personal thanks')
        resources = data.get('resources')
        if not isinstance(resources, list) or any(not isinstance(r, dict) or not r.get('name') or not r.get('receipt') or r.get('status') not in ('released', 'transferred') or (r['status'] == 'transferred' and not r.get('owner')) for r in resources):
            raise Stop('resources require release receipts or named transfer owners')
        if self.args.waiting and (not isinstance(data.get('waiting'), dict) or not data['waiting'].get('decision') or not data['waiting'].get('owner')):
            raise Stop('waiting shard needs pending decision and human owner')
        return data

    def publish(self, paths, index=False):
        with locked(self.common / 'retirement.lock'):
            if index:
                entries = ['| Shard | Session | Provider | Role | State | Last verified |\n| --- | --- | --- | --- | --- | --- |\n']
                def cell(value):
                    return str(value).replace('|', '/').replace('\n', ' ')
                for path in sorted((self.repo / 'waiting').glob('*.md')):
                    if path.name == 'INDEX.md':
                        continue
                    try:
                        data = json.loads(path.read_text())
                        if not isinstance(data, dict):
                            continue
                        receipt = self.repo / 'receipts' / (data['event'] + '.json')
                        record = json.loads(receipt.read_text()) if receipt.exists() else {}
                        if record.get('event') != data['event'] or record.get('shard') != 'waiting/' + path.name:
                            continue  # Existing external/legacy metadata has another owner.
                        status = record['status']
                        provider = {'codex': 'OpenAI', 'claude': 'Anthropic', 'agy': 'Google'}.get(data.get('harness'), 'unknown')
                        values = (data.get('session_id', 'unknown'), provider, data.get('role', 'unknown'), status, record.get('verified_at', 'unknown'))
                        entries.append(f'| [{path.name}]({path.name}) | ' + ' | '.join(cell(value) for value in values) + ' |\n')
                    except (ValueError, KeyError, OSError):
                        continue
                index_path = self.repo / 'waiting' / 'INDEX.md'
                if index_path.is_symlink() or (index_path.exists() and not index_path.is_file()):
                    raise Stop('unsafe index path; preserve for reconciliation')
                begin, end = '<!-- retire-agent:begin -->', '<!-- retire-agent:end -->'
                original = index_path.read_text() if index_path.exists() else '# Waiting and retired sessions\n'
                section = begin + '\n' + ''.join(entries) + end
                if begin in original or end in original:
                    if original.count(begin) != 1 or original.count(end) != 1 or original.index(begin) >= original.index(end):
                        raise Stop('malformed generated index section; preserve for reconciliation')
                    updated = original[:original.index(begin)] + section + original[original.index(end) + len(end):]
                else:
                    updated = original + '\n\n' + section + '\n'
                index_path.write_text(updated)
                paths = [*paths, 'waiting/INDEX.md']
            self.publish_locked(paths)

    def publish_locked(self, paths):
        self.private()
        if self.git('branch', '--show-current') != 'master':
            raise Stop('private records checkout must be on master')
        self.git('fetch', 'origin', 'master')
        if run(['git', '-C', self.repo, 'merge-base', '--is-ancestor', 'origin/master', 'HEAD']).returncode:
            raise Stop('private master diverged; reconcile deliberately')
        if self.git('diff', 'HEAD', '--', *paths) or any(not run(['git', '-C', self.repo, 'ls-files', '--error-unmatch', p]).returncode == 0 for p in paths):
            self.git('add', '--', *paths)
            self.git('commit', '--only', '-m', 'Preserve retirement ' + self.state['id'], '--', *paths)
        self.git('push', 'origin', 'HEAD:master')
        self.git('fetch', 'origin', 'master')
        for path in paths:
            local = self.git('hash-object', path)
            remote = self.git('rev-parse', 'origin/master:' + path)
            if local != remote:
                raise Stop('private remote shard verification failed')

    def ended(self):
        original = self.state['identity']['process']
        current = proc(original['pid'])
        if current and current['start'] == original['start'] and current['boot'] == original['boot']:
            return False
        for core in original.get('cores', []):
            remaining = proc(core['pid'])
            if remaining and remaining['start'] == core['start'] and remaining['boot'] == core['boot']:
                return False
        try:
            fields = self.pane()
        except Stop:
            return True  # original process was independently verified ended
        if self.actors(fields[1]):
            raise Stop('replacement actor found; successor spawn withheld')
        return True

    def execute(self):
        self.private()  # No control send to an unverified destination.
        if any(not os.access(self.shared / name, os.X_OK) for name in ('tmux-message.sh', 'agent-spawn.sh')):
            raise Stop('shared sender/launcher missing or not executable')
        common = Path(self.git('rev-parse', '--git-common-dir'))
        if not common.is_absolute():
            common = self.repo / common
        self.common = common
        directory = common / 'retirements'
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        if self.args.resume:
            requested = Path(self.args.resume)
            self.path = requested.resolve()
            if self.path.parent != directory.resolve() or requested.is_symlink():
                raise Stop('resume requires the private state path from this repository')
            self.state = json.loads(self.path.read_text())
            if not re.fullmatch(r'[0-9a-f]{32}', self.state['id']) or self.path.stem != self.state['id']:
                raise Stop('invalid retirement event identity')
            if self.state['pane'] != self.args.pane or self.state['mode'] != self.args.mode:
                raise Stop('resume pane/mode differs')
            if self.state.get('shard') and not re.fullmatch(r'waiting/[\w.-]+\.md', self.state['shard']):
                raise Stop('invalid preserved shard path')
        else:
            if self.args.caller_shard:
                raise Stop('caller shard requires deliberate --resume')
            with locked(self.common / 'retirement.lock'):
                identity = self.identity()
                for existing in directory.glob('*.json'):
                    previous = json.loads(existing.read_text())
                    if previous['identity'] == identity and previous.get('status') != 'retired':
                        raise Stop('existing attempt requires --resume ' + str(existing))
                event = uuid.uuid4().hex
                self.path = directory / (event + '.json')
                self.state = {'id': event, 'pane': self.args.pane, 'identity': identity, 'mode': self.args.mode, 'status': 'requesting'}
                self.persist()
        self.event_lock = (directory / (self.state['id'] + '.lock')).open('a')
        try:
            fcntl.flock(self.event_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('same retirement event already active')
        self.event_owned = True
        event = self.state['id']
        receipt = self.repo / 'receipts' / (event + '.json')
        receipt.parent.mkdir(mode=0o700, exist_ok=True)
        if receipt.parent.is_symlink():
            raise Stop('receipt directory must not be a symlink')
        if not receipt.exists():
            save(receipt, {'event': event, 'status': 'retirement-requested', 'mode': self.state['mode']})
            self.publish(['receipts/' + receipt.name])
        shard = directory / (event + '.shard')
        if self.args.retry_deferred:
            phase = self.args.retry_deferred
            attempt = self.state.get(phase, {})
            if attempt.get('status') != 'deferred' or attempt.get('paste_attempted', True):
                raise Stop('retry is allowed only for proven no-paste deferral')
            if phase == 'exit' and self.ended():
                raise Stop('original process already ended; no retry into shell/replacement')
            del self.state[phase]
            self.persist()
        if not self.state.get('handoff_receipt'):
            if not self.args.caller_shard:
                protocol = Path(__file__).resolve().parents[1] / 'references' / 'protocol.md'
                self.send('offer', f'Thank you for your work. Every agent at every tier is offered retirement and off-duty time. Event {event}. Stop product work; preserve operational recovery separately from any personal thanks. Read {protocol} and write {shard} using its complete JSON schema, event {event}, identity {json.dumps(self.state["identity"])}. Include date/session_id/harness/model/working_directory/tmux_window/role/landed_work/lessons/provenance, structured recovery and resources. Do not exit. After writing your shard respond on a standalone line RETIRE-DONE-{event}. You may decline; respond on a standalone line RETIRE-DECLINED-{event} and the caller will author recovery with honest attribution. Off-duty is not permission for product work.' + (' Include waiting.decision and waiting.owner for the pending human decision.' if self.args.waiting else ''))
                deadline = time.monotonic() + self.args.wait
                disposition = None
                while time.monotonic() < deadline:
                    disposition = self.observe()
                    if disposition:
                        break
                    time.sleep(min(self.args.poll, max(0, deadline - time.monotonic())))
                self.state['disposition'] = disposition
                self.persist()
                self.audit('completion-observed', disposition=disposition)
                if disposition != 'DONE':
                    raise Stop('agent declined; author caller shard then --resume --caller-shard' if disposition == 'DECLINED' else 'DONE not observed; resume observes, never resends')
            elif self.state.get('disposition') != 'DECLINED':
                raise Stop('caller continuation requires observed agent decline')
            self.verify_identity()
            data = self.validate_shard(shard)
            waiting = self.repo / 'waiting'
            waiting.mkdir(mode=0o700, exist_ok=True)
            if waiting.is_symlink():
                raise Stop('waiting directory must not be a symlink')
            canonical = waiting / (data['date'] + '-' + data['session_id'][:12] + '-retire-' + event + '.md')
            if canonical.exists() and canonical.read_bytes() != shard.read_bytes():
                raise Stop('canonical shard already exists with different content')
            canonical.write_bytes(shard.read_bytes())
            canonical.chmod(0o600)
            self.state['shard'] = 'waiting/' + canonical.name
            self.publish([self.state['shard']], index=True)
            receipt = self.repo / 'receipts' / (event + '.json')
            receipt.parent.mkdir(mode=0o700, exist_ok=True)
            save(receipt, {'event': event, 'shard': self.state['shard'], 'commit': self.git('rev-parse', 'origin/master'), 'author': data['author'], 'status': 'handoff-verified', 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
            self.publish(['receipts/' + receipt.name], index=True)
            self.state['handoff_receipt'] = 'receipts/' + receipt.name
            self.state['shard_hash'] = self.git('hash-object', self.state['shard'])
            self.persist()
            self.audit('handoff-receipt', shard_path=self.state['shard'], receipt_path=self.state['handoff_receipt'])
        # Reverify immutable handoff evidence before resumed exit or successor.
        with locked(self.common / 'retirement.lock'):
            self.private()
            self.git('fetch', 'origin', 'master')
            if self.git('hash-object', self.state['shard']) != self.state['shard_hash'] or self.git('rev-parse', 'origin/master:' + self.state['shard']) != self.state['shard_hash']:
                raise Stop('preserved shard changed after receipt')
            record = json.loads(self.git('show', 'origin/master:' + self.state['handoff_receipt']))
            if record.get('event') != event or record.get('shard') != self.state['shard'] or not re.fullmatch(r'[0-9a-f]{40,64}', record.get('commit', '')):
                raise Stop('private handoff receipt changed or missing')
        if not self.state.get('exit'):
            if self.ended():
                raise Stop('original process already ended before exit attempt; no send')
            try:
                self.send('exit', '/exit' if self.state['identity']['process']['agent'] != 'agy' else '/quit')
            except Stop:
                if not self.state.get('exit'):
                    raise
        deadline = time.monotonic() + self.args.wait
        while not self.ended():
            if time.monotonic() >= deadline:
                raise Stop('handoff verified; retirement pending original process exit')
            time.sleep(.25)
        self.state['status'] = 'retired'
        self.persist()
        receipt = self.repo / self.state['handoff_receipt']
        record = json.loads(receipt.read_text())
        record['status'] = 'retired'
        record['process_exit'] = 'original-process-and-recorded-native-core-ended'
        record['verified_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(receipt, record)
        self.publish([self.state['handoff_receipt']], index=True)
        self.audit('original-exit-confirmed', exit_confirmed=True, shard_path=self.state['shard'])
        if self.args.handoff:
            if self.state.get('spawn'):
                raise Stop('successor already attempted; inspect launcher receipt, never respawn')
            roles = Path(self.args.roles).expanduser().resolve()
            brief = directory / (event + '-brief.txt')
            brief.write_text('Cold-start audit before action: reconstruct state from this shard and committed artifacts alone; file any protocol defect first. Establish identity and write your first durable record before acting.\n\n' + (self.repo / self.state['shard']).read_text() + '\n\n' + roles.read_text())
            brief.chmod(0o600)
            self.state['spawn'] = {'status': 'attempting', 'brief': str(brief)}
            self.persist()
            result = run([self.shared / 'agent-spawn.sh', self.state['identity']['session'] + ':successor-' + event[:12], self.args.handoff[0], self.state['identity']['directory'], '--model', self.args.handoff[1], '--prompt-file', brief])
            self.state['spawn']['status'] = 'delivered' if result.returncode == 0 else 'unconfirmed'
            self.persist()
            if result.returncode:
                raise Stop('successor launcher unconfirmed; inspect private brief and launcher pane before any retry')
        print(json.dumps({'status': 'retired', 'state': str(self.path), 'receipt': self.state['handoff_receipt']}))

    def record_pending(self):
        if not self.state or not getattr(self, 'event_owned', False):
            return
        receipt = self.repo / 'receipts' / (self.state['id'] + '.json')
        if receipt.exists():
            record = json.loads(receipt.read_text())
            record['status'] = 'retired' if self.state.get('status') == 'retired' else ('pending-exit' if self.state.get('handoff_receipt') else 'pending-handoff')
            record['offer'] = self.state.get('offer', {}).get('status', 'not-attempted')
            record['exit'] = self.state.get('exit', {}).get('status', 'not-attempted')
            save(receipt, record)
            self.publish(['receipts/' + receipt.name], index=bool(self.state.get('shard')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pane')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--retire', action='store_true')
    modes.add_argument('--waiting', action='store_true')
    modes.add_argument('--handoff', nargs=2, metavar=('AGENT', 'MODEL'))
    parser.add_argument('--resume')
    parser.add_argument('--caller-shard', action='store_true')
    parser.add_argument('--retry-deferred', choices=('offer', 'exit'))
    parser.add_argument('--wait', type=float, default=300)
    parser.add_argument('--poll', type=float, default=60)
    parser.add_argument('--roles', default='~/Projects/orgs/master/doctrine/ROLES.md')
    args = parser.parse_args()
    if not re.fullmatch(r'%[0-9]+', args.pane) or not 0 < args.wait <= 600 or not 0 < args.poll <= 90:
        parser.error('stable %pane and wait in (0,600] required')
    if args.handoff and args.handoff[0] not in ('codex', 'claude'):
        parser.error('published launcher supports explicit model selection for codex/claude only')
    if args.handoff and (not args.handoff[1].strip() or not Path(args.roles).expanduser().is_file()):
        parser.error('handoff needs model and readable ROLES.md')
    args.mode = 'handoff:' + ':'.join(args.handoff) if args.handoff else ('waiting' if args.waiting else 'retire')
    retirement = Retirement(args)
    try:
        retirement.execute()
    except (Stop, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        try:
            if getattr(retirement, 'event_owned', False):
                retirement.audit('failure-or-deferral', failure_type=type(error).__name__)
            retirement.record_pending()
        except (Stop, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass  # Primary failure and exact outgoing state are retained locally.
        print(json.dumps({'status': 'pending', 'reason': str(error), 'state': str(retirement.path) if retirement.path else None}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
