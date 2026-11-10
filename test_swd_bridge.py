"""Pruebas del puente (lado laptop): python3 -m unittest test_swd_bridge -v"""
import json
import os
import subprocess
import sys
import tempfile
import time
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


class WriteVocabularyTests(unittest.TestCase):
    def test_write_verbs_match_the_server(self):
        path = Path.home() / 'PycharmProjects' / 'SWDprod' / 'remote' / 'verbs.py'
        if not path.exists():
            self.skipTest('SWDprod no está en esta máquina')
        ns = {}
        exec(compile(path.read_text(), str(path), 'exec'), ns)  # noqa: S102
        self.assertEqual((ns['WRITE_VERBS'], ns['KEYS'], ns['MAX_TEXT'], ns['NAME_RE'].pattern),
                         (b.WRITE_VERBS, b.KEYS, b.MAX_TEXT, b.NAME_RE.pattern))

    def test_text_is_cleaned(self):
        self.assertEqual(b.clean_text('  hola\r\nmundo\x1b[31m\x07  '), 'hola\nmundo[31m')   # sin ESC ni BEL
        for bad in ('', '   ', '\x1b\x1b', 'x' * (b.MAX_TEXT + 1), None):
            with self.assertRaises(b.VerbError):
                b.clean_text(bad)

    def test_keys_and_names(self):
        self.assertEqual(b.clean('key', {'pane': '%1', 'key': 'ctrl-c'})['key'], 'ctrl-c')
        self.assertEqual(set(b.KEYS), set(b.TMUX_KEYS))
        for args in ({'pane': '%1', 'key': 'C-d'}, {'pane': '%1', 'key': 'enter; ls'}):
            with self.assertRaises(b.VerbError):
                b.clean('key', args)
        for name in ('../x', 'a b', '-rf', '', 'x' * 50):
            with self.assertRaises(b.VerbError):
                b.clean('start', {'project': name, 'profile': 'sonnet-bajo'})


@unittest.skipUnless(subprocess.run(['which', 'tmux', 'cc'], capture_output=True).returncode == 0, 'sin tmux o cc')
class LiveTmuxTests(unittest.TestCase):
    """Contra un servidor de tmux PROPIO (`tmux -L`), jamás la sesión real. El «agente» es una copia de `cat` llamada
    `claude` (tmux ve el nombre del proceso), y el servidor un http.server en un puerto alto."""
    SOCK = f'swdbridge-test-{os.getpid()}'
    PORT = 18197

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        # Un «agente» de mentira: un eco mínimo compilado y llamado `claude` (tmux ve el nombre del ejecutable). Copiar
        # /bin/cat no sirve: macOS mata la copia de un binario del sistema (pierde la firma).
        fake_agent = t / 'claude'
        (t / 'agent.c').write_text('#include <unistd.h>\nint main(void){char b[4096];ssize_t n;'
                                   'while((n=read(0,b,sizeof b))>0)write(1,b,n);return 0;}\n')
        if subprocess.run(['cc', '-o', str(fake_agent), str(t / 'agent.c')], capture_output=True).returncode != 0:
            raise unittest.SkipTest('sin compilador de C para el agente de prueba')
        projects = [{'alias': 'fakeproj', 'path': str(t), 'venv_path': '', 'panes_config': [
            {'name': 'Main', 'use_cd': False, 'use_venv': False, 'custom_cmd': None},
            {'name': 'Server', 'use_cd': False, 'use_venv': False, 'custom_cmd': 'python3 -m http.server $PORT --bind 127.0.0.1'},
            {'name': 'Extra', 'use_cd': False, 'use_venv': False, 'custom_cmd': None}]},
            {'alias': 'otro', 'path': str(t), 'venv_path': '', 'panes_config': []}]
        (t / 'projects.json').write_text(json.dumps(projects))
        (t / 'registry.json').write_text(json.dumps({'ports': {'fakeproj': cls.PORT}, 'profiles': {}}))
        launcher = t / 'launcher.py'
        launcher.write_text('import sys, pathlib; pathlib.Path(sys.argv[0]).with_name("launched.txt").write_text(" ".join(sys.argv[1:]))\n')
        cls.env = {**os.environ, 'SWD_BRIDGE_TMUX_SOCKET': cls.SOCK, 'SWD_BRIDGE_PROJECTS': str(t / 'projects.json'),
                   'SWD_REGISTRY': str(t / 'registry.json'), 'SWD_BRIDGE_LAUNCHER': str(launcher), 'SWD_BRIDGE_DIR': str(t)}
        tm = ['tmux', '-L', cls.SOCK]
        wid = subprocess.run([*tm, 'new-session', '-d', '-P', '-F', '#{window_id}', '-s', 't', '-n', 'fakeproj',
                              '-x', '160', '-y', '40', '-c', str(t), f'exec {fake_agent}'],
                             check=True, capture_output=True, text=True).stdout.strip()
        # Sin renombrado automático: tmux le pondría a la ventana el nombre del comando y dejaría de ser «fakeproj».
        subprocess.run([*tm, 'set-window-option', '-t', wid, 'automatic-rename', 'off'], check=True)
        subprocess.run([*tm, 'set-window-option', '-t', wid, 'allow-rename', 'off'], check=True)   # el prompt de zsh la renombra
        subprocess.run([*tm, 'split-window', '-v', '-t', f'{wid}', '-c', str(t)], check=True)
        subprocess.run([*tm, 'split-window', '-h', '-t', f'{wid}', '-c', str(t)], check=True)
        panes = subprocess.run([*tm, 'list-panes', '-t', wid, '-F', '#{pane_id}'], capture_output=True, text=True).stdout.split()
        subprocess.run([*tm, 'send-keys', '-t', panes[2], f'python3 -m http.server {cls.PORT} --bind 127.0.0.1', 'Enter'], check=True)
        time.sleep(1.5)
        # El default-command de ~/.tmux.conf hace `tmux rename-window Hallelujah` en cada panel nuevo; el launcher real
        # renombra al final, igual que acá.
        subprocess.run([*tm, 'rename-window', '-t', wid, 'fakeproj'], check=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['tmux', '-L', cls.SOCK, 'kill-server'], capture_output=True)
        cls.tmp.cleanup()

    def bridge(self, *args):
        return subprocess.run([sys.executable, str(HERE / 'swd_bridge.py'), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def snap(self):
        return json.loads(self.bridge('--snapshot').stdout)

    def pane(self, role):
        w = next(w for w in self.snap()['windows'] if w['name'] == 'fakeproj')
        return w, next(p for p in w['panes'] if p['role'] == role)

    def test_1_snapshot_declares_projects_and_roles(self):
        s = self.snap()
        self.assertEqual(s['projects'], ['fakeproj', 'otro'])
        w, agent = self.pane('agent')
        self.assertEqual((agent['command'], w['port']), ('claude', self.PORT))

    def test_2_send_and_keys_reach_the_agent(self):
        _, agent = self.pane('agent')
        r = self.bridge('--exec', 'send', f'pane={agent["id"]}', 'text=hola desde el teléfono')
        self.assertEqual(r.returncode, 0, r.stderr)
        time.sleep(0.5)
        out = self.bridge('--exec', 'capture', f'pane={agent["id"]}').stdout
        self.assertIn('hola desde el teléfono', out)
        self.assertEqual(self.bridge('--exec', 'key', f'pane={agent["id"]}', 'key=y').returncode, 0)

    def test_3_writes_never_go_to_a_shell_or_server_pane(self):
        for role in ('shell', 'server'):
            _, p = self.pane(role)
            r = self.bridge('--exec', 'send', f'pane={p["id"]}', 'text=rm -rf ~')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('no se escribe en una terminal', r.stderr)
            r = self.bridge('--exec', 'key', f'pane={p["id"]}', 'key=enter')
            self.assertNotEqual(r.returncode, 0)

    def test_4_server_restart_runs_the_registered_command(self):
        w, server = self.pane('server')
        r = self.bridge('--exec', 'server-restart', f'window={w["id"]}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'export PORT', subprocess.run(['tmux', '-L', self.SOCK, 'capture-pane', '-p', '-t', server['id'], '-S', '-40'],
                                                     capture_output=True, text=True).stdout)
        self.assertIn('http.server $PORT', r.stdout)
        time.sleep(1)
        self.assertEqual(self.pane('server')[1]['command'].lower()[:6], 'python')     # volvió a correr

    def test_5_start_validates_and_uses_the_launcher_in_remote_mode(self):
        r = self.bridge('--exec', 'start', 'project=noexiste', 'profile=sonnet-bajo')
        self.assertIn('no está en projects.json', r.stderr)
        r = self.bridge('--exec', 'start', 'project=fakeproj', 'profile=sonnet-bajo')
        self.assertIn('ya está abierto', r.stderr)
        r = self.bridge('--exec', 'start', 'project=otro', 'profile=noexiste')
        self.assertIn('no existe', r.stderr)
        r = self.bridge('--exec', 'start', 'project=otro', 'profile=sonnet-bajo')
        self.assertEqual(r.returncode, 0, r.stderr)
        time.sleep(1.5)
        launched = (Path(self.tmp.name) / 'launched.txt').read_text()
        self.assertEqual(launched, 'otro --profile sonnet-bajo --remote')

    def test_6_late_writes_are_refused(self):
        _, agent = self.pane('agent')
        calls = []
        orig_call, orig_audit = b.call, b.audit
        b.call = lambda url, token, path, payload, timeout=20: calls.append(payload) or (200, {})
        b.audit = lambda entry: None
        try:
            b.handle('u', 't', {'id': 9, 'verb': 'send', 'args': {'pane': agent['id'], 'text': 'tarde'}, 'age': 120},
                     {'windows': []})
        finally:
            b.call, b.audit = orig_call, orig_audit
        self.assertFalse(calls[0]['ok'])
        self.assertIn('llegó tarde', calls[0]['error'])
