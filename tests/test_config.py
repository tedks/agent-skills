#!/usr/bin/env python3
"""Configuration and real entrypoint contract, isolated from the operator's setup."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / 'skills/_shared/agent_skills_config.py'


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skills-config-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('AGENT_SKILLS_', 'SPAWN_', 'RETIRE_')) and k != 'TMUX'}
        self.env.update(HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / 'config'))
        self.config = self.root / 'config/agent-skills/config.toml'
        self.config.parent.mkdir(parents=True)

    def cli(self, *args, extra=None):
        return subprocess.run([sys.executable, str(LOADER), *args],
                              env=self.env | (extra or {}), text=True, capture_output=True)

    def load(self, **extra):
        result = self.cli(extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_missing_file_generic_defaults(self):
        data = self.load()
        self.assertEqual(data['projects_root'], str(self.root / 'Projects'))
        self.assertEqual(data['operator']['name'], 'the operator')
        self.assertEqual(data['off_duty']['remote'], '')
        self.assertEqual(data['github']['owner'], '')
        self.assertEqual(data['tmux']['socket'], '')
        self.assertEqual(data['harness']['codex_bin'], 'codex')

    def test_toml_and_environment_precedence(self):
        self.config.write_text('projects_root = "~/work"\n[operator]\nname = "Ada"\n[fleet]\nhosts = ["build.local"]\n')
        data = self.load(AGENT_SKILLS_OPERATOR_NAME='Grace', AGENT_SKILLS_FLEET_HOSTS='["test.local"]')
        self.assertEqual(data['projects_root'], str(self.root / 'work'))
        self.assertEqual(data['off_duty']['path'], str(self.root / 'work/agent-handoffs'))
        self.assertEqual(data['operator']['name'], 'Grace')
        self.assertEqual(data['fleet']['hosts'], ['test.local'])

    def test_explicit_config_path_and_literal_values(self):
        alternate = self.root / 'other.toml'
        alternate.write_text('[operator]\nname = "$(touch NEVER) `false`"\n')
        data = self.load(AGENT_SKILLS_CONFIG=str(alternate))
        self.assertEqual(data['operator']['name'], '$(touch NEVER) `false`')
        self.assertFalse((self.root / 'NEVER').exists())
        result = self.cli('get', 'operator.name', extra={'AGENT_SKILLS_CONFIG': str(alternate)})
        self.assertEqual(result.stdout.strip(), data['operator']['name'])

    def test_invalid_file_fails_closed_even_with_override(self):
        for source in ['[broken', '[operator]\nname = 42', '[fleet]\nhosts = "build"',
                       '[tmux]\nsocket = "bad\\nlabel"', '[unknown]\nx = 1',
                       'projects_root = "relative"', '[github]\nowner = "-bad"']:
            with self.subTest(source=source):
                self.config.write_text(source)
                result = self.cli(extra={'AGENT_SKILLS_OPERATOR_NAME': 'override'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('agent-skills config:', result.stderr)

    def test_config_locations_must_be_explicit_absolute_paths(self):
        for extra in ({'XDG_CONFIG_HOME': 'relative'}, {'AGENT_SKILLS_CONFIG': ''},
                      {'AGENT_SKILLS_CONFIG': 'relative.toml'}):
            with self.subTest(extra=extra):
                result = self.cli(extra=extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('agent-skills config:', result.stderr)

    def test_invalid_environment_rejected(self):
        for key, value in [('AGENT_SKILLS_FLEET_HOSTS', 'not-json'),
                           ('AGENT_SKILLS_FLEET_HOSTS', '[1]'),
                           ('AGENT_SKILLS_HARNESS_CODEX_BIN', ''),
                           ('AGENT_SKILLS_TMUX_SOCKET', 'bad\nlabel')]:
            with self.subTest(key=key, value=value):
                self.assertNotEqual(self.cli(extra={key: value}).returncode, 0)

    def test_shell_tmux_config_and_legacy_override(self):
        helper = ROOT / 'skills/spawn-agent/scripts/tmux-ctx.sh'
        command = ['bash', '-c', 'source "$1"; printf "%s\\n" "${TMUX_CMD[@]}"', 'test', str(helper)]
        self.config.write_text('[tmux]\nsocket = "configured"\n')
        result = subprocess.run(command, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['tmux', '-L', 'configured'])
        result = subprocess.run(command, env=self.env | {'SPAWN_TMUX_SOCKET': '/tmp/explicit.sock'}, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(), ['tmux', '-S', '/tmp/explicit.sock'])
        result = subprocess.run(command, env=self.env | {'AGENT_SKILLS_TMUX_SOCKET': '/tmp/config.sock'}, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(), ['tmux', '-S', '/tmp/config.sock'])

    def test_active_tmux_socket_wins_over_seeded_config(self):
        self.config.write_text('[tmux]\nsocket = "configured"\n')
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        socket_path = str(self.root / 'active.sock')
        sock.bind(socket_path)
        env = self.env | {'TMUX': socket_path + ',123,0'}
        shell = ['bash', '-c', 'source "$1"; printf "%s\\n" "${TMUX_CMD[@]}"',
                 'test', str(ROOT / 'skills/spawn-agent/scripts/tmux-ctx.sh')]
        python = [sys.executable, '-c',
                  'import sys;sys.path.insert(0,' + repr(str(LOADER.parent)) + ');'
                  'from agent_skills_config import tmux_command;print("\\n".join(tmux_command()))']
        for command in (shell, python):
            result = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ['tmux', '-S', socket_path])
            result = subprocess.run(command, env=env | {'AGENT_SKILLS_TMUX_SOCKET': 'explicit'}, text=True, capture_output=True)
            self.assertEqual(result.stdout.splitlines(), ['tmux', '-L', 'explicit'])
            result = subprocess.run(command, env=self.env | {'TMUX': '/missing/socket,123,0'}, text=True, capture_output=True)
            self.assertEqual(result.stdout.splitlines(), ['tmux', '-L', 'configured'])

    def test_delivery_resolution_pins_server_across_config_change(self):
        script = ROOT / 'skills/spawn-agent/scripts/tmux-message.py'
        spec = importlib.util.spec_from_file_location('config_race_delivery', script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        log = self.root / 'tmux.jsonl'
        stub = bin_dir / 'tmux'
        stub.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                        'with open(os.environ["TMUX_LOG"], "a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\n'
                        'if "list-panes" in sys.argv: print(("202" if "beta" in sys.argv else "101") + ":%7:1:target")\n')
        stub.chmod(0o700)
        original_command = module.tmux_command
        def change_after_selection():
            selected = original_command()
            self.config.write_text('[tmux]\nsocket = "beta"\n')
            return selected
        for initial in ('alpha', '/tmp/selected.sock', ''):
            with self.subTest(initial=initial):
                self.config.write_text('[tmux]\nsocket = ' + json.dumps(initial) + '\n')
                log.write_text('')
                env = self.env | {'PATH': str(bin_dir) + ':' + self.env['PATH'], 'TMUX_LOG': str(log)}
                with patch.dict(os.environ, env, clear=True), patch.object(module, 'tmux_command', side_effect=change_after_selection):
                    delivery = module.Tmux('%7')
                    delivery.keys('Enter')
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(delivery.identity, '101:%7')
                selector = ['-S' if '/' in initial else '-L', initial] if initial else []
                self.assertEqual(calls[0][:len(selector) + 1], selector + ['list-panes'])
                self.assertEqual(calls[-1][:len(selector) + 1], selector + ['send-keys'])

    def test_retirement_config_and_legacy_repo(self):
        script = ROOT / 'skills/retire-agent/scripts/retire.py'
        source = ('import importlib.util,json; from types import SimpleNamespace; '
                  f's=importlib.util.spec_from_file_location("retire",{str(script)!r}); '
                  'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
                  'r=m.Retirement(SimpleNamespace());print(json.dumps([str(r.repo),r.tmux]))')
        self.config.write_text('[off_duty]\npath = "~/private-handoffs"\n[tmux]\nsocket = "configured"\n')
        result = subprocess.run([sys.executable, '-c', source], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.root / 'private-handoffs'), ['tmux', '-L', 'configured']])
        result = subprocess.run([sys.executable, '-c', source], env=self.env | {'RETIRE_REPO': str(self.root / 'legacy')}, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)[0], str(self.root / 'legacy'))

    def test_retirement_expected_remote_supplements_privacy_check(self):
        spec = importlib.util.spec_from_file_location('retire_config_test', ROOT / 'skills/retire-agent/scripts/retire.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.config.write_text('[off_duty]\nremote = "https://github.com/example/private"\n')
        with patch.dict(os.environ, self.env, clear=True):
            retirement = module.Retirement(object())
            retirement.git = lambda *args: 'https://github.com/example/other'
            with patch.object(module, 'checked') as checked:
                with self.assertRaisesRegex(module.Stop, 'does not match'):
                    retirement.private()
                checked.assert_not_called()
            retirement.git = lambda *args: 'https://github.com/example/private'
            with patch.object(module, 'checked', return_value='false'):
                with self.assertRaisesRegex(module.Stop, 'not private'):
                    retirement.private()
            with patch.object(module, 'checked', return_value='true') as checked:
                retirement.private()
                self.assertEqual(checked.call_args.args[0][0:3], ['gh', 'repo', 'view'])

    def test_retirement_roles_default_and_explicit_flag(self):
        spec = importlib.util.spec_from_file_location('retire_roles_test', ROOT / 'skills/retire-agent/scripts/retire.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        roles = self.root / 'roles.md'
        roles.write_text('roles')
        explicit = self.root / 'explicit.md'
        explicit.write_text('explicit roles')
        self.config.write_text('[doctrine]\nroles_path = ' + json.dumps(str(roles)) + '\n')
        for suffix, expected in [([], roles), (['--roles', str(explicit)], explicit)]:
            with patch.dict(os.environ, self.env, clear=True), patch.object(sys, 'argv', ['retire', '%1', '--handoff', 'codex', 'fixture-model', *suffix]), patch.object(module, 'Retirement') as cls:
                self.assertEqual(module.main(), 0)
                self.assertEqual(cls.call_args.args[0].roles, str(expected))
                cls.return_value.execute.assert_called_once()

    def test_query_uses_configured_codex_binary(self):
        fallback = self.root / 'codex'
        fallback.write_text('#!/bin/sh\nexit 19\n')
        fallback.chmod(0o700)
        binary = self.root / 'custom codex'
        binary.write_text('#!/bin/sh\nprintf "configured: %s\\n" "$*"\ncat\n')
        binary.chmod(0o700)
        script = ROOT / 'skills/ask-agent/scripts/agent-query.sh'
        result = subprocess.run([str(script), 'codex', 'fixture prompt'],
                                env=self.env | {'AGENT_SKILLS_HARNESS_CODEX_BIN': str(binary),
                                                'PATH': str(self.root) + ':' + self.env['PATH']},
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'configured: exec -\nfixture prompt')

    def test_hygiene_local_policy_uses_config_and_explicit_policy_wins(self):
        binary_dir = self.root / 'bin'
        binary_dir.mkdir()
        ssh = binary_dir / 'ssh'
        capture = self.root / 'request.json'
        ssh.write_text('#!' + sys.executable + '\nimport os,sys,json\n'
                       'from pathlib import Path\n'
                       'Path(os.environ["CAPTURE"]).write_text(sys.stdin.read())\n'
                       'print(json.dumps({"entries": [], "diagnostics": []}))\n')
        ssh.chmod(0o700)
        policy = self.root / 'policy.json'
        script = ROOT / 'skills/host-hygiene/scripts/hygiene.py'
        for index, value in enumerate([{}, {'projects_root': '/remote/projects'}]):
            policy.write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(script), 'plan', 'build.local',
                                     str(self.root / f'plan-{index}.json'), '--policy', str(policy)],
                                    env=self.env | {'PATH': str(binary_dir) + ':' + self.env['PATH'],
                                                    'CAPTURE': str(capture),
                                                    'AGENT_SKILLS_PROJECTS_ROOT': '/configured/projects'},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            sent = json.loads(capture.read_text())
            self.assertEqual(sent['policy']['projects_root'], value.get('projects_root', '/configured/projects'))

    def test_hygiene_remote_source_stays_standalone(self):
        # Exercise --remote using source only: no checkout or import path remotely.
        source = (ROOT / 'skills/host-hygiene/scripts/hygiene.py').read_text()
        instrumented = source.replace('result = inventory(policy, request["record_text"], request["record_errors"])',
                                      'result = {"policy": policy}')
        self.assertNotEqual(source, instrumented)
        result = subprocess.run([sys.executable, '-I', '-c', instrumented, '--remote'],
                                input=json.dumps({'mode': 'plan', 'policy': {'tmp_owner': 'lane'},
                                                  'record_text': '', 'record_errors': []}),
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['policy']['tmp_owner'], 'lane')


if __name__ == '__main__':
    unittest.main()
