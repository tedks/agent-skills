#!/usr/bin/env python3
"""Behavior tests with a virtual clock and a simulated terminal, no model calls."""
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout, nullcontext
from unittest.mock import patch
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('delivery', Path(__file__).with_name('tmux-message.py'))
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def screen(text='', history='', harness='codex', footer='  Any model · branch', prefix=''):
    if harness == 'claude':
        raw = prefix + '──────────────\n❯ ' + text + '\n──────────────\n'
        y = len(prefix.splitlines()) + 1 + text.count('\n')
    else:
        raw = prefix + '› ' + text + '\n\n' + footer + '\n'
        y = len(prefix.splitlines()) + text.count('\n')
    return m.Frame(raw, 2 + len(text.rsplit('\n', 1)[-1]), y, history + raw)


class Terminal:
    def __init__(self, payload='hello', dropped=0, collapsed=False, queued=False, delayed=0, draft='', mutate=False):
        self.payload, self.dropped, self.collapsed, self.queued = payload, dropped, collapsed, queued
        self.delayed, self.draft, self.mutate = delayed, draft, mutate
        self.now = 0
        self.pastes = 0
        self.enters = 0
        self.accepted_at = None
        self.history = ''
        self.actions = []
        self.pane = '%1'
        self.frames = 0
        self.submitted = None
        self.harness = 'codex'
        self.home = False
        self.end_moves = True
        self.end_paint_delay = 0
        self.end_requested_at = None
        self.end_unknown_frames = 0

    def sleep(self, n): self.now += n
    def clock(self): return self.now

    def frame(self):
        if self.end_requested_at is not None and self.end_unknown_frames:
            self.end_unknown_frames -= 1
            self.frames += 1
            return m.Frame("", -1, -1)
        if self.end_moves and self.end_requested_at is not None and self.now >= self.end_requested_at + self.end_paint_delay:
            self.home = False
        self.frames += 1
        history = self.history
        if self.accepted_at is not None and self.now >= self.accepted_at + self.delayed:
            history += ('• Messages to be submitted after next tool call\n  ↳ ' if self.queued else '› ') + (self.submitted or self.payload).replace('\n', '\n  ') + '\n\n'
        text = self.draft
        if self.mutate and self.pastes:
            text += ' human'
        frame = screen(text, history, harness=self.harness, prefix='• output ' + str(self.frames) + '\n\n')
        if self.home:
            frame.x = 2
        return frame

    def paste(self, path):
        self.actions.append('paste')
        self.paste_at = self.now
        self.pastes += 1
        content = path.read_text()
        self.draft += (('\n' if self.draft and content.startswith('\n') else '') + '[Pasted text #1 +20 lines]') if self.collapsed else content

    def type_command(self, text):
        self.actions.append("typed:" + text)
        self.draft += text

    def keys(self, *keys):
        self.actions.extend(keys)
        if keys == ('End',) and self.end_moves:
            self.end_requested_at = self.now
            if not self.end_paint_delay:
                self.home = False
        if keys == ('Enter',):
            self.enters += 1
            if self.enters > self.dropped:
                self.accepted_at = self.now
                self.submitted = self.draft
                self.draft = ''


class ClaudeExitTerminal(Terminal):
    session = '11111111-2222-3333-4444-555555555555'

    def __init__(self, **kw):
        super().__init__(payload='/exit', **kw)
        self.typed = 0

    def frame(self):
        self.frames += 1
        if self.accepted_at is not None and self.now >= self.accepted_at + self.delayed:
            history = self.history + 'Resume this session with:\nclaude --resume ' + self.session + '\nuser@host:~$ \n'
            return m.Frame(history, 14, len(history.splitlines()) - 1, history)
        text = self.draft + (' human' if self.mutate and self.typed else '')
        return screen(text, self.history, harness='claude')

    def keys(self, *keys):
        if keys == ('-l', '/exit'):
            self.typed += 1
            self.draft += '/exit'
            self.actions.extend(keys)
        else:
            super().keys(*keys)


def deliver(t, **kw):
    o = dict(max_wait=8, stable_wait=3, poll_interval=.25, backoff=False,
             submit_delay=1, render_wait=3, receipt_wait=8, retry_wait=3)
    o.update(kw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'message'
        path.write_text(t.payload)
        return m.Sender(t, SimpleNamespace(**o), t.payload, path, t.clock, t.sleep).deliver()


class DeliveryTests(unittest.TestCase):
    def test_literal_and_typed_modes_conflict_before_input(self):
        t = ClaudeExitTerminal()
        with self.assertRaisesRegex(m.DeliveryError, 'conflicting-input-modes'):
            deliver(t, literal_exit=True, typed_command=True)
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 0, 0))

    def test_response_rows_exclude_live_composer_and_archive(self):
        for harness in ('codex', 'claude'):
            f = screen('● RETIRE-DONE-in-draft', history='● archived marker\n',
                       prefix='● visible response\n\n', harness=harness)
            with patch.object(m, 'Tmux') as t:
                t.return_value.frame.return_value = f
                self.assertEqual(m.read_response_rows('%1', '/tmp/test-socket'),
                                 ['● visible response', ''] if harness == 'codex' else ['● visible response', '', '──────────────'])
                t.assert_called_once_with('%1', socket='/tmp/test-socket')
                t.return_value.keys.assert_not_called()
                t.return_value.paste.assert_not_called()

    def test_response_rows_unknown_modal_and_failed_read(self):
        for f in (m.Frame('', -1, -1), screen('Allow once')):
            with patch.object(m, 'Tmux') as t:
                t.return_value.frame.return_value = f
                self.assertIsNone(m.read_response_rows('%1', '/tmp/test-socket'))
        with patch.object(m, 'Tmux', side_effect=OSError('private detail')):
            with self.assertRaisesRegex(m.DeliveryError, 'observation-read-failed'):
                m.read_response_rows('%1', '/tmp/test-socket')
        for error in (TypeError, IndexError, KeyError):
            with self.subTest(error=error), patch.object(m, 'Tmux'), patch.object(m, 'composer', side_effect=error('private frame detail')):
                with self.assertRaisesRegex(m.DeliveryError, 'observation-read-failed'):
                    m.read_response_rows('%1', '/tmp/test-socket')
        with self.assertRaisesRegex(m.DeliveryError, 'invalid-observation-target'):
            m.read_response_rows('session:window', 'relative')

    def test_observation_socket_is_explicit_without_global_env_change(self):
        import os
        before = dict(os.environ)
        with patch.dict(os.environ, {'SPAWN_TMUX_LABEL': 'stale-server'}), patch.object(m.subprocess, 'run') as run:
            run.return_value.stdout = '123:%1'
            t = m.Tmux('%1', socket='/tmp/pinned-socket')
            self.assertEqual(t.base, ['tmux', '-S', '/tmp/pinned-socket'])
            self.assertEqual(run.call_args.kwargs['env']['SPAWN_TMUX_SOCKET'], '/tmp/pinned-socket')
            self.assertNotIn('SPAWN_TMUX_LABEL', run.call_args.kwargs['env'])
        self.assertEqual(dict(os.environ), before)

    def test_literal_claude_exit_fresh_resume_receipt(self):
        t = ClaudeExitTerminal(delayed=2)
        t.history = 'Resume this session with:\nclaude --resume ' + t.session + '\n'
        result = deliver(t, literal_exit=True)
        self.assertEqual(result['receipt'], 'command')
        self.assertEqual(result['resume_session_id'], t.session)
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 1, 1))

    def test_literal_exit_canonical_idle_placeholder(self):
        class Ghost(ClaudeExitTerminal):
            def frame(self):
                if not self.typed:
                    f = screen('Try "summarize this work"', harness='claude')
                    f.x = 2
                    return f
                return super().frame()
        t = Ghost()
        self.assertEqual(deliver(t, literal_exit=True)['receipt'], 'command')
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 1, 1))
        self.assertNotIn('Space', t.actions)

    def test_literal_exit_suggestion_shaped_actual_draft(self):
        t = ClaudeExitTerminal(draft='Try "my draft"')
        with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 0, 0))

    def test_literal_exit_noncanonical_suggestion_stays_protected(self):
        t = ClaudeExitTerminal()
        f = screen('Try unquoted draft', harness='claude')
        f.x = 2
        t.frame = lambda: f
        with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 0, 0))

    def test_literal_exit_swallowed_enter_never_retries(self):
        t = ClaudeExitTerminal(dropped=99)
        t.history = 'Resume this session with:\nclaude --resume ' + t.session + '\n'
        with self.assertRaisesRegex(m.DeliveryError, 'receipt-timeout'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 1, 1))

    def test_literal_exit_old_resume_in_shell_cannot_confirm(self):
        class NoNewNotice(ClaudeExitTerminal):
            def frame(self):
                if self.accepted_at is not None:
                    history = self.history + 'user@host:~$ \n'
                    return m.Frame(history, 14, len(history.splitlines()) - 1, history)
                return super().frame()
        t = NoNewNotice()
        t.history = 'Resume this session with:\nclaude --resume ' + t.session + '\n'
        with self.assertRaisesRegex(m.DeliveryError, 'receipt-timeout'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 1, 1))

    def test_literal_exit_protects_draft_and_mixed_input(self):
        t = ClaudeExitTerminal(draft='human draft')
        with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.typed, t.enters), (0, 0))
        t = ClaudeExitTerminal(mutate=True)
        with self.assertRaisesRegex(m.DeliveryError, 'mixed-input'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.typed, t.enters), (1, 0))

    def test_literal_exit_rejects_other_harness_and_payload(self):
        t = Terminal(payload='/exit')
        with self.assertRaisesRegex(m.DeliveryError, 'requires-claude-exit'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.pastes, t.enters), (0, 0))
        t = ClaudeExitTerminal()
        t.payload = 'ordinary task'
        with self.assertRaisesRegex(m.DeliveryError, 'requires-claude-exit'):
            deliver(t, literal_exit=True)
        self.assertEqual((t.pastes, t.typed, t.enters), (0, 0, 0))
    def test_typed_commands_never_paste(self):
        for command in ('/compact', '/exit', '/status', '/model example'):
            t = Terminal(payload=command)
            self.assertEqual(deliver(t, typed_command=True)['status'], 'delivered')
            self.assertEqual(t.pastes, 0)
            self.assertEqual(t.actions, ['typed:' + command, 'Enter'])

    def test_typed_command_rejects_multiline_and_noncommands(self):
        for command in (' /exit', '/compact\n', 'hello', '/x\targument', '/x\n/exit'):
            t = Terminal(payload=command)
            with self.assertRaisesRegex(m.DeliveryError, 'unsupported-typed-command'):
                deliver(t, typed_command=True)
            self.assertEqual(t.actions, [])

    def test_typed_command_rejects_unrenderable_tail_and_length(self):
        for command in ('/model x ', '/model x;', '/model x\u3000', '/model x\u2003', '/model ' + 'x' * 194):
            t = Terminal(payload=command)
            with self.assertRaisesRegex(m.DeliveryError, 'unsupported-typed-command'):
                deliver(t, typed_command=True)
            self.assertEqual(t.actions, [])

    def test_typed_validation_failure_reports_no_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / 'pending'
            pending.mkdir()
            err = io.StringIO()
            with patch.object(m, 'options', return_value=m.options(['%1', '--typed-command', '/x '])), patch.object(m, 'Tmux', return_value=SimpleNamespace(identity='0:%1', pane='%1')), patch.object(m, 'pane_lock', return_value=nullcontext()), patch.object(m.Sender, 'deliver', return_value={'status': 'delivered'}), redirect_stdout(io.StringIO()), patch.object(m.tempfile, 'mkdtemp', return_value=str(pending)), redirect_stderr(err):
                self.assertEqual(m.main(), 2)
            result = json.loads(err.getvalue())
            self.assertEqual(result['input_method'], 'typed')
            self.assertFalse(result['input_attempted'])
            self.assertFalse(result['paste_attempted'])
            self.assertEqual(result['status'], 'deferred')

    def test_typed_command_preserves_draft_and_modal(self):
        for modal in (False, True):
            t = Terminal(payload='/exit', draft='human draft')
            if modal:
                t.frame = lambda: m.Frame('Would you like to proceed?\n› yes\n', 2, 1)
            with self.assertRaises(m.DeliveryError):
                deliver(t, typed_command=True)
            self.assertEqual(t.actions, [])

    def test_typed_command_rejects_mixed_or_collapsed_input(self):
        for content in ('/exit human', '[Pasted text #1 +2 lines]'):
            t = Terminal(payload='/exit')
            def type_command(text):
                t.actions.append('typed:' + text)
                t.draft = content
            t.type_command = type_command
            with self.assertRaises(m.DeliveryError):
                deliver(t, typed_command=True)
            self.assertEqual(t.enters, 0)

    def test_literal_tmux_command_argv(self):
        t = object.__new__(m.Tmux)
        t.pane = '%9'
        calls = []
        t.run = lambda *args: calls.append(args)
        t.type_command('/compact')
        self.assertEqual(calls, [('send-keys', '-t', '%9', '-l', '--', '/compact')])
        self.assertTrue(m.options(['%1', '--typed-command', '/exit']).typed_command)

    def test_blocks_until_delayed_receipt_without_extra_enter(self):
        t = Terminal(delayed=5)
        self.assertEqual(deliver(t)['status'], 'delivered')
        self.assertGreaterEqual(t.now, t.accepted_at + 5)
        self.assertEqual((t.pastes, t.enters), (1, 1))

    def test_swallowed_enter_recovers_once(self):
        t = Terminal(dropped=1)
        self.assertEqual(deliver(t)['enters'], 2)
        self.assertEqual(t.pastes, 1)

    def test_collapsed_paste_recovers_and_confirms_queue(self):
        t = Terminal(payload='long\nactual message', dropped=1, collapsed=True, queued=True)
        self.assertEqual(deliver(t)['receipt'], 'queued')
        self.assertEqual((t.pastes, t.enters), (1, 2))

    def test_retry_bounded(self):
        t = Terminal(dropped=99)
        with self.assertRaisesRegex(m.DeliveryError, 'receipt-timeout'):
            deliver(t)
        self.assertEqual((t.pastes, t.enters), (1, 2))

    def test_old_receipt_cannot_confirm(self):
        t = Terminal(dropped=99)
        t.history = '› hello\n\n'
        with self.assertRaisesRegex(m.DeliveryError, 'receipt-timeout'):
            deliver(t)

    def test_new_identical_receipt_counts(self):
        t = Terminal()
        t.history = '› hello\n\n'
        self.assertEqual(deliver(t)['status'], 'delivered')

    def test_mixed_input_stops_before_enter(self):
        t = Terminal(mutate=True)
        with self.assertRaisesRegex(m.DeliveryError, 'mixed-input'):
            deliver(t)
        self.assertEqual(t.enters, 0)

    def test_stable_draft_cosubmitted_codex_and_claude(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                t = Terminal(draft='stale TICK')
                t.harness = harness
                original = t.frame
                fixtures = Path(__file__).with_name('fixtures') / 'claude-send'
                def initial():
                    if not t.pastes:
                        x, y = map(int, (fixtures / (harness + '-stable-draft.cursor')).read_text().split(':'))
                        return m.Frame((fixtures / (harness + '-stable-draft.ansi')).read_text(), x, y)
                    return original()
                t.frame = initial
                try:
                    result = deliver(t)
                except m.DeliveryError as error:
                    self.fail("stable draft deferred: " + error.reason)
                self.assertEqual(t.submitted, 'stale TICK\nhello')
                self.assertEqual((t.pastes, t.enters), (1, 1))
                self.assertEqual(result['stale_draft_length'], len('stale TICK'))
                self.assertNotIn('stale TICK', json.dumps(result))

    def test_stable_home_draft_navigates_then_appends_both_harnesses(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                t = Terminal(draft='stale TICK')
                t.home, t.harness = True, harness
                try:
                    result = deliver(t)
                except m.DeliveryError as error:
                    self.fail('Home draft deferred: ' + error.reason)
                self.assertEqual(result['status'], 'delivered')
                self.assertEqual(t.actions, ['End', 'paste', 'Enter'])
                self.assertEqual(t.submitted, 'stale TICK\nhello')

    def test_home_end_delayed_repaint_waits_without_more_keys(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                t = Terminal(draft='stale TICK')
                t.home, t.harness, t.end_paint_delay = True, harness, .5
                try:
                    result = deliver(t, stable_wait=0, max_wait=1)
                except m.DeliveryError as error:
                    self.fail('unchanged Home repaint deferred: ' + error.reason)
                self.assertEqual(result['status'], 'delivered')
                self.assertEqual(t.actions, ['End', 'paste', 'Enter'])
                self.assertGreaterEqual(t.paste_at, t.end_requested_at + .5)
                self.assertEqual(t.submitted, 'stale TICK\nhello')

    def test_home_end_unknown_repaint_then_known_end(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                t = Terminal(draft='stale TICK')
                t.home, t.harness, t.end_unknown_frames = True, harness, 2
                try:
                    result = deliver(t, stable_wait=0, max_wait=1)
                except m.DeliveryError as error:
                    self.fail('unknown repaint deferred before budget: ' + error.reason)
                self.assertEqual(result['status'], 'delivered')
                self.assertEqual(t.actions, ['End', 'paste', 'Enter'])
                self.assertEqual(t.submitted, 'stale TICK\nhello')

    def test_home_end_persistent_unknown_times_out_without_input(self):
        t = Terminal(draft='stale TICK')
        t.home, t.end_unknown_frames = True, 99
        o = SimpleNamespace(max_wait=1, stable_wait=0, poll_interval=.25, backoff=False)
        sender = m.Sender(t, o, t.payload, Path('/unused'), t.clock, t.sleep)
        with self.assertRaisesRegex(m.DeliveryError, 'cursor-navigation-unverified'):
            sender.deliver()
        self.assertEqual(t.now, 1)
        self.assertFalse(sender.pasted)
        self.assertEqual(t.actions, ['End'])

    def test_home_end_known_changed_frames_fail_without_wait_or_input(self):
        for kind in ('text', 'harness', 'modal', 'middle', 'empty'):
            with self.subTest(kind=kind):
                t = Terminal(draft='stale TICK')
                t.home = True
                original = t.frame
                def changed_frame():
                    if t.end_requested_at is not None:
                        if kind == 'modal':
                            return m.Frame('Would you like to proceed?\n› yes\n', 2, 1)
                        if kind == 'text':
                            t.draft = 'human changed text'
                        if kind == 'harness':
                            t.harness = 'claude'
                        if kind == 'empty':
                            t.draft = ''
                    frame = original()
                    if t.end_requested_at is not None and kind == 'middle':
                        frame.x = 4
                    return frame
                t.frame = changed_frame
                o = SimpleNamespace(max_wait=1, stable_wait=0, poll_interval=.25, backoff=False)
                sender = m.Sender(t, o, t.payload, Path('/unused'), t.clock, t.sleep)
                with self.assertRaisesRegex(m.DeliveryError, 'cursor-navigation-unverified'):
                    sender.deliver()
                self.assertEqual(t.now, t.end_requested_at)
                self.assertFalse(sender.pasted)
                self.assertEqual(t.actions, ['End'])

    def test_home_end_not_moving_or_text_changes_defers_without_paste(self):
        for harness in ('codex', 'claude'):
            for change in (False, True):
                with self.subTest(harness=harness, change=change):
                    t = Terminal(draft='stale TICK')
                    t.home, t.harness, t.end_moves = True, harness, change
                    original = t.keys
                    def keys(*keys):
                        original(*keys)
                        if change and keys == ('End',):
                            t.draft = 'human changed text'
                    t.keys = keys
                    with self.assertRaisesRegex(m.DeliveryError, 'cursor-navigation-unverified'):
                        deliver(t)
                    self.assertEqual(t.actions, ['End'])
                    self.assertEqual((t.pastes, t.enters), (0, 0))

    def test_stable_draft_requires_cursor_at_end(self):
        t = Terminal(draft='stale TICK')
        original = t.frame
        def midtext():
            frame = original()
            frame.x = 4
            return frame
        t.frame = midtext
        with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
            deliver(t)
        self.assertEqual(t.actions, [])

    def test_zero_window_still_observes_twice(self):
        t = Terminal(draft='stale TICK')
        original = t.frame
        def second():
            if t.frames == 1:
                t.draft = 'human changed this'
            return original()
        t.frame = second
        with self.assertRaisesRegex(m.DeliveryError, 'human-typing'):
            deliver(t, stable_wait=0)
        self.assertEqual(t.actions, [])

    def test_stable_window_defaults_and_alias(self):
        for delay in ('0', '8', '12'):
            self.assertEqual(m.options(['%1', '--submit-delay', delay, 'hello']).stable_window, float(delay))
        for flag in ('--stable-window', '--stable-wait'):
            self.assertEqual(m.options(['%1', flag, '2', 'hello']).stable_window, 2)

    def test_zero_window_backoff_requires_unchanged_pair(self):
        t = Terminal(draft='typing')
        original = t.frame
        def changing():
            t.draft = 'typing ' + str(t.frames)
            return original()
        t.frame = changing
        o = SimpleNamespace(max_wait=1, stable_wait=0, poll_interval=.25, backoff=True)
        sender = m.Sender(t, o, t.payload, Path('/unused'), t.clock, t.sleep)
        with self.assertRaisesRegex(m.DeliveryError, 'human-typing'):
            sender.ready()
        self.assertEqual(t.now, 1)
        self.assertEqual(t.actions, [])

    def test_zero_window_one_change_waits_for_repeated_sample(self):
        t = Terminal(draft='first')
        original = t.frame
        def changing_once():
            if t.frames == 1:
                t.draft = 'second'
            return original()
        t.frame = changing_once
        o = SimpleNamespace(max_wait=1, stable_wait=0, poll_interval=.25, backoff=True)
        sender = m.Sender(t, o, t.payload, Path('/unused'), t.clock, t.sleep)
        self.assertEqual(sender.ready()[1].text, 'second')
        self.assertGreaterEqual(t.frames, 3)
        self.assertEqual(t.actions, [])

    def test_append_failure_metadata_and_allocation_not_attempted(self):
        for allocation_failure in (True, False):
            with self.subTest(allocation_failure=allocation_failure), tempfile.TemporaryDirectory() as tmp:
                pending = Path(tmp) / 'pending'
                pending.mkdir()
                t = Terminal(draft='stale TICK', mutate=not allocation_failure)
                t.identity = '0:%1'
                o = m.options(['%1', '--stable-window', '0', '--submit-delay', '0', '--poll-interval', '.01', 'hello'])
                err = io.StringIO()
                allocation = patch.object(m.tempfile, 'NamedTemporaryFile', side_effect=OSError('private detail')) if allocation_failure else nullcontext()
                with patch.object(m, 'options', return_value=o), patch.object(m, 'Tmux', return_value=t), patch.object(m, 'pane_lock', return_value=nullcontext()), patch.object(m.tempfile, 'mkdtemp', return_value=str(pending)), allocation, redirect_stderr(err):
                    self.assertEqual(m.main(), 2 if allocation_failure else 3)
                result = json.loads(err.getvalue())
                self.assertEqual(result['stale_draft_length'], 10)
                self.assertEqual(result['input_attempted'], not allocation_failure)
                self.assertEqual(result['paste_attempted'], not allocation_failure)
                self.assertNotIn('stale TICK', err.getvalue())
                self.assertEqual((pending / 'message').read_bytes(), b'hello')

    def test_collapsed_append_stops_and_cannot_replay(self):
        for harness in ('codex', 'claude'):
            t = Terminal(draft='stale TICK', collapsed=True)
            t.harness = harness
            with self.assertRaisesRegex(m.DeliveryError, 'mixed-input'):
                deliver(t)
            self.assertEqual((t.pastes, t.enters), (1, 0))
            first_actions = list(t.actions)
            with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
                deliver(t)
            self.assertEqual(t.actions, first_actions)

    def test_stale_draft_combined_enter_recovery(self):
        t = Terminal(draft='stale TICK', dropped=1)
        self.assertEqual(deliver(t)['enters'], 2)
        self.assertEqual((t.pastes, t.submitted), (1, 'stale TICK\nhello'))

    def test_stale_draft_failure_keeps_only_outgoing_pending(self):
        t = Terminal(draft='stale TICK', mutate=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'message'
            path.write_text(t.payload)
            o = SimpleNamespace(max_wait=8, stable_wait=3, poll_interval=.25,
                                backoff=False, submit_delay=1, render_wait=3,
                                receipt_wait=8, retry_wait=3)
            sender = m.Sender(t, o, t.payload, path, t.clock, t.sleep)
            with self.assertRaisesRegex(m.DeliveryError, 'mixed-input'):
                sender.deliver()
            self.assertEqual(path.read_text(), 'hello')
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            self.assertEqual(t.enters, 0)

    def test_combined_residue_two_attempts_never_repastes(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                t = Terminal(draft='stale TICK', dropped=99)
                t.harness = harness
                with self.assertRaisesRegex(m.DeliveryError, 'receipt-timeout'):
                    deliver(t)
                self.assertEqual(t.draft, 'stale TICK\nhello')
                first_actions = list(t.actions)
                try:
                    deliver(t)
                except m.DeliveryError:
                    pass
                self.assertEqual(t.actions, first_actions, 'retained combined input was replayed')
                self.assertEqual(t.pastes, 1)

    def test_mixed_own_payload_residue_both_harnesses(self):
        for harness in ('codex', 'claude'):
            for draft in ('hello human', 'human hello', 'human he\nllo'):
                with self.subTest(harness=harness, draft=draft):
                    t = Terminal(draft=draft)
                    t.harness = harness
                    try:
                        deliver(t)
                    except m.DeliveryError:
                        pass
                    self.assertEqual(t.actions, [], 'recognizable outgoing content was replayed')

    def test_observe_private_draft_file_utf8_in_ascii_locale(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'draft'
            child = r"""
import importlib.util, locale, pathlib, sys
sys.dont_write_bytecode = True
assert sys.flags.utf8_mode == 0
assert locale.getpreferredencoding(False).lower() in {'ascii', 'ansi_x3.4-1968'}
spec = importlib.util.spec_from_file_location('screen', sys.argv[1])
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)
draft = 'caf\u00e9'
frame = '\u2500' * 40 + '\n\u276f\u00a0' + draft + '\n' + '\u2500' * 40 + '\n'
assert screen.observe(frame, 'outgoing', 6, 1, pathlib.Path(sys.argv[2])) == 'draft'
"""
            env = dict(os.environ, LC_ALL='C', PYTHONUTF8='0', PYTHONCOERCECLOCALE='0')
            result = subprocess.run([sys.executable, '-c', child,
                                     str(Path(__file__).with_name('claude-send-screen.py')), str(path)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_bytes(), 'caf\u00e9'.encode('utf-8'))

    def test_partial_outgoing_overlap_is_still_a_stale_draft(self):
        for draft in ('he human', 'world hell', 'ello'):
            t = Terminal(draft=draft)
            self.assertEqual(deliver(t)['status'], 'delivered')
            self.assertEqual(t.submitted, draft + '\nhello')
            self.assertEqual(t.pastes, 1)

    def test_existing_ambiguous_payload_never_reappended(self):
        for draft in ('hello', '[Pasted text #1 +20 lines]', '[Pasted Content 5 chars]'):
            t = Terminal(draft=draft)
            with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
                deliver(t)
            self.assertEqual(t.actions, [])

    def test_change_only_at_second_observation_defers(self):
        t = Terminal(draft='stale TICK')
        original = t.frame
        def second():
            if t.now >= 3:
                t.draft = 'new human text'
            return original()
        t.frame = second
        with self.assertRaisesRegex(m.DeliveryError, 'human-typing'):
            deliver(t)
        self.assertEqual(t.actions, [])

    def test_typing_exits_without_paste(self):
        t = Terminal(draft='draft')
        original = t.frame
        def changing():
            t.draft = 'draft ' + str(int(t.now))
            return original()
        t.frame = changing
        with self.assertRaisesRegex(m.DeliveryError, 'human-typing'):
            deliver(t)
        self.assertEqual(t.pastes, 0)

    def test_backoff_waits_until_human_submits(self):
        t = Terminal(draft='draft')
        original = t.frame
        def changing():
            if not t.pastes:
                t.draft = 'draft' if t.now < 2 else ''
            return original()
        t.frame = changing
        self.assertEqual(deliver(t, backoff=True)['status'], 'delivered')
        self.assertGreaterEqual(t.accepted_at, 6)

    def test_modal_no_keys(self):
        t = Terminal()
        t.frame = lambda: m.Frame('Would you like to proceed?\n› yes\n', 2, 1)
        with self.assertRaisesRegex(m.DeliveryError, 'modal'):
            deliver(t)
        self.assertEqual(t.actions, [])

    def test_wrapped_and_multiline_composers(self):
        for harness in ['codex', 'claude']:
            f = screen('A long message\n  with another line', harness=harness)
            inp = m.composer(f)
            self.assertEqual(inp.kind, 'text')
            self.assertTrue(m.paste_evidence(inp, 'A long message with another line'))

    def test_assistant_echo_not_receipt(self):
        f = screen(history='• The message was hello\n\n')
        self.assertEqual(m.records(f, m.composer(f))[('submitted', 'hello')], 0)

    def test_collapsed_history_does_not_prove_receipt(self):
        f = screen(history='› [Pasted text #1 +20 lines]\n\n')
        self.assertEqual(m.records(f, m.composer(f))[('submitted', 'hello')], 0)

    def test_long_compaction_receipt_budget_is_supported(self):
        self.assertEqual(m.options(['%1', '--receipt-wait', '900', '/compact']).receipt_wait, 900)

    def test_partial_paste_render_settles_before_enter(self):
        t = Terminal(payload='hello long message')
        original = t.frame
        def frame():
            if t.pastes and t.now < 4:
                return screen('hello')
            return original()
        t.frame = frame
        self.assertEqual(deliver(t)['status'], 'delivered')
        self.assertGreaterEqual(t.accepted_at, 4.25)
        self.assertEqual(t.enters, 1)

    def test_collapsed_marker_render_updates_before_enter(self):
        t = Terminal(collapsed=True)
        original = t.frame
        def frame():
            if t.pastes and t.now < 3.5:
                return screen('[Pasted text #1 +2 lines]')
            return original()
        t.frame = frame
        self.assertEqual(deliver(t)['status'], 'delivered')
        self.assertEqual(t.enters, 1)

    def test_new_receipt_wins_over_stale_composer(self):
        t = Terminal(queued=True)
        original = t.keys
        def keys(*keys):
            original(*keys)
            if t.accepted_at is not None:
                t.draft = t.payload
        t.keys = keys
        self.assertEqual(deliver(t)['receipt'], 'queued')
        self.assertEqual(t.enters, 1)

    def test_content_character_count_marker(self):
        for payload in ['x' * 1034, 'hello\nworld', 'emoji 😀']:
            for count in {len(payload), len(payload.encode('utf-16-le')) // 2}:
                marker = f'[Pasted Content {count} chars]'
                self.assertTrue(m.paste_evidence(m.Input('text', marker), payload))
                self.assertFalse(m.paste_evidence(m.Input('text', marker + ' human'), payload))
                self.assertFalse(m.paste_evidence(m.Input('text', 'human ' + marker), payload))
                self.assertFalse(m.paste_evidence(m.Input('text', ' ' + marker), payload))
        self.assertFalse(m.paste_evidence(m.Input('text', '[Pasted Content 1034 chars]'), 'short'))

    def test_content_marker_drop_enter_recovers_and_confirms(self):
        t = Terminal(payload='x' * 1034, dropped=1)
        original = t.paste
        def paste(path):
            original(path)
            t.draft = '[Pasted Content 1034 chars]'
        t.paste = paste
        self.assertEqual(deliver(t)['status'], 'delivered')
        self.assertEqual((t.pastes, t.enters), (1, 2))

    def test_collapsed_marker_cannot_duplicate_visible_payload(self):
        for text in ['[Pasted text #1 +20 lines]hello', 'hello[Pasted text]hello']:
            self.assertFalse(m.paste_evidence(m.Input('text', text), 'hello'))

    def test_archived_input_box_is_not_receipt(self):
        for harness in ['codex', 'claude']:
            archived = screen('hello', harness=harness).raw
            f = screen(history=archived)
            self.assertEqual(m.records(f, m.composer(f))[('submitted', 'hello')], 0)

    def test_collapsed_history_matches_observed_paste(self):
        t = Terminal(collapsed=True)
        original = t.frame
        def frame():
            f = original()
            if t.accepted_at is not None:
                f.history = f.history.replace('› hello', '› [Pasted text #1 +20 lines]')
            return f
        t.frame = frame
        self.assertEqual(deliver(t)['status'], 'delivered')

    def test_compact_command_completion(self):
        t = Terminal(payload='/compact')
        original = t.frame
        def frame():
            f = original()
            if t.accepted_at is not None:
                f.history = f.history.replace('› /compact', '• Context compacted · 14s')
            return f
        t.frame = frame
        self.assertEqual(deliver(t)['receipt'], 'command')

    def test_empty_then_old_input_reappears_never_reenters(self):
        t = Terminal(dropped=99)
        original = t.frame
        def frame():
            if t.enters == 1 and 4.25 <= t.now < 4.75:
                return screen()
            return original()
        t.frame = frame
        with self.assertRaisesRegex(m.DeliveryError, 'receipt-timeout'):
            deliver(t)
        self.assertEqual(t.enters, 1)

    def test_recorded_composers_without_model_specific_positions(self):
        fixtures = Path(__file__).with_name('fixtures') / 'claude-send'
        for name in ['codex-fork-empty', 'codex-generating-queue', 'codex-context-queue']:
            x, y = map(int, (fixtures / (name + '.cursor')).read_text().strip().split(':'))
            f = m.Frame((fixtures / (name + '.ansi')).read_text(), x, y)
            self.assertEqual(m.composer(f).kind, 'empty' if name == 'codex-fork-empty' else 'text', name)

    def test_suggestion_probe_empty(self):
        t = Terminal()
        original_frame, original_keys = t.frame, t.keys
        t.ghost = True
        def frame():
            if t.ghost:
                f = screen('Try "hello"', harness='claude')
                f.x = 2
                return f
            return original_frame()
        def keys(*keys):
            if keys == ('Space', 'BSpace'):
                t.ghost = False
            original_keys(*keys)
        t.frame, t.keys = frame, keys
        self.assertEqual(deliver(t)['status'], 'delivered')
        self.assertEqual(t.actions.count('Space'), 1)

    def test_suggestion_shaped_human_draft_preserved(self):
        t = Terminal()
        f = screen('Try "my own draft"', harness='claude')
        f.x = 2
        t.frame = lambda: f
        with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
            deliver(t)
        self.assertEqual(t.actions, ['Space', 'BSpace'])


def typed_mutations():
    """Compile-valid changes must fail assertions, not merely fail to import."""
    subprocess.run([sys.executable, __file__], check=True, timeout=30)
    root = Path(__file__).parent
    original = (root / 'tmux-message.py').read_text()
    cases = [
        ('conflicting_modes', 'if self.typed and getattr(options, "literal_exit", False):', 'if False:'),
        ('typed_length_tail', 'if self.typed and (len(payload.encode("utf-8")) > 200 or payload != payload.rstrip() or payload.endswith(";")):', 'if False:'),
        ('typed_failure_method', '"typed" if o.typed_command or o.literal_exit else "paste"', '"typed" if sender and sender.typed else "paste"'),
        ('typed_dispatch', 'self.tmux.type_command(self.payload)', 'self.tmux.paste(self.path)'),
        ('typed_scope', 'if self.typed and not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_-]*(?: [^\\r\\n\\x00-\\x1f\\x7f]*)?", payload):', 'if False:'),
        ('literal', 'self.pane, "-l", "--", text', 'self.pane, "--", text'),
        ('typed_evidence', 'return inp.kind == "text" and inp.text == self.payload',
         'return paste_evidence(inp, self.payload)'),
    ]
    with tempfile.TemporaryDirectory(prefix='typed-command-mutations-') as tmp:
        target = Path(tmp)
        for name in ('test-tmux-message.py', 'claude-send-screen.py'):
            shutil.copy2(root / name, target / name)
        shutil.copytree(root / 'fixtures', target / 'fixtures')
        for name, before, after in cases:
            assert original.count(before) == 1, name
            code = original.replace(before, after)
            compile(code, name, 'exec')
            (target / 'tmux-message.py').write_text(code)
            result = subprocess.run([sys.executable, str(target / 'test-tmux-message.py')],
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode != 0 and 'FAIL:' in result.stderr and 'ERROR:' not in result.stderr, (name, result.stderr)
            print('killed:', name)


if __name__ == '__main__':
    if '--mutation-check' in sys.argv:
        typed_mutations()
    else:
        unittest.main()
