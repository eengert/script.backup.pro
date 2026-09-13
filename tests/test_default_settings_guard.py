from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class ProgramEntryGuardTests(unittest.TestCase):
    def test_guard_runs_before_backup_object_is_constructed(self):
        source = (ROOT / 'default.py').read_text()

        guard = source.index('settingsGuard.allow_operation(requested_action)')
        construction = source.index(
            'backup = XbmcBackup(settings_guard=settingsGuard)')
        dispatch = source.index('if(mode == SETTINGS):')

        self.assertLess(guard, construction)
        self.assertLess(guard, dispatch)

    def test_unsafe_entry_exits_before_all_settings_dependent_modes(self):
        source = (ROOT / 'default.py').read_text()
        exit_position = source.index('raise SystemExit(0)')

        for text in (
                'if(mode == SETTINGS):',
                'elif(mode == ADVANCED_EDITOR',
                'elif(mode == LAUNCHER):',
                'elif(mode == BACKUP or mode == RESTORE):'):
            self.assertLess(exit_position, source.index(text))

    def test_settings_dialog_is_bracketed_for_legitimate_rebaseline(self):
        source = (ROOT / 'default.py').read_text()
        begin = source.index('settingsGuard.begin_settings_edit()')
        opened = source.index('utils.openSettings()', begin)
        finish = source.index('settingsGuard.finish_settings_edit()', opened)

        self.assertLess(begin, opened)
        self.assertLess(opened, finish)

    def test_each_dispatched_action_records_a_read_only_boundary_snapshot(self):
        source = (ROOT / 'default.py').read_text()

        snapshot = source.index('settingsGuard.log_operation_boundary(action_name)')
        dispatch = source.index('if(mode == SETTINGS):')
        self.assertLess(snapshot, dispatch)
        for action in ('manual_backup', 'restore', 'open_settings',
                       'advanced_editor', 'status'):
            self.assertIn(action, source)

    def test_backup_and_restore_recheck_safety_immediately_before_planning(self):
        source = (ROOT / 'default.py').read_text()

        backup_gate = source.index("allow_operation('manual_backup_preplan')")
        backup_boundary = source.index(
            "log_operation_boundary('manual_backup_preplan')", backup_gate)
        backup_run = source.index('backup.backup()', backup_boundary)
        restore_gate = source.index("allow_operation('restore_preplan')")
        restore_select = source.index('backup.selectRestore(', restore_gate)

        self.assertLess(backup_gate, backup_boundary)
        self.assertLess(backup_boundary, backup_run)
        self.assertLess(restore_gate, restore_select)


if __name__ == '__main__':
    unittest.main()
