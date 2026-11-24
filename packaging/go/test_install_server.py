import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('install_server', Path(__file__).with_name('install-server.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class AbruptExit(BaseException):
    pass


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.app, self.data = root / 'app', root / 'data'
        self.maintenance, self.candidate = root / 'maintenance', root / 'candidate'
        for path in (self.app, self.data, self.maintenance, self.candidate):
            path.mkdir()
        (self.app / 'VERSION').write_text('1.0.0')
        (self.candidate / 'VERSION').write_text('1.1.0')
        (self.data / 'toolbox.db').write_text('original data')
        self.layout = (8000, 'remote-multi')
        self.start_count = 0
        self.enterContext(patch.multiple(installer, APP=self.app, DATA=self.data, MAINTENANCE=self.maintenance,
                                         fsync_dir=lambda path: None, sync_program=lambda path: None,
                                         data_lock=nullcontext, service_user=lambda: None,
                                         systemctl=self.systemctl, copy_database=self.copy_database,
                                         bundled_version=lambda path: (path / 'VERSION').read_text()))

    def systemctl(self, action, *arguments):
        if action == 'start':
            self.start_count += 1
            if (self.app / installer.PENDING_MARKER).exists():
                (self.data / 'toolbox.db').write_text('migrated data')

    def copy_database(self, source, target, owner=None, replace=False):
        (target / 'toolbox.db').write_bytes((source / 'toolbox.db').read_bytes())

    def saved_state(self):
        return json.loads((self.maintenance / 'state.json').read_bytes())

    def assert_version_and_data(self, version, data):
        self.assertEqual((self.app / 'VERSION').read_text(), version)
        self.assertEqual((self.data / 'toolbox.db').read_text(), data)
        self.assertFalse((self.app / installer.PENDING_MARKER).exists())

    def test_unhealthy_candidate_restores_original_data(self):
        def probe(version, layout, gated=None):
            if gated:
                raise ValueError('unhealthy')

        with patch.object(installer, 'installed_health', probe):
            with self.assertRaisesRegex(ValueError, '原版本已重新运行'):
                installer.switch_release({}, self.layout, self.candidate, '1.1.0')
        self.assert_version_and_data('1.0.0', 'original data')
        self.assertIsNone(self.saved_state()['pending'])
        self.assertEqual(self.start_count, 2)

    def test_interrupted_unconfirmed_switch_can_restore_source(self):
        with patch.object(installer, 'installed_health', side_effect=AbruptExit):
            with self.assertRaises(AbruptExit):
                installer.switch_release({}, self.layout, self.candidate, '1.1.0')
        self.assertTrue((self.app / installer.PENDING_MARKER).exists())
        with patch.object(installer, 'installed_health'):
            installer.recover_pending(self.saved_state(), self.layout)
        self.assert_version_and_data('1.0.0', 'original data')

    def test_interrupted_confirmed_switch_activates_candidate(self):
        with patch.object(installer, 'installed_health'), patch.object(installer, 'finish_activation', side_effect=AbruptExit):
            with self.assertRaises(AbruptExit):
                installer.switch_release({}, self.layout, self.candidate, '1.1.0')
        state = self.saved_state()
        self.assertIsNone(state['pending'])
        self.assertEqual(state['previous'], state['activate'])
        with patch.object(installer, 'installed_health'):
            installer.finish_activation(state, self.layout)
        self.assert_version_and_data('1.1.0', 'migrated data')
        self.assertEqual((self.maintenance / state['previous'] / 'data' / 'toolbox.db').read_text(), 'original data')


if __name__ == '__main__':
    unittest.main()
