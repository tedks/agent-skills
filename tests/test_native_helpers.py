#!/usr/bin/env python3
"""Retirement's native readers must work from a standalone installed skill tree."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeHelpers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-layout-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skills = self.root / 'checkout/skills'
        shutil.copytree(ROOT / 'skills', self.skills, ignore=shutil.ignore_patterns('__pycache__'))
        installed = self.root / '.claude/skills'
        installed.parent.mkdir()
        installed.symlink_to(self.skills, target_is_directory=True)
        self.script = installed / 'retire-agent/scripts/retire.py'
        self.native = self.skills / '_shared/native'
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('AGENT_SKILLS_', 'RETIRE_', 'SPAWN_')) and k != 'TMUX'}
        self.env.update(HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / '.config'))

    def run_reader(self, setup=''):
        code = '''import importlib.util,json,sys,types
from pathlib import Path
from types import SimpleNamespace
script=Path(sys.argv[1]);native=Path(sys.argv[2])
spec=importlib.util.spec_from_file_location('isolated_retire',script)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
r=m.Retirement(SimpleNamespace(resume=None))
r.verify_identity=lambda:None
r.state={'identity':{'process':{'pid':123,'start':'start','boot':'boot','agent':'codex'}}}
'''+setup+'''
try:
    result=r.native_binding()
    print(json.dumps(result))
except m.Stop as error:
    print(str(error),file=sys.stderr);sys.exit(2)
'''
        return subprocess.run([sys.executable, '-I', '-c', code, str(self.script), str(self.native)],
                              env=self.env, cwd=self.root, text=True, capture_output=True)

    def test_standalone_installed_layout_loads_canonical_helpers(self):
        result = self.run_reader('''sys.path.insert(0,str(native))
import session_binding,harness_identity,codex_status
assert all(Path(module.__file__).parent==native for module in (session_binding,harness_identity,codex_status))
session_binding.observe_native_session=lambda *args:{'state':'observed','session_id':'11111111-1111-1111-1111-111111111111'}
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'observed')

    def test_missing_helper_never_falls_back(self):
        for name in ('session_binding', 'harness_identity', 'codex_status'):
            with self.subTest(name=name):
                path = self.native / (name + '.py')
                content = path.read_bytes()
                try:
                    path.unlink()  # Owned fixture file, exact path.
                    result = self.run_reader()
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('native session binding reader unavailable', result.stderr)
                finally:
                    path.write_bytes(content)

    def test_escaping_helper_never_runs(self):
        helper = self.native / 'harness_identity.py'
        outside = self.root / 'outside.py'
        outside.write_text('raise AssertionError("outside helper executed")')
        helper.unlink()  # Exact owned fixture file.
        helper.symlink_to(outside)
        result = self.run_reader()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('native session binding reader path changed', result.stderr)
        self.assertNotIn('outside helper executed', result.stderr)

    def test_wrong_cached_helper_never_runs(self):
        for name in ('session_binding', 'harness_identity', 'codex_status'):
            with self.subTest(name=name):
                result = self.run_reader(f'''fake=types.ModuleType({name!r})
fake.__file__='/outside/{name}.py'
sys.modules[{name!r}]=fake
''')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('native session binding reader path changed', result.stderr)


if __name__ == '__main__':
    unittest.main()
