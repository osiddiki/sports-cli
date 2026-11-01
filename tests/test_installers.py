import pathlib
import subprocess
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


if __name__ == '__main__':
    unittest.main()
