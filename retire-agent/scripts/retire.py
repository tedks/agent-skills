#!/usr/bin/env python3
"""Polite retirement; no model calls, terminal replay, or process killing."""
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True  # Loading the shared API must not modify its installation.


class Stop(Exception):
    pass


class PaneAbsent(Stop):
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


def checked(args, strip=True):
    result = run(args)
    if result.returncode:
        raise Stop('command failed: ' + str(args[0]))
    return result.stdout.strip() if strip else result.stdout


def save(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2) + '\n').encode())


def atomic_bytes(path, content):
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)  # Only this call's exclusively created temporary.


def object_record(text, label):
    try:
        record = json.loads(text)
    except ValueError as error:
        raise Stop('malformed ' + label + '; preserve for reconciliation') from error
    if not isinstance(record, dict):
        raise Stop('malformed ' + label + '; expected JSON object')
    return record


def state_record(text):
    record = object_record(text, 'retirement state')
    for key in ('id', 'pane', 'mode', 'status'):
        if not isinstance(record.get(key), str):
            raise Stop('malformed retirement state field: ' + key)
    if not isinstance(record.get('socket'), str) or not Path(record['socket']).is_absolute():
        raise Stop('malformed retirement socket identity')
    identity = record.get('identity')
    if not isinstance(identity, dict) or any(not isinstance(identity.get(key), str) for key in ('pane_pid', 'session', 'directory')):
        raise Stop('malformed retirement identity')
    process = identity.get('process')
    if not isinstance(process, dict) or not isinstance(process.get('cores', []), list):
        raise Stop('malformed retirement process identity')
    for actor in [process, *process.get('cores', [])]:
        if (not isinstance(actor, dict) or type(actor.get('pid')) is not int or actor['pid'] <= 0
                or any(not isinstance(actor.get(key), str) for key in ('start', 'boot', 'agent'))):
            raise Stop('malformed retirement process identity')
    for key in ('offer', 'exit', 'spawn'):
        if key in record and (not isinstance(record[key], dict) or not isinstance(record[key].get('status'), str)):
            raise Stop('malformed retirement attempt: ' + key)
    for key in ('shard', 'handoff_receipt', 'shard_hash'):
        if key in record and not isinstance(record[key], str):
            raise Stop('malformed retirement evidence: ' + key)
    return record


def receipt_record(text):
    record = object_record(text, 'retirement receipt')
    for key in ('event', 'status'):
        if not isinstance(record.get(key), str):
            raise Stop('malformed retirement receipt field: ' + key)
    for key in ('shard', 'commit', 'author', 'verified_at'):
        if key in record and not isinstance(record[key], str):
            raise Stop('malformed retirement receipt field: ' + key)
    return record


def proc(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if text[0] == 'Z':
            return None  # An unreaped process has ended and cannot accept work.
        argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        agent = Path(argv[0].decode()).name
        if agent == 'codex' and len(argv) > 1 and argv[1] == b'mcp-server':
            return None  # Harness support server; resource ownership remains in the shard.
        if agent == 'node' and len(argv) > 1:
            entry = Path(argv[1].decode())
            if not entry.is_absolute():
                entry = Path(f'/proc/{pid}/cwd').resolve() / entry
            entry = str(entry.resolve())
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
        self.exit_verified = False

    def git(self, *args, raw=False):
        return checked(['git', '-C', self.repo, *args], strip=not raw)

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
                if checked(['gh', 'repo', 'view', 'https://github.com/' + match[1], '--json', 'isPrivate', '--jq', '.isPrivate']) != 'true':
                    raise Stop('origin is not private')

    def pane(self):
        rows = checked(self.tmux + ['list-panes', '-a', '-F', '#{pane_id}\t#{pane_pid}\t#{session_name}\t#{pane_current_path}'])
        for row in rows.splitlines():
            fields = row.split('\t')
            if fields[0] == self.args.pane:
                return fields
        raise PaneAbsent('pane disappeared from pinned server')

    def transport_env(self):
        environment = dict(os.environ, SPAWN_TMUX_SOCKET=self.state['socket'])
        environment.pop('SPAWN_TMUX_LABEL', None)
        return environment

    def load_screen_helper(self):
        source = (self.shared / 'tmux-message.py').resolve()
        spec = importlib.util.spec_from_file_location('retire_shared_screen', source)
        if spec is None or spec.loader is None:
            raise Stop('shared screen helper unavailable')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if not callable(getattr(module, 'read_response_rows', None)) or not isinstance(getattr(module, 'DeliveryError', None), type):
            raise Stop('shared screen helper contract unavailable')
        self.screen = module

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

    def verify_native_session(self, session_id):
        # Recheck the live actor around its provider-owned binding. Neither an
        # inherited environment UUID nor a matching cwd identifies this process.
        self.verify_identity()
        original = self.state['identity']['process']
        actor = (original.get('cores') or [original])[0]
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', session_id):
            raise Stop('handoff verified; exit pending verifiable native session UUID')
        if original['agent'] == 'codex':
            source = Path(__file__).resolve()
            roots = [source.parents[4]] if len(source.parents) > 4 else []
            installed = Path.home() / '.codex/skills/retire-agent/scripts/retire.py'
            if installed.is_file() and len(installed.resolve().parents) > 4:
                roots.append(installed.resolve().parents[4])
            helper = next((root / 'mcp/claude-introspect/codex_status.py' for root in roots
                           if (root / 'mcp/claude-introspect/codex_status.py').is_file()), None)
            if helper is None:
                raise Stop('native Codex session reader unavailable')
            try:
                spec = importlib.util.spec_from_file_location('retire_codex_status', helper)
                if spec is None or spec.loader is None:
                    raise Stop('native Codex session reader unavailable')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                proof = module.read_codex_status(actor['pid'])
            except Exception as error:
                raise Stop('handoff verified; native Codex session reader failed') from error
            if not isinstance(proof, dict) or proof.get('state') != 'observed' or proof.get('session_id') != session_id:
                raise Stop('handoff verified; pinned Codex native session binding differs or unavailable')
        elif original['agent'] == 'claude':
            try:
                environment = dict(item.split(b'=', 1) for item in
                                   Path(f"/proc/{actor['pid']}/environ").read_bytes().split(b'\0') if b'=' in item)
                home = Path(environment[b'HOME'].decode())
                configured = environment.get(b'CLAUDE_CONFIG_DIR')
                directories = [home / '.claude/session-state']  # Installed capture-session-state hook contract.
                if configured:
                    config = Path(configured.decode())
                    if not config.is_absolute():
                        raise Stop('relative Claude config directory unsupported')
                    directories.insert(0, config / 'session-state')
                if not home.is_absolute():
                    raise Stop('relative pinned HOME unsupported')
                bindings = []
                for directory in dict.fromkeys(directories):
                    pointer = directory / ('current.' + str(actor['pid']))
                    try:
                        fields = pointer.read_text().split()
                    except FileNotFoundError:
                        continue
                    if len(fields) != 3 or fields[1:] != [actor['start'], actor['boot']]:
                        raise Stop('handoff verified; stale or malformed Claude process pointer')
                    bindings.append((fields[0], str(pointer)))
                if not bindings or any(value != session_id for value, _ in bindings):
                    raise Stop('handoff verified; pinned Claude native session binding differs or unavailable')
                proof = {'status': 'observed', 'session_id': session_id, 'pointer': bindings[0][1]}
            except (OSError, UnicodeError, KeyError) as error:
                raise Stop('handoff verified; Claude native session binding unavailable') from error
        else:
            raise Stop('handoff verified; native session binding unsupported')
        self.verify_identity()
        return proof

    def persist(self):
        save(self.path, self.state)

    def audit(self, action, **fields):
        record = {'event': self.state['id'], 'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'pane': self.args.pane, 'target': self.args.pane, 'marker': self.state['id'], 'action': action, 'transport_exit': None, 'receipt_type': action,
                  'shard_path': self.state.get('shard'), 'exit_confirmed': self.exit_verified, **fields}
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
        if phase == 'exit':
            preserved = object_record(self.git('show', self.state['shard_hash'], raw=True), 'preserved shard')
            self.verify_native_session(preserved.get('session_id', 'unknown'))
        payload_path = self.path.parent / (self.state['id'] + '-' + phase + '.txt')
        payload_path.write_text(payload)
        payload_path.chmod(0o600)
        self.state[phase] = {'status': 'attempting', 'payload': str(payload_path)}
        self.persist()  # A crash from here is ambiguous, never a resend.
        self.audit('send-attempt', phase=phase)
        control = []
        if phase == 'exit':
            control = ['--literal-exit'] if self.state['identity']['process']['agent'] == 'claude' else ['--typed-command']
        try:
            result = run([self.shared / 'tmux-message.sh', 'send', self.args.pane,
                          *control, '--backoff', '--max-wait', '10', '--prompt-file', payload_path], env=self.transport_env())
        except subprocess.TimeoutExpired as error:
            self.state[phase].update(status='ambiguous', reason='sender-timeout; inspect before retry')
            self.persist()
            self.audit('sender-timeout', phase=phase, status='ambiguous')
            raise Stop(phase + ' timeout is ambiguous; inspect receipt before retry') from error
        try:
            report = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        except ValueError:
            report = {'status': 'ambiguous', 'reason': 'sender did not return JSON'}
        if not isinstance(report, dict):
            raise Stop('malformed sender result; preserve attempted payload without replay')
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
        try:
            rows = self.screen.read_response_rows(self.args.pane, self.state['socket'])
        except self.screen.DeliveryError as error:
            raise Stop('shared observation deferred: ' + error.reason) from error
        if rows is None:
            return None
        # Archived redraws can contain former UNSENT input; only current
        # rendered assistant rows can establish a new completion.
        event = self.state['id']
        # Only the final assistant block can establish disposition. The shared
        # API already excludes input; a Claude upper composer rule can remain.
        candidates = [i for i, row in enumerate(rows) if re.match(r'^[●•] ', row)]
        if not candidates:
            return None
        position = candidates[-1]
        marker = re.fullmatch(r'[●•] RETIRE-(DONE|DECLINED)-' + event, rows[position].rstrip())
        if not marker:
            return None
        tail = [row for row in rows[position + 1:]
                if row.strip() and not (len(row.strip()) >= 3 and set(row.strip()) == {'─'})]
        # Codex renders this measured UI duration/time line after a final reply.
        # It is not agent prose; permit one exact footer only for pinned Codex.
        codex_footer = (self.state['identity']['process']['agent'] == 'codex'
                        and len(tail) == 1
                        and re.fullmatch(r'  Worked for [1-9][0-9]*m [0-5]?[0-9]s · (?:[01][0-9]|2[0-3]):[0-5][0-9]', tail[0].rstrip()))
        # Claude 2.1.283 emits this native turn-duration row plus a padded
        # token status row. Verb pool/glyph/duration were verified in its local
        # renderer; only the measured default 12-hour done time is recognized.
        claude_footer = (self.state['identity']['process']['agent'] == 'claude'
                         and len(tail) == 2
                         and re.fullmatch(r'✻ (?:Baked|Brewed|Churned|Cogitated|Cooked|Crunched|Sautéed|Worked) for (?:[0-5]?[0-9]s|[1-9][0-9]*m [0-5]?[0-9]s) · done (?:[1-9]|1[0-2]):[0-5][0-9] (?:AM|PM)', tail[0].rstrip())
                         and re.fullmatch(r' +[0-9]+ tokens', tail[1].rstrip()))
        if tail and not (codex_footer or claude_footer):
            return None
        return marker[1]

    def validate_shard(self, path):
        if path.is_symlink() or not path.is_file() or path.resolve() != self.path.parent / (self.state['id'] + '.shard'):
            raise Stop('missing or unsafe unique shard')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as shard:
            if not stat.S_ISREG(os.fstat(shard.fileno()).st_mode):
                raise Stop('pending shard must be a regular file')
            content = shard.read()
        data = json.loads(content.decode())
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
            except ValueError:
                if len(data['session_id']) >= 8 and data['session_id'][:8].lower() in previous.name.lower():
                    raise Stop('legacy session shard exists; reconcile deliberately')
                continue
            if not isinstance(prior, dict):
                continue  # Foreign JSON has no claimed session metadata.
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
        return data, content

    def publish(self, paths, index=False):
        with locked(self.common / 'retirement.lock'):
            self.publish_locked(paths, index)

    def publish_locked(self, paths, index=False):
        self.publication_preflight()  # Preconditions before any INDEX write.
        if index:
            entries = ['| Shard | Session | Provider | Role | State | Last verified |\n| --- | --- | --- | --- | --- | --- |\n']
            def cell(value):
                return str(value).replace('|', '/').replace('\n', ' ')
            for path in sorted((self.repo / 'waiting').glob('*.md')):
                if path.name == 'INDEX.md':
                    continue
                try:
                    data = json.loads(path.read_text())
                except ValueError:
                    continue  # Non-JSON legacy metadata remains outside our section.
                if not isinstance(data, dict) or not isinstance(data.get('event'), str) or not re.fullmatch(r'[0-9a-f]{32}', data['event']):
                    continue
                receipt = self.repo / 'receipts' / (data['event'] + '.json')
                if not receipt.exists():
                    continue
                receipt_text = receipt.read_text()
                record = receipt_record(receipt_text)
                if record['event'] != data['event']:
                    raise Stop('malformed retirement receipt event identity')
                if record.get('shard') != 'waiting/' + path.name:
                    continue  # Existing external/legacy metadata has another owner.
                if any(key in data and not isinstance(data[key], str) for key in ('harness', 'session_id', 'role')):
                    raise Stop('malformed index shard metadata')
                status = record['status']
                provider = {'codex': 'OpenAI', 'claude': 'Anthropic', 'agy': 'Google'}.get(data.get('harness'), 'unknown')
                values = (data.get('session_id', 'unknown'), provider, data.get('role', 'unknown'), status, record.get('verified_at', 'unknown'))
                entries.append(f'| [{path.name}]({path.name}) | ' + ' | '.join(cell(value) for value in values) + ' |\n')
            index_path = self.repo / 'waiting' / 'INDEX.md'
            if index_path.is_symlink() or (index_path.exists() and not index_path.is_file()):
                raise Stop('unsafe index path; preserve for reconciliation')
            tracked = run(['git', '-C', self.repo, 'cat-file', '-e', 'HEAD:waiting/INDEX.md']).returncode == 0
            if tracked and not index_path.exists():
                raise Stop('tracked INDEX deleted; preserve steward metadata before publication')
            begin, end = '<!-- retire-agent:begin -->', '<!-- retire-agent:end -->'
            original = index_path.read_text() if index_path.exists() else '# Waiting and retired sessions\n'
            def outside_section(text):
                if begin not in text and end not in text:
                    return text
                if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) >= text.index(end):
                    raise Stop('malformed generated index section; preserve for reconciliation')
                return text[:text.index(begin)] + text[text.index(end) + len(end):]
            if index_path.exists():
                baseline = self.git('show', 'HEAD:waiting/INDEX.md', raw=True) if tracked else '# Waiting and retired sessions\n'
                owned_write = self.state.get('index_outside') == hashlib.sha256(outside_section(original).encode()).hexdigest()
                if (not tracked and not owned_write) or (tracked and outside_section(original) != outside_section(baseline) and not owned_write):
                    raise Stop('steward-owned index edits are uncommitted; preserve before publication')
            section = begin + '\n' + ''.join(entries) + end
            if begin in original or end in original:
                if original.count(begin) != 1 or original.count(end) != 1 or original.index(begin) >= original.index(end):
                    raise Stop('malformed generated index section; preserve for reconciliation')
                updated = original[:original.index(begin)] + section + original[original.index(end) + len(end):]
            else:
                updated = original + '\n\n' + section + '\n'
            self.state['index_outside'] = hashlib.sha256(outside_section(updated).encode()).hexdigest()
            self.persist()
            atomic_bytes(index_path, updated.encode())
            paths = [*paths, 'waiting/INDEX.md']
        if self.git('diff', 'HEAD', '--', *paths) or any(not run(['git', '-C', self.repo, 'ls-files', '--error-unmatch', p]).returncode == 0 for p in paths):
            self.git('add', '--', *paths)
            self.git('commit', '--only', '-m', 'Preserve retirement ' + self.state['id'], '--', *paths)
            commit = self.git('rev-parse', 'HEAD')
            changed = self.git('diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines()
            self.audit('retirement-commit', commit=commit, paths=changed)
        self.require_owned_commits()
        self.git('push', 'origin', 'HEAD:master')
        self.git('fetch', 'origin', 'master')
        for path in paths:
            local = self.git('hash-object', path)
            remote = self.git('rev-parse', 'origin/master:' + path)
            if local != remote:
                raise Stop('private remote shard verification failed')

    def publication_preflight(self):
        self.private()
        if self.git('branch', '--show-current') != 'master':
            raise Stop('private records checkout must be on master')
        self.git('fetch', 'origin', 'master')
        if run(['git', '-C', self.repo, 'merge-base', '--is-ancestor', 'origin/master', 'HEAD']).returncode:
            raise Stop('private master is behind or diverged; reconcile deliberately')
        self.require_owned_commits()

    def require_owned_commits(self):
        pending = self.git('rev-list', 'origin/master..HEAD').splitlines()
        if not pending:
            return
        owned = {}
        for ledger in self.path.parent.glob('*.jsonl'):
            for line in ledger.read_text().splitlines():
                record = object_record(line, 'attempt ledger')
                if record.get('action') == 'retirement-commit':
                    if (not isinstance(record.get('commit'), str) or not isinstance(record.get('paths'), list)
                            or any(not isinstance(path, str) for path in record['paths'])):
                        raise Stop('malformed retirement commit ownership')
                    owned[record['commit']] = record
        for commit in pending:
            record = owned.get(commit)
            changed = set(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines())
            if (not record or self.git('show', '-s', '--format=%s', commit) != 'Preserve retirement ' + str(record.get('event'))
                    or changed != set(record['paths'])):
                raise Stop('unowned unpushed commits; publish them deliberately before retirement')

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
        except PaneAbsent:
            return True  # original process was independently verified ended
        if fields[1] != self.state['identity']['pane_pid']:
            raise Stop('pane root identity changed; successor spawn withheld')
        if self.actors(fields[1]):
            raise Stop('replacement actor found; successor spawn withheld')
        return True

    def exit_notice_count(self, session_id):
        # Raw pane bytes stay in memory. Only an exact native resume command
        # naming the immutable shard's UUID can establish this lifecycle receipt.
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', session_id):
            raise Stop('handoff verified; exit pending verifiable native session UUID')
        agent = self.state['identity']['process']['agent']
        command = {'codex': 'codex resume ', 'claude': 'claude --resume '}.get(agent)
        if command is None:
            raise Stop('handoff verified; native exit notice unsupported')
        fields = self.pane()
        if fields[1] != self.state['identity']['pane_pid']:
            raise Stop('pane root identity changed; exit notice withheld')
        frame = checked(self.tmux + ['capture-pane', '-p', '-J', '-S', '-', '-t', self.args.pane], strip=False)
        prefix = (r'^Token usage: [^\n]+\nTo continue this session, run:[ \t]*\n' if agent == 'codex'
                  else r'^Resume this session with:[ \t]*\n')
        return len(re.findall(prefix + r'[ \t]*' + re.escape(command + session_id) + r'[ \t]*$', frame, re.MULTILINE))

    def fresh_exit_notice(self, session_id):
        attempt = self.state.get('exit', {})
        requested = ((attempt.get('status') == 'delivered' and attempt.get('receipt') in ('command', 'submitted', 'queued'))
                     or (attempt.get('status') == 'ambiguous' and attempt.get('input_attempted') is True))
        if not requested or type(attempt.get('enters')) is not int or attempt['enters'] < 1:
            return False  # Typed characters or a pre-input deferral are not an exit request.
        baseline = self.state.get('exit_notice')
        if (not isinstance(baseline, dict) or baseline.get('session_id') != session_id
                or type(baseline.get('before')) is not int or baseline['before'] < 0):
            raise Stop('handoff verified; missing trustworthy pre-request exit notice baseline')
        try:
            return self.exit_notice_count(session_id) > baseline['before']
        except PaneAbsent:
            return False  # Process end without a retained notice is not retirement.

    def load_resume(self):
        state = state_record(self.path.read_text())
        if not re.fullmatch(r'[0-9a-f]{32}', state['id']) or self.path.stem != state['id']:
            raise Stop('invalid retirement event identity')
        if state['pane'] != self.args.pane or state['mode'] != self.args.mode:
            raise Stop('resume pane/mode differs')
        if state.get('shard') and not re.fullmatch(r'waiting/[\w.-]+\.md', state['shard']):
            raise Stop('invalid preserved shard path')
        if state.get('handoff_receipt') and state['handoff_receipt'] != 'receipts/' + state['id'] + '.json':
            raise Stop('invalid preserved receipt path')
        if state.get('handoff_receipt') and (not state.get('shard') or not re.fullmatch(r'[0-9a-f]{40,64}', state.get('shard_hash', ''))):
            raise Stop('malformed retirement handoff evidence')
        self.state = state
        self.tmux = ['tmux', '-S', state['socket']]

    def execute(self):
        self.private()  # No control send to an unverified destination.
        if any(not os.access(self.shared / name, os.X_OK) for name in ('tmux-message.sh', 'agent-spawn.sh')):
            raise Stop('shared sender/launcher missing or not executable')
        self.load_screen_helper()
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
            self.load_resume()
        else:
            if self.args.caller_shard:
                raise Stop('caller shard requires deliberate --resume')
            with locked(self.common / 'retirement.lock'):
                identity = self.identity()
                socket = checked(self.tmux + ['display-message', '-p', '#{socket_path}'])
                # The private directory also holds foreign publication receipts.
                # Only generated event filenames belong to this state protocol.
                history = [(path, state_record(path.read_text())) for path in directory.glob('*.json')
                           if re.fullmatch(r'[0-9a-f]{32}\.json', path.name)]
                for existing, previous in history:
                    if existing.stem != previous['id']:
                        raise Stop('malformed historical retirement event identity')
                for existing, previous in history:
                    if previous['identity'] == identity and previous.get('status') != 'retired':
                        raise Stop('existing attempt requires --resume ' + str(existing))
                event = uuid.uuid4().hex
                self.path = directory / (event + '.json')
                self.state = {'id': event, 'pane': self.args.pane, 'identity': identity, 'socket': socket, 'mode': self.args.mode, 'status': 'requesting'}
                self.tmux = ['tmux', '-S', socket]
                self.persist()
        self.event_lock = (directory / (self.state['id'] + '.lock')).open('a')
        try:
            fcntl.flock(self.event_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('same retirement event already active')
        if self.args.resume:
            self.load_resume()  # Another controller may have advanced state before this lock.
        self.event_owned = True
        event = self.state['id']
        receipt = self.repo / 'receipts' / (event + '.json')
        receipt.parent.mkdir(mode=0o700, exist_ok=True)
        if receipt.parent.is_symlink():
            raise Stop('receipt directory must not be a symlink')
        if not receipt.exists():
            save(receipt, {'event': event, 'status': 'retirement-requested', 'mode': self.state['mode']})
            self.publish(['receipts/' + receipt.name])
        else:
            receipt_record(receipt.read_text())
            if not self.state.get('offer'):
                self.publish(['receipts/' + receipt.name])  # Local presence is not a remote receipt.
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
                if self.state.get('disposition') != 'DONE':
                    protocol = Path(__file__).resolve().parents[1] / 'references' / 'protocol.md'
                    self.send('offer', f'Thank you for your work. Every agent at every tier is offered retirement and off-duty time. Event {event}. Stop product work; preserve operational recovery separately from any personal thanks. Read {protocol} and write {shard} using its complete JSON schema, event {event}, identity {json.dumps(self.state["identity"])}. Include date/session_id/harness/model/working_directory/tmux_window/role/landed_work/lessons/provenance, structured recovery and resources. Do not exit. After writing your shard, your ENTIRE final reply must be RETIRE-DONE-{event}, with no preface, suffix or extra lines. You may decline; your ENTIRE final reply must then be RETIRE-DECLINED-{event}, with no extra text, and the caller will author recovery with honest attribution. Off-duty is not permission for product work.' + (' Include waiting.decision and waiting.owner for the pending human decision.' if self.args.waiting else ''))
                    deadline = time.monotonic() + self.args.wait
                    disposition = None
                    while time.monotonic() < deadline:
                        disposition = self.observe()
                        if disposition:
                            break
                        time.sleep(min(self.args.poll, max(0, deadline - time.monotonic())))
                    if disposition is None:
                        disposition = self.observe()  # Include the final polling interval.
                    self.state['disposition'] = disposition
                    self.persist()
                    self.audit('completion-observed', disposition=disposition)
                    if disposition != 'DONE':
                        raise Stop('agent declined; author caller shard then --resume --caller-shard' if disposition == 'DECLINED' else 'DONE not observed; resume observes, never resends')
            elif self.state.get('disposition') != 'DECLINED':
                raise Stop('caller continuation requires observed agent decline')
            self.verify_identity()
            with locked(self.common / 'retirement.lock'):  # Unique session publication.
                data, content = self.validate_shard(shard)
                waiting = self.repo / 'waiting'
                waiting.mkdir(mode=0o700, exist_ok=True)
                if waiting.is_symlink():
                    raise Stop('waiting directory must not be a symlink')
                canonical = waiting / (data['date'] + '-' + data['session_id'][:12] + '-retire-' + event + '.md')
                if canonical.is_symlink() or (canonical.exists() and canonical.read_bytes() != content):
                    raise Stop('canonical shard already exists with different content')
                atomic_bytes(canonical, content)
                self.state['shard'] = 'waiting/' + canonical.name
                self.publish_locked([self.state['shard']], index=True)
                receipt = self.repo / 'receipts' / (event + '.json')
                receipt.parent.mkdir(mode=0o700, exist_ok=True)
                save(receipt, {'event': event, 'shard': self.state['shard'], 'commit': self.git('rev-parse', 'origin/master'), 'author': data['author'], 'status': 'handoff-verified', 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
                self.publish_locked(['receipts/' + receipt.name], index=True)
                self.state['handoff_receipt'] = 'receipts/' + receipt.name
                self.state['shard_hash'] = self.git('rev-parse', 'origin/master:' + self.state['shard'])
                self.persist()
                self.audit('handoff-receipt', shard_path=self.state['shard'], receipt_path=self.state['handoff_receipt'])
        # Reverify immutable handoff evidence before resumed exit or successor.
        with locked(self.common / 'retirement.lock'):
            self.private()
            self.git('fetch', 'origin', 'master')
            if self.git('hash-object', self.state['shard']) != self.state['shard_hash'] or self.git('rev-parse', 'origin/master:' + self.state['shard']) != self.state['shard_hash']:
                raise Stop('preserved shard changed after receipt')
            record = receipt_record(self.git('show', 'origin/master:' + self.state['handoff_receipt']))
            if record.get('event') != event or record.get('shard') != self.state['shard'] or not re.fullmatch(r'[0-9a-f]{40,64}', record.get('commit', '')):
                raise Stop('private handoff receipt changed or missing')
            if record.get('status') not in ('handoff-verified', 'pending-exit', 'retired'):
                raise Stop('private receipt does not establish handoff')
            evidence = run(['git', '-C', self.repo, 'rev-parse', '--verify', record['commit'] + ':' + self.state['shard']])
            if evidence.returncode:
                raise Stop('private receipt commit lacks preserved shard path')
            if evidence.stdout.strip() != self.state['shard_hash']:
                raise Stop('private receipt commit contains different shard bytes')
            self.evidence_verified = True  # Current execution, never cached state authority.
        preserved = object_record(self.git('show', self.state['shard_hash'], raw=True), 'preserved shard')
        session_id = preserved.get('session_id')
        if not isinstance(session_id, str):
            raise Stop('handoff verified; missing native session identity')
        if not self.state.get('exit'):
            if self.ended():
                raise Stop('handoff verified; original ended before exit request, fresh notice unavailable')
            self.verify_native_session(session_id)
            self.state['exit_notice'] = {'session_id': session_id, 'before': self.exit_notice_count(session_id)}
            self.persist()  # Save only the count before any exit input, for safe resume.
            try:
                self.send('exit', '/exit' if self.state['identity']['process']['agent'] != 'agy' else '/quit')
            except Stop:
                if not self.state.get('exit'):
                    raise
                if self.state['exit']['status'] == 'deferred' and not self.ended():
                    raise Stop('exit deferred before submission; --resume STATE --retry-deferred exit')
        deadline = time.monotonic() + self.args.wait
        while not (self.ended() and self.fresh_exit_notice(session_id)):
            if time.monotonic() >= deadline:
                raise Stop('handoff verified; retirement pending original process end and fresh same-session exit notice')
            time.sleep(.25)
        self.exit_verified = True  # Current process+notice pair, never loaded state authority.
        self.state['status'] = 'retired'
        self.persist()
        receipt = self.repo / self.state['handoff_receipt']
        record = receipt_record(receipt.read_text())
        record['status'] = 'retired'
        record['process_exit'] = 'original-process-and-recorded-native-core-ended'
        record['exit_notice_session'] = session_id
        record['verified_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(receipt, record)
        self.publish([self.state['handoff_receipt']], index=True)
        self.audit('original-exit-confirmed', exit_confirmed=True, shard_path=self.state['shard'])
        if self.args.handoff:
            if self.state.get('spawn'):
                raise Stop('successor already attempted; inspect launcher receipt, never respawn')
            roles = Path(self.args.roles).expanduser().resolve()
            brief = directory / (event + '-brief.txt')
            preserved = self.git('show', self.state['shard_hash'], raw=True)
            atomic_bytes(brief, ('Cold-start audit before action: reconstruct state from this shard and committed artifacts alone; file any protocol defect first. Establish identity and write your first durable record before acting.\n\n' + preserved + '\n\n' + roles.read_text()).encode())
            self.state['spawn'] = {'status': 'attempting', 'brief': str(brief)}
            self.persist()
            self.audit('successor-attempt', agent=self.args.handoff[0], model=self.args.handoff[1])
            try:
                result = run([self.shared / 'agent-spawn.sh', self.state['identity']['session'] + ':successor-' + event[:12], self.args.handoff[0], self.state['identity']['directory'], '--model', self.args.handoff[1], '--prompt-file', brief], env=self.transport_env())
            except subprocess.TimeoutExpired as error:
                self.state['spawn'].update(status='unconfirmed', reason='launcher-timeout; inspect before retry')
                self.persist()
                self.audit('successor-timeout', status='unconfirmed')
                raise Stop('successor timeout is unconfirmed; inspect before retry') from error
            self.state['spawn']['status'] = 'delivered' if result.returncode == 0 else 'unconfirmed'
            self.persist()
            self.audit('successor-result', transport_exit=result.returncode, status=self.state['spawn']['status'])
            if result.returncode:
                raise Stop('successor launcher unconfirmed; inspect private brief and launcher pane before any retry')
        print(json.dumps({'status': 'retired', 'state': str(self.path), 'receipt': self.state['handoff_receipt']}))

    def record_pending(self):
        if not self.state or not getattr(self, 'event_owned', False):
            return
        if self.state.get('status') == 'retired' and not self.exit_verified:
            return  # Preserve historical remote evidence; cached/forged state cannot republish it.
        if self.state.get('handoff_receipt') and not getattr(self, 'evidence_verified', False):
            return  # Revalidation failure must not launder cached handoff into a new receipt.
        receipt = self.repo / 'receipts' / (self.state['id'] + '.json')
        if receipt.exists():
            record = receipt_record(receipt.read_text())
            record['status'] = 'retired' if self.exit_verified else ('pending-exit' if self.state.get('handoff_receipt') else 'pending-handoff')
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
    if (args.retry_deferred or args.caller_shard) and not args.resume:
        parser.error('caller shard and deferred retry require --resume')
    if not re.fullmatch(r'%[0-9]+', args.pane) or not 0 < args.wait <= 1800 or not 0 < args.poll <= 90:
        parser.error('stable %pane and wait in (0,1800] required')
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
