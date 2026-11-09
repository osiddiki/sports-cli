import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAC = ROOT / 'installers' / 'Install Sports CLI.command'
WINDOWS = ROOT / 'installers' / 'Install Sports CLI.bat'


class DoubleClickInstallerTests(unittest.TestCase):
    def test_mac_installer_is_an_executable_script_that_runs_the_installer_then_the_app(self):
        text = MAC.read_text()
        self.assertTrue(text.startswith('#!/bin/sh\n'))
        self.assertIn('raw.githubusercontent.com/osiddiki/sports-cli/main/install.sh | sh;', text)
        self.assertIn('exec "$HOME/.local/bin/sports"', text)
        mode = subprocess.run(['git', 'ls-files', '-s', str(MAC)], cwd=ROOT, capture_output=True, text=True).stdout
        if mode: self.assertTrue(mode.startswith('100755'), 'Finder only runs an executable .command file')
        self.assertEqual(subprocess.run(['sh', '-n', str(MAC)]).returncode, 0)

    def test_windows_installer_uses_crlf_and_opens_the_app_after_installing(self):
        data = WINDOWS.read_bytes()
        self.assertNotIn(b'\n', data.replace(b'\r\n', b''), 'cmd.exe needs CRLF line endings')
        text = data.decode('ascii')
        self.assertIn('install.ps1 | iex', text)
        self.assertLess(text.index('if errorlevel 1'), text.index(r'call "%LOCALAPPDATA%\sports-cli\bin\sports.cmd"'))

    def test_installers_add_a_launcher_so_the_app_opens_without_a_command(self):
        self.assertIn('Applications/Sports CLI.app', (ROOT / 'install.sh').read_text())
        self.assertIn('sports-cli.desktop', (ROOT / 'install.sh').read_text())
        self.assertIn("'Sports CLI.lnk'", (ROOT / 'install.ps1').read_text())


class UninstallTests(unittest.TestCase):
    def test_uninstall_removes_what_the_installer_made_and_keeps_the_rest_of_the_profile(self):
        with tempfile.TemporaryDirectory() as home:
            home = pathlib.Path(home)
            made = [home / '.local/share/sports-cli/sports-cli', home / '.local/bin/sports',
                    home / 'Applications/Sports CLI.app/Contents/Info.plist', home / '.local/share/applications/sports-cli.desktop',
                    home / '.config/nba-tui/config.json', home / '.cache/sports-live/checks.json',
                    home / '.local/share/sports-live/ad-mute/model.tflite']
            for path in made:
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text('x')
            mine = home / '.config/other-app/settings'; mine.parent.mkdir(parents=True); mine.write_text('keep')
            (home / '.zprofile').write_text('export A=1\n\n# Added by the Sports CLI installer\nexport PATH="$HOME/.local/bin:$PATH"\nexport B=2\n')
            env = {'HOME': str(home), 'PATH': os.environ['PATH']}
            done = subprocess.run(['sh', str(ROOT / 'uninstall.sh'), '--yes'], env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual([path for path in made if path.exists()], [])
            self.assertTrue(mine.exists())
            self.assertEqual((home / '.zprofile').read_text(), 'export A=1\n\nexport B=2\n')
            again = subprocess.run(['sh', str(ROOT / 'uninstall.sh'), '--yes'], env=env, capture_output=True, text=True)
            self.assertIn('not installed', again.stdout)

    def test_every_launcher_the_installers_add_is_one_uninstall_removes(self):
        self.assertIn('--uninstall) exec sh "$APP/uninstall.sh"', (ROOT / 'install.sh').read_text())
        self.assertIn('"--uninstall"', (ROOT / 'install.ps1').read_text())
        for name in ('Applications/Sports CLI.app', 'sports-cli.desktop'):
            self.assertIn(name, (ROOT / 'uninstall.sh').read_text())
        self.assertIn("'Sports CLI.lnk'", (ROOT / 'uninstall.ps1').read_text())
        self.assertNotIn(b'\n', (ROOT / 'installers/Uninstall Sports CLI.bat').read_bytes().replace(b'\r\n', b''))



class SetupTests(unittest.TestCase):
    def test_a_node_that_cannot_start_skips_the_resolver_instead_of_crashing_the_install(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            for name in ('node', 'npm'):
                tool = pathlib.Path(bin_dir) / name
                tool.write_text('#!/bin/sh\necho "dyld: Library not loaded" >&2\nkill -ABRT $$\n'); tool.chmod(0o755)
            env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'PYTHON': '/nonexistent/python3', 'SPORTS_CLI_INSTALLER': '1'}
            done = subprocess.run(['sh', str(ROOT / 'setup.sh')], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('Node.js is installed but does not start', done.stderr)
        self.assertNotIn('Library not loaded', done.stderr)

if __name__ == '__main__':
    unittest.main()
