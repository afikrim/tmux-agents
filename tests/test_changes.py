import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from lib import changes, diffpane, ui


class ChangeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ta diff ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.paths = {'dir': str(Path(self.temp.name) / 'private')}
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.test')
        (self.root / 'code.py').write_text('original\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')

    def git(self, *args):
        return changes.git(str(self.root), *args)

    def test_turn_excludes_existing_dirty_and_untracked_changes_without_index_mutation(self):
        code = self.root / 'code.py'
        code.write_text('original\nuser change\n')
        (self.root / 'user.txt').write_text('user draft\n')
        index = (self.root / '.git/index').read_bytes()
        changes.checkpoint(self.paths, '%3', str(self.root), 100, 200, 300, 'turn')
        code.write_text('original\nuser change\nagent change\n')
        (self.root / 'new.py').write_text('new file\n')
        record = changes.load_checkpoint(self.paths, '%3', str(self.root), 100, ('codex', 200, 300))
        lines, count = changes.diff_lines(str(self.root), record)
        patch = '\n'.join(lines)
        self.assertEqual(count, 2)
        self.assertIn('+agent change', patch)
        self.assertNotIn('+user change', patch)
        self.assertNotIn('diff -- user.txt', patch)
        self.assertIn('+new file', patch)
        self.assertEqual(index, (self.root / '.git/index').read_bytes())
        self.assertEqual(os.stat(changes.checkpoint_path(self.paths, '%3')).st_mode & 0o777, 0o600)
        self.assertIsNone(changes.load_checkpoint(self.paths, '%3', str(self.root), 999))
        self.assertIsNone(changes.load_checkpoint(self.paths, '%3', str(self.root), 100, ('codex', 201, 301)))
        worktree, _ = changes.diff_lines(str(self.root))
        self.assertIn('+user change', '\n'.join(worktree))

    def test_new_deleted_binary_and_repeated_turn_checkpoints(self):
        record = changes.checkpoint(self.paths, '%3', str(self.root), 100)
        (self.root / 'code.py').unlink()
        (self.root / 'binary').write_bytes(b'\0binary')
        lines, count = changes.diff_lines(str(self.root), record)
        self.assertEqual(count, 2)
        self.assertIn('Binary file changed', lines)
        self.assertIn('+++ /dev/null', lines)
        record = changes.checkpoint(self.paths, '%3', str(self.root), 100)
        self.assertEqual(changes.diff_lines(str(self.root), record)[1], 0)
        (self.root / 'code.py').write_text('recreated\n')
        self.assertIn('--- /dev/null', changes.diff_lines(str(self.root), record)[0])

    def test_unborn_repo_and_git_worktree(self):
        branch = self.git('branch', '--show-current').decode().strip()
        self.git('checkout', '--orphan', 'fresh')
        self.git('rm', '-rf', '.')
        record = changes.checkpoint(self.paths, '%3', str(self.root), 100)
        (self.root / 'new').write_text('new\n')
        self.assertEqual(changes.diff_lines(str(self.root), record)[1], 1)
        self.git('checkout', '-f', branch)
        other = Path(self.temp.name) / 'linked worktree'
        self.git('worktree', 'add', '-qb', 'linked', str(other))
        self.assertEqual(changes.repository(str(other)), os.path.realpath(other))

    def test_large_files_ignored_files_and_symlink_parent(self):
        (self.root / '.gitignore').write_text('secret\n')
        (self.root / 'secret').write_text('not included')
        (self.root / 'large').write_bytes(b'x' * (changes.MAX_FILE + 1))
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (self.root / 'linkdir').symlink_to(outside)
        with self.assertRaises(ValueError):
            changes.working_blob(str(self.root), 'linkdir/file')
        record = changes.checkpoint(self.paths, '%3', str(self.root), 100)
        self.assertNotIn('secret', record['files'])
        self.assertIn('large', record['omitted'])

    def test_mode_only_change_and_disabled_tracking(self):
        (self.root / 'code.py').chmod(0o755)
        lines, count = changes.diff_lines(str(self.root))
        self.assertEqual(count, 1)
        self.assertIn('new mode 100755', lines)
        changes.checkpoint(self.paths, '%3', str(self.root), 100)
        (self.root / 'code.py').chmod(0o644)
        record = changes.load_checkpoint(self.paths, '%3', str(self.root), 100)
        self.assertIn('new mode 100644', changes.diff_lines(str(self.root), record)[0])
        Path(self.paths['dir'], 'disabled').touch()
        with self.assertRaises(ValueError):
            changes.checkpoint(self.paths, '%3', str(self.root), 100)

    def test_total_budget_and_non_utf8_are_reported(self):
        from unittest.mock import patch
        (self.root / 'code.py').write_text('dirty before turn\n')
        with patch.object(changes, 'MAX_TOTAL', 4):
            record = changes.checkpoint(self.paths, '%3', str(self.root), 100)
            self.assertIn('code.py', record['omitted'])
        lines, count = changes.diff_lines(str(self.root), record)
        self.assertNotIn('Working tree is clean.', lines)
        self.assertTrue(any('omitted' in line for line in lines))
        (self.root / 'raw').write_bytes(b'\xff\xfe')
        self.assertIn('Binary file changed', changes.diff_lines(str(self.root))[0])

    def test_soft_wrap_preserves_text_cells_and_color(self):
        line = '+ABC工具é ' * 8
        rows = diffpane.display_rows([line], 12, True)
        self.assertGreater(len(rows), 1)
        self.assertEqual(''.join(row[0] for row in rows), line)
        self.assertTrue(all(ui.cell_width(row[0]) <= 11 for row in rows))
        self.assertTrue(all(row[1] == 'accent' and row[2] == 0 for row in rows))
        self.assertEqual(diffpane.cell_slice('A工具B', 2, 3), ' 具')
        data = ('Working tree', 'repo', '', [line], 1)
        import re
        strip = lambda lines: [re.sub(r'\x1b\[[0-9;]*m', '', text) for text in lines]
        start, _ = diffpane.frame(data, 12, 10, wrap=True)
        panned, _ = diffpane.frame(data, 12, 10, horizontal=100, wrap=True)
        self.assertEqual(start, panned)
        end, top = diffpane.frame(data, 12, 10, top=999, wrap=True)
        self.assertGreater(top, 0)
        self.assertEqual(strip(end)[-1].strip(), rows[-1][0].strip())

    def test_frame_dimensions_colors_and_empty_state(self):
        data = ('Latest turn', 'repo', 'Checkpoint 12:00', ['diff -- file', '@@ -1 +1 @@', '-old', '+new'], 1)
        import re
        ansi = re.compile(r'\x1b\[[0-9;]*m')
        for width, height in ((12, 5), (40, 20), (64, 40)):
            lines, _ = diffpane.frame(data, width, height)
            self.assertEqual(len(lines), height)
            self.assertTrue(all(ui.cell_width(ansi.sub('', line)) == width for line in lines))
