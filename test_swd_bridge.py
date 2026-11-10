"""Pruebas del puente (lado laptop): python3 -m unittest test_swd_bridge -v"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import swd_bridge as b  # noqa: E402


class VocabularyTests(unittest.TestCase):
    def test_same_contract_as_the_server(self):
        self.assertEqual(b.clean('capture', {'pane': '%3'}), {'pane': '%3', 'lines': 120})
        self.assertEqual(b.clean('git-diff', {'window': '@2'})['mode'], 'stat')
        self.assertEqual(b.clean('capture', {'pane': '%3', 'lines': 10 ** 6})['lines'], 400)
        bad = [('shell', {'cmd': 'ls'}), ('capture', {'pane': '%3; ls'}), ('git-diff', {'window': '@1', 'mode': '--output=x'}),
               ('git-status', {'window': '@1', 'cwd': '/'}), ('server-log', {'window': 'swd'})]
        for verb, args in bad:
            with self.assertRaises(b.VerbError, msg=(verb, args)):
                b.clean(verb, args)

    def test_server_side_vocabulary_matches(self):
        """Si SWDprod está al lado, sus verbos y límites deben ser los mismos que los de acá."""
        path = Path.home() / 'PycharmProjects' / 'SWDprod' / 'remote' / 'verbs.py'
        if not path.exists():
            self.skipTest('SWDprod no está en esta máquina')
        ns = {}
        exec(compile(path.read_text(), str(path), 'exec'), ns)  # noqa: S102 — archivo propio, solo constantes
        self.assertEqual(ns['VERBS'], b.VERBS)
        self.assertEqual((ns['MAX_LINES'], ns['DEFAULT_LINES'], ns['DIFF_MODES']), (b.MAX_LINES, b.DEFAULT_LINES, b.DIFF_MODES))


class RedactTests(unittest.TestCase):
    def test_secrets_are_hidden(self):
        samples = {
            'export OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123': 'sk-proj-abcdef',
            'token: ghp_' + 'a' * 36: 'ghp_aaaa',
            'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig': 'eyJhbGci',
            'RECO_TOKEN="supersecretvalue123"': 'supersecretvalue123',
            'postgres://user:hunter2pass@db:5432/x': 'hunter2pass',
            'AKIAABCDEFGHIJKLMNOP': 'AKIAABCDEFGHIJKLMNOP',
            '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----': 'abc',
        }
        for text, leaked in samples.items():
            self.assertNotIn(leaked, b.redact(text), text)

    def test_normal_output_is_untouched(self):
        text = 'Starting development server at http://localhost:8006/\n M swd/settings.py\n'
        self.assertEqual(b.redact(text), text)


class HookTests(unittest.TestCase):
    def run_hook(self, *args, stdin='', pane='%77', home=None):
        env = {**os.environ, 'TMUX_PANE': pane, 'SWD_BRIDGE_DIR': home}
        return subprocess.run([sys.executable, str(HERE / 'swd_bridge_hook.py'), *args], input=stdin,
                              capture_output=True, text=True, env=env, timeout=10)

    def test_states_and_silence(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / 'state' / '77.json'
            r = self.run_hook('claude', 'UserPromptSubmit', stdin='{"prompt": "hola"}', home=home)
            self.assertEqual((r.returncode, r.stdout), (0, ''))           # nunca imprime nada
            self.assertEqual(json.loads(state.read_text())['state'], 'working')
            self.run_hook('claude', 'Notification', stdin='{"notification_type": "permission_prompt"}', home=home)
            self.assertEqual(json.loads(state.read_text())['state'], 'needs_input')
            self.run_hook('claude', 'Notification', stdin='{"notification_type": "idle_prompt"}', home=home)
            self.assertEqual(json.loads(state.read_text())['state'], 'done')
            self.run_hook('codex', 'PermissionRequest', home=home)
            self.assertEqual(json.loads(state.read_text())['agent'], 'codex')
            self.run_hook('claude', 'SessionEnd', home=home)
            self.assertFalse(state.exists())
            r = self.run_hook('claude', 'Stop', stdin='no es json', home=home)
            self.assertEqual(r.returncode, 0)

    def test_outside_tmux_does_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_hook('claude', 'Stop', pane='', home=home)
            self.assertFalse((Path(home) / 'state').exists())


if __name__ == '__main__':
    unittest.main()
