#!/usr/bin/env python3
"""Behavior tests with a virtual clock and a simulated terminal, no model calls."""
import importlib.util
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

    def sleep(self, n): self.now += n
    def clock(self): return self.now

    def frame(self):
        self.frames += 1
        history = self.history
        if self.accepted_at is not None and self.now >= self.accepted_at + self.delayed:
            history += ('• Messages to be submitted after next tool call\n  ↳ ' if self.queued else '› ') + self.payload.replace('\n', '\n  ') + '\n\n'
        text = self.draft
        if self.mutate and self.pastes:
            text += ' human'
        return screen(text, history, prefix='• output ' + str(self.frames) + '\n\n')

    def paste(self, path):
        self.actions.append('paste')
        self.pastes += 1
        self.draft += '[Pasted text #1 +20 lines]' if self.collapsed else self.payload

    def keys(self, *keys):
        self.actions.extend(keys)
        if keys == ('Enter',):
            self.enters += 1
            if self.enters > self.dropped:
                self.accepted_at = self.now
                self.draft = ''


def deliver(t, **kw):
    o = dict(max_wait=8, stable_wait=3, poll_interval=.25, backoff=False,
             submit_delay=1, render_wait=3, receipt_wait=8, retry_wait=3)
    o.update(kw)
    return m.Sender(t, SimpleNamespace(**o), t.payload, Path('/unused'), t.clock, t.sleep).deliver()


class DeliveryTests(unittest.TestCase):
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

    def test_paused_draft_not_submitted(self):
        t = Terminal(draft='my unfinished message')
        with self.assertRaisesRegex(m.DeliveryError, 'existing-input'):
            deliver(t)
        self.assertEqual(t.pastes, 0)

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


if __name__ == '__main__': unittest.main()
