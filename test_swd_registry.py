"""Pruebas del registro de puertos/perfiles: python3 -m unittest test_swd_registry -v"""
import importlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['SWD_REGISTRY'] = str(Path(self.tmp.name) / 'registry.json')
        import swd_registry
        self.reg = importlib.reload(swd_registry)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop('SWD_REGISTRY', None)

    def test_pinned_ports_are_respected_and_distinct(self):
        self.assertEqual(self.reg.port_for('safety'), 8000)
        self.assertEqual(self.reg.port_for('swd'), 8006)
        self.assertEqual(len(set(self.reg.PINNED_PORTS.values())), len(self.reg.PINNED_PORTS))

    def test_new_projects_get_the_next_free_port_forever(self):
        a, b = self.reg.port_for('uno'), self.reg.port_for('dos')
        self.assertEqual((a, b), (8100, 8101))
        self.assertEqual(self.reg.port_for('uno'), 8100)            # estable
        self.assertNotIn(a, self.reg.PINNED_PORTS.values())

    def test_concurrent_launchers_never_share_a_port(self):
        got = {}
        def work(i):
            got[i] = self.reg.port_for(f'p{i}')
        threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(set(got.values())), 12)
        self.assertEqual(len(self.reg.load()['ports']), 12)

    def test_claude_command_uses_account_by_project(self):
        spec = {'provider': 'claude', 'model': 'sonnet', 'effort': 'low'}
        self.assertEqual(self.reg.agent_command(spec, '~/PycharmProjects/SWDprod'),
                         'claudep --model sonnet --effort low')
        self.assertEqual(self.reg.agent_command(spec, '~/dev-chirho/intranet-chirho'),
                         'claudet --model sonnet --effort low')

    def test_codex_family_resolves_to_newest_offered(self):
        slugs = [m['slug'] for m in self.reg.codex_catalog()]
        if not slugs:
            self.skipTest('sin ~/.codex/models_cache.json')
        luna = self.reg.resolve_codex_family('luna')
        self.assertTrue(luna.endswith('-luna'))
        self.assertEqual(luna, [s for s in slugs if s.endswith('-luna')][0])
        cmd = self.reg.agent_command({'provider': 'codex', 'family': 'luna', 'effort': 'low'})
        self.assertEqual(cmd, f'codext -m {luna} -c model_reasoning_effort=low')

    def test_unknown_codex_family_is_an_error(self):
        with self.assertRaises(ValueError):
            self.reg.agent_command({'provider': 'codex', 'family': 'noexiste', 'effort': 'low'})

    def test_user_profiles_override_defaults(self):
        self.reg.save({'ports': {}, 'profiles': {'sonnet-bajo': {'provider': 'claude', 'model': 'haiku', 'effort': 'low'}}})
        self.assertEqual(self.reg.profiles()['sonnet-bajo']['model'], 'haiku')
        self.assertIn('opus-alto', self.reg.profiles())

    def test_default_profiles_are_the_requested_ones(self):
        p = self.reg.profiles()
        self.assertEqual({(v['provider'], v.get('model') or v.get('family'), v['effort']) for v in p.values()}, {
            ('claude', 'sonnet', 'low'), ('claude', 'sonnet', 'medium'), ('claude', 'sonnet', 'high'),
            ('claude', 'opus', 'high'), ('codex', 'luna', 'low'), ('codex', 'terra', 'medium'),
            ('codex', 'sol', 'high')})


if __name__ == '__main__':
    unittest.main()
