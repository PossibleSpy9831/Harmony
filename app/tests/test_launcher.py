import unittest
from pathlib import Path
from unittest.mock import call, patch

from app import launcher


class LauncherTests(unittest.TestCase):
    def test_project_root_is_the_directory_containing_the_app_package(self):
        self.assertEqual(launcher.PROJECT_ROOT, Path(__file__).resolve().parents[2])
        self.assertTrue((launcher.PROJECT_ROOT / "app" / "__init__.py").is_file())

    @patch("app.launcher.subprocess.run")
    def test_run_action_dispatches_scan_and_player_from_project_root(self, run):
        run.return_value.returncode = 0

        self.assertEqual(launcher.run_action("1"), 0)
        self.assertEqual(launcher.run_action("2"), 0)

        self.assertEqual(
            run.call_args_list,
            [
                call(
                    [launcher.sys.executable, "-m", "app.tools.scannerv2"],
                    cwd=launcher.PROJECT_ROOT,
                    check=False,
                ),
                call(
                    [launcher.sys.executable, "-m", "app.src.Harmony"],
                    cwd=launcher.PROJECT_ROOT,
                    check=False,
                ),
            ],
        )

    def test_run_action_rejects_unsupported_choices(self):
        with self.assertRaisesRegex(ValueError, "Unknown launcher choice"):
            launcher.run_action("3")

    @patch("app.launcher.subprocess.run")
    @patch("app.launcher.Prompt.ask", side_effect=["1", "3"])
    def test_main_reports_a_failed_child_command(self, _prompt, run):
        run.return_value.returncode = 2

        with patch("app.launcher.Console.print") as print_message:
            launcher.main()

        self.assertTrue(
            any("status 2" in str(call_args) for call_args in print_message.call_args_list)
        )
