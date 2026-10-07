import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).with_name("agent_hooks.py")


class AgentHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.repo = root / "repo"
        self.tools = self.repo / "tools"
        self.tools.mkdir(parents=True)
        # Stands in for a hook script: records how it was called next to itself.
        script = self.tools / "log.sh"
        script.write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/log"\n')
        script.chmod(0o755)
        self.config = self.tools / "agent-hooks.json"
        self.write_config({
            "name": "demo",
            "hooks": {"Stop": [{"command": "{dir}/log.sh {agent} Stop", "timeout": 5}]},
            "claude": {"PreToolUse": [{"matcher": "AskUserQuestion", "command": "{dir}/log.sh claude ask"}]},
            "codex": {"PreToolUse": [{"matcher": "^request_user_input", "command": "{dir}/log.sh codex ask"}]},
        })
        self.claude_file = root / "claude" / "settings.json"
        self.codex_file = root / "codex" / "hooks.json"
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(root / "claude"), "CODEX_HOME": str(root / "codex")}

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, config):
        self.config.write_text(json.dumps(config))

    def run_tool(self, *args, check=True):
        return subprocess.run([sys.executable, TOOL, *args], text=True, capture_output=True, env=self.env, check=check)

    def commands(self, path):
        hooks = json.loads(path.read_text())["hooks"]
        return {event: [(g.get("matcher"), h["command"]) for g in groups for h in g["hooks"]] for event, groups in hooks.items()}

    def run_hook(self, command, cwd, log, env=None):
        subprocess.run(["/bin/sh", "-c", command], cwd=cwd, env={**os.environ, **(env or {})}, check=True)
        return log.read_text().splitlines()[-1]

    def test_install_adds_shared_and_agent_hooks_that_run(self):
        self.run_tool("install", str(self.config))

        log = self.tools / "log"
        for path, agent, matcher in [(self.claude_file, "claude", "AskUserQuestion"), (self.codex_file, "codex", "^request_user_input")]:
            [(stop_matcher, stop)] = self.commands(path)["Stop"]
            [(ask_matcher, ask)] = self.commands(path)["PreToolUse"]
            self.assertIsNone(stop_matcher)
            self.assertEqual(ask_matcher, matcher)
            self.assertEqual(self.run_hook(stop, "/", log), f"{agent} Stop")
            self.assertEqual(self.run_hook(ask, "/", log), f"{agent} ask")
        self.assertEqual(json.loads(self.claude_file.read_text())["hooks"]["Stop"][0]["hooks"][0]["timeout"], 5)

    def test_install_keeps_other_settings_and_hooks_and_repeats_without_changes(self):
        foreign = {"hooks": [{"type": "command", "command": "notify.sh"}]}
        self.claude_file.parent.mkdir()
        self.claude_file.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [foreign]}}))

        self.run_tool("install", str(self.config))
        first = self.claude_file.read_text()
        out = self.run_tool("install", str(self.config)).stdout

        self.assertEqual(self.claude_file.read_text(), first)
        self.assertIn("claude: unchanged", out)
        settings = json.loads(first)
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["hooks"]["Stop"][0], foreign)
        self.assertEqual(len(settings["hooks"]["Stop"]), 2)

    def test_install_replaces_hooks_of_an_earlier_config(self):
        self.run_tool("install", str(self.config))
        self.write_config({"name": "demo", "hooks": {"Stop": [{"command": "{dir}/log.sh new"}]}})

        self.run_tool("install", str(self.config))

        commands = self.commands(self.claude_file)
        self.assertEqual(list(commands), ["Stop"])
        [(_, stop)] = commands["Stop"]
        self.assertEqual(self.run_hook(stop, "/", self.tools / "log"), "new")

    def test_remove_takes_out_only_installed_hooks(self):
        settings = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify.sh"}]}]}}
        self.claude_file.parent.mkdir()
        self.claude_file.write_text(json.dumps(settings))
        self.run_tool("install", str(self.config))
        self.write_config({"name": "demo"})

        self.run_tool("remove", str(self.config))

        self.assertEqual(json.loads(self.claude_file.read_text()), settings)
        self.assertEqual(json.loads(self.codex_file.read_text()), {})

    def test_remove_without_install_writes_nothing(self):
        out = self.run_tool("remove", str(self.config)).stdout

        self.assertIn("claude: unchanged", out)
        self.assertFalse(self.claude_file.exists())
        self.assertFalse(self.codex_file.exists())

    def test_project_install_runs_scripts_in_a_clone_from_any_of_its_folders(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

        self.run_tool("install", str(self.config), "--project")

        self.assertFalse(self.claude_file.exists())
        self.assertFalse(self.codex_file.exists())
        clone = Path(self.tmp.name).resolve() / "clone"
        shutil.copytree(self.repo, clone)
        (clone / "sub").mkdir()
        log = clone / "tools" / "log"
        [(_, claude_stop)] = self.commands(clone / ".claude/settings.json")["Stop"]
        [(_, codex_stop)] = self.commands(clone / ".codex/hooks.json")["Stop"]
        self.assertEqual(self.run_hook(claude_stop, clone / "sub", log), "claude Stop")
        self.assertEqual(self.run_hook(codex_stop, clone / "sub", log), "codex Stop")

    def test_project_install_outside_git_is_refused(self):
        result = self.run_tool("install", str(self.config), "--project", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git repository", result.stderr)

    def test_agent_option_installs_into_that_agent_only(self):
        self.run_tool("install", str(self.config), "--agent", "codex")

        self.assertFalse(self.claude_file.exists())
        self.assertIn("Stop", self.commands(self.codex_file))

    def test_config_without_name_is_refused(self):
        self.write_config({"hooks": {"Stop": [{"command": "x"}]}})

        result = self.run_tool("install", str(self.config), check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.claude_file.exists())


if __name__ == "__main__":
    unittest.main()
