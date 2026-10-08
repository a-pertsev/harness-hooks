#!/usr/bin/env python3
"""Installs the hooks that a config file lists into Claude Code and Codex, and removes them.

Usage: harness-hooks install|remove CONFIG [--project] [--editable] [--agent claude|codex]
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

AGENTS = ("claude", "codex")
PROJECT_FILES = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}
# Codex runs a hook in the folder the session started in, which can be below the project root.
PROJECT_ROOT = '"$(git rev-parse --show-toplevel)"'


def hook_file(agent, project):
    if project:
        return project / PROJECT_FILES[agent]
    if agent == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json"


def git_root(folder):
    result = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"--project needs the config inside a git repository: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def config_dir(folder, project):
    if not project:
        return shlex.quote(str(folder))
    rel = folder.relative_to(project)
    return PROJECT_ROOT + ("" if rel == Path() else "/" + shlex.quote(str(rel)))


def build(config, agent, folder, tag):
    events = {}
    for block in (config.get("hooks", {}), config.get(agent, {})):
        for event, entries in block.items():
            for entry in entries:
                handler = {"type": "command", **entry}
                matcher = handler.pop("matcher", None)
                handler["command"] = handler["command"].replace("{dir}", folder).replace("{agent}", agent) + tag
                group = {} if matcher is None else {"matcher": matcher}
                group["hooks"] = [handler]
                events.setdefault(event, []).append(group)
    return events


def strip(hooks, tag):
    kept = {}
    for event, groups in hooks.items():
        groups = [{**g, "hooks": [h for h in g["hooks"] if not h.get("command", "").endswith(tag)]} for g in groups]
        groups = [g for g in groups if g["hooks"]]
        if groups:
            kept[event] = groups
    return kept


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def copy(folder, files, target):
    """Builds the copy aside and swaps it in, so a hook never runs from a half-copied folder."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fresh = Path(tempfile.mkdtemp(dir=target.parent))
    for name in files:
        (fresh / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / name, fresh / name)
    old = fresh.with_suffix(".old")
    if target.exists():
        os.rename(target, old)
    os.rename(fresh, target)
    shutil.rmtree(old, ignore_errors=True)


def update(path, tag, new):
    """Replaces the hooks carrying the tag with the new ones; returns whether the file changed."""
    # Writing through a symlink keeps a settings file that lives elsewhere, such as in dotfiles.
    path = path.resolve()
    data = json.loads(path.read_text()) if path.exists() else {}
    old = data.get("hooks", {})
    hooks = strip(old, tag)
    for event, groups in new.items():
        hooks.setdefault(event, []).extend(groups)
    if hooks == old:
        return False
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Installs the hooks a config file lists into Claude Code and Codex, and removes them.")
    parser.add_argument("action", choices=["install", "remove"])
    parser.add_argument("config", type=Path)
    parser.add_argument("--project", action="store_true", help="use the hook files of the git repository that holds the config, for everyone who clones it")
    parser.add_argument("--editable", action="store_true", help="run hooks from the config's folder instead of a copy of its files, so edits take effect at once")
    parser.add_argument("--agent", choices=AGENTS, help="only this agent")
    args = parser.parse_args()

    path = args.config.resolve()
    config = json.loads(path.read_text())
    if "name" not in config or set(config) - {"name", "files", "hooks", *AGENTS}:
        sys.exit(f"{path}: needs a name, and allows only files, hooks, claude and codex besides it")
    name = config["name"]
    # The name is also a folder that remove deletes, so it must not lead out of its parent.
    if name in ("", ".", "..") or "/" in name:
        sys.exit(f"{path}: name must be a plain folder name")
    files = config.get("files", [])
    if args.action == "install":
        for file in files:
            if Path(file).is_absolute() or ".." in Path(file).parts or not (path.parent / file).is_file():
                sys.exit(f"{path}: {file} is not a file in {path.parent}")
    project = git_root(path.parent) if args.project else None
    copying = args.action == "install" and bool(files) and not args.editable and not project
    # Marks every installed command, so remove finds it even after the config changed.
    tag = f" # harness-hooks: {name}"
    folder = config_dir(path.parent, project)
    for agent in [args.agent] if args.agent else AGENTS:
        target = hook_file(agent, project)
        # Next to the agent's own settings, so the hooks keep working after the config's folder moves.
        copy_dir = target.parent / "harness-hooks" / name
        if copying:
            copy(path.parent, files, copy_dir)
            print(f"{agent}: copied {len(files)} files to {copy_dir}")
        hooks = build(config, agent, shlex.quote(str(copy_dir)) if copying else folder, tag) if args.action == "install" else {}
        changed = update(target, tag, hooks)
        print(f"{agent}: {'updated' if changed else 'unchanged'} {target}")
        if not copying and not project and copy_dir.exists():
            shutil.rmtree(copy_dir)
            print(f"{agent}: removed {copy_dir}")
        if agent == "codex" and changed and hooks:
            print("codex: asks you to review the new hooks at its next start")


if __name__ == "__main__":
    main()
