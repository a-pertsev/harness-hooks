#!/usr/bin/env python3
"""Installs the hooks and settings that config files list into Claude Code and Codex, and removes them.

Usage: harness-hooks install|remove CONFIG... [--project] [--editable] [--agent claude|codex] [--ask]
"""

import argparse
from collections.abc import MutableMapping
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

import tomlkit

AGENTS = ("claude", "codex")
FIELDS = {"name", "files", "hooks", "agents", "inputs", "env", "settings"}
PROJECT_FILES = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}
# Codex has no env block; it passes this table to the commands it runs.
ENV_PATH = {"claude": ("env",), "codex": ("shell_environment_policy", "set")}
# Codex runs a hook in the folder the session started in, which can be below the project root.
PROJECT_ROOT = '"$(git rev-parse --show-toplevel)"'
PLACEHOLDER = re.compile(r"\{(\w+)\}")
MISSING = object()


def home(agent):
    if agent == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def hook_file(agent, project):
    if project:
        return project / PROJECT_FILES[agent]
    return home(agent) / ("settings.json" if agent == "claude" else "hooks.json")


def settings_file(agent):
    return home(agent) / ("settings.json" if agent == "claude" else "config.toml")


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


def read_config(path, install, project):
    config = json.loads(path.read_text())
    if "name" not in config or set(config) - FIELDS:
        sys.exit(f"{path}: needs a name, and allows only {', '.join(sorted(FIELDS - {'name'}))} besides it")
    name = config["name"]
    # The name is also a folder that remove deletes, so it must not lead out of its parent.
    if name in ("", ".", "..") or "/" in name:
        sys.exit(f"{path}: name must be one folder name")
    if not set(config.get("agents", AGENTS)) | set(config.get("settings", {})) <= set(AGENTS):
        sys.exit(f"{path}: agents and settings can name only claude or codex")
    for key, spec in config.get("inputs", {}).items():
        if not re.fullmatch(r"\w+", key) or "ask" not in spec:
            sys.exit(f"{path}: input {key} needs an ask prompt; its name may contain only letters, digits and _")
    if project and config.keys() & {"inputs", "env", "settings"}:
        sys.exit(f"{path}: --project installs only hooks")
    if install:
        for file in config.get("files", []):
            if Path(file).is_absolute() or ".." in Path(file).parts or not (path.parent / file).is_file():
                sys.exit(f"{path}: {file} must be an existing file inside {path.parent}")
    return config


def build(config, agent, folder, tag):
    hooks = config.get("hooks", {})
    shared = {event: entries for event, entries in hooks.items() if event not in AGENTS}
    events = {}
    for block in (shared, hooks.get(agent, {})):
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


def set_hooks(data, tag, new):
    """Replaces the hooks carrying the tag with the new ones."""
    old = data.get("hooks", {})
    hooks = strip(old, tag)
    for event, groups in new.items():
        hooks.setdefault(event, []).extend(groups)
    if hooks == old:
        return
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)


def wanted_settings(config, agent):
    def leaves(tree, path=()):
        for key, value in tree.items():
            if isinstance(value, dict) and value:
                yield from leaves(value, path + (key,))
            else:
                yield path + (key,), value

    wanted = dict(leaves(config.get("settings", {}).get(agent, {})))
    wanted.update({ENV_PATH[agent] + (key,): value for key, value in config.get("env", {}).items()})
    return wanted


def uses(template, inputs):
    if isinstance(template, list):
        return set().union(*(uses(item, inputs) for item in template))
    return {key for key in PLACEHOLDER.findall(template) if key in inputs} if isinstance(template, str) else set()


def render(template, inputs, value):
    if isinstance(template, list):
        return [render(item, inputs, value) for item in template]
    if not isinstance(template, str):
        return template
    whole = PLACEHOLDER.fullmatch(template)
    if whole and whole[1] in inputs:
        return value(whole[1])

    def fill(match):
        key = match[1]
        if key not in inputs:
            return match[0]
        # A file's path inside a longer string is quoted, since such a string is a command.
        return shlex.quote(value(key)) if inputs[key].get("file") else value(key)

    return PLACEHOLDER.sub(fill, template)


def answer(key, spec, answers):
    """Asks once per run; a HARNESS_HOOKS_<KEY> variable answers instead, such as in scripts."""
    if key not in answers:
        variable = f"HARNESS_HOOKS_{key.upper()}"
        if variable in os.environ:
            answers[key] = os.environ[variable]
        elif sys.stdin.isatty():
            answers[key] = (getpass.getpass if spec.get("secret") else input)(f"{spec['ask']}: ").strip()
        else:
            sys.exit(f"{spec['ask']}: no terminal to ask in; set {variable}")
        if not answers[key]:
            sys.exit(f"{spec['ask']}: answer was empty")
    return answers[key]


def plain(value):
    return value.unwrap() if hasattr(value, "unwrap") else value


def digest(value):
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True).encode()).hexdigest()


def lookup(data, path):
    for key in path:
        if not isinstance(data, MutableMapping) or key not in data:
            return MISSING
        data = data[key]
    return data


def assign(data, path, value):
    for key in path[:-1]:
        if key not in data:
            data[key] = {}
        data = data[key]
    data[path[-1]] = value


def discard(data, path):
    """Deletes the value and the tables it leaves empty."""
    parents = [data]
    for key in path[:-1]:
        parents.append(parents[-1][key])
    for parent, key in reversed(list(zip(parents, path))):
        del parent[key]
        if parent:
            break


def sync(data, registry, name, wanted, inputs, value, ask):
    """Brings the values the named config set to the wanted ones; returns (path, reason) for each value left alone.

    The registry keeps a fingerprint of each value the tool wrote and the configs that want it,
    so the tool changes only its own values, and only while nobody has edited them.
    """
    left = []
    for path, template in wanted.items():
        current = lookup(data, path)
        entry = registry.get(path)
        ours = entry is not None and current is not MISSING and digest(current) == entry["hash"]
        alone = ours and set(entry["names"]) <= {name}
        if current is MISSING or (alone and (entry["names"].get(name) != template or (ask and uses(template, inputs)))):
            new = render(template, inputs, value)
            assign(data, path, new)
            registry[path] = {"path": list(path), "hash": digest(new), "names": {**(entry or {}).get("names", {}), name: template}}
        elif entry is not None and not ours:
            left.append((path, "changed since install"))
        elif any(not inputs[key].get("file") for key in uses(template, inputs)):
            # Comparing would need an answer, so the value stays, and this config doesn't claim it.
            continue
        elif plain(current) == render(template, inputs, value):
            if ours:
                entry["names"][name] = template
        else:
            left.append((path, "a different value is already there"))
    for path, entry in list(registry.items()):
        if name not in entry["names"] or path in wanted:
            continue
        del entry["names"][name]
        if entry["names"]:
            continue
        del registry[path]
        current = lookup(data, path)
        if current is not MISSING and digest(current) == entry["hash"]:
            discard(data, path)
        elif current is not MISSING:
            left.append((path, "changed since install"))
    return left


def parse(path, text):
    if path.suffix == ".toml":
        return tomlkit.parse(text)
    return json.loads(text) if text else {}


def dump(path, data):
    if path.suffix == ".toml":
        return tomlkit.dumps(data)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def load(files, path, agent):
    # Writing through a symlink keeps a settings file that lives elsewhere, such as in dotfiles.
    path = path.resolve()
    if path not in files:
        text = path.read_text() if path.exists() else ""
        files[path] = (parse(path, text), text, agent)
    return files[path][0]


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


def main():
    parser = argparse.ArgumentParser(description="Install and remove Claude Code and Codex hooks and settings from config files.")
    parser.add_argument("action", choices=["install", "remove"])
    parser.add_argument("configs", nargs="+", type=Path, metavar="config")
    parser.add_argument("--project", action="store_true", help="install hooks in the config's git repository")
    parser.add_argument("--editable", action="store_true", help="run hook files from the config's folder, so edits take effect at once")
    parser.add_argument("--agent", choices=AGENTS, help="only this agent")
    parser.add_argument("--ask", action="store_true", help="ask for values again, such as after a token change")
    args = parser.parse_args()

    install = args.action == "install"
    paths = [path.resolve() for path in args.configs]
    configs = [read_config(path, install, args.project) for path in paths]
    if len({config["name"] for config in configs}) < len(configs):
        sys.exit("configs in one run need different names")
    # Nothing is written until every config is read and every question answered.
    files, later, notes, review, answers = {}, [], [], set(), {}
    for path, config in zip(paths, configs):
        project = git_root(path.parent) if args.project else None
        name, inputs = config["name"], config.get("inputs", {})
        # Marks every installed command, so remove finds it even after the config changed.
        tag = f" # harness-hooks: {name}"
        for agent in [args.agent] if args.agent else AGENTS:
            installing = install and agent in config.get("agents", AGENTS)
            target = hook_file(agent, project)
            # Next to the agent's own settings, so the hooks keep working after the config's folder moves.
            copy_dir = target.parent / "harness-hooks" / name
            copying = installing and bool(config.get("files")) and not args.editable and not project
            if copying:
                later.append((copy, (path.parent, config["files"], copy_dir), f"{agent}: copied {', '.join(config['files'])} to {copy_dir}"))
            elif not project and copy_dir.exists():
                later.append((shutil.rmtree, (copy_dir,), f"{agent}: removed {copy_dir}"))
            hooks = build(config, agent, shlex.quote(str(copy_dir)) if copying else config_dir(path.parent, project), tag) if installing else {}
            set_hooks(load(files, target, agent), tag, hooks)
            if agent == "codex" and hooks:
                review.add(target.resolve())
            if project:
                continue

            settings = wanted_settings(config, agent) if installing else {}
            saved = load(files, home(agent) / "harness-hooks.json", None)
            registry = {tuple(entry["path"]): entry for entry in saved.pop("settings", [])}
            store = home(agent) / "harness-hooks-inputs" / name
            if settings or any(name in entry["names"] for entry in registry.values()):
                def value(key):
                    return str(store / key) if inputs[key].get("file") else answer(key, inputs[key], answers)

                left = sync(load(files, settings_file(agent), agent), registry, name, settings, inputs, value, args.ask)
                notes += [f"{agent}: kept {'.'.join(key)} in {settings_file(agent)}: {reason}" for key, reason in left]
            if registry:
                saved["settings"] = list(registry.values())
            installed = [settings[key] for key, entry in registry.items() if key in settings and name in entry["names"]]
            kept = {key for template in installed for key in uses(template, inputs) if inputs[key].get("file")}
            for key in sorted(kept):
                if args.ask or not (store / key).exists():
                    later.append((write, (store / key, answer(key, inputs[key], answers)), f"{agent}: saved the answer for \"{inputs[key]['ask']}\" in {store / key}"))
            if store.exists() and not kept:
                later.append((shutil.rmtree, (store,), f"{agent}: removed {store}"))
            for old in sorted(store.iterdir()) if store.exists() and kept else []:
                if old.name not in kept:
                    later.append((old.unlink, (), f"{agent}: removed {old}"))

    for action, action_args, message in later:
        action(*action_args)
        print(message)
    for path, (data, text, agent) in files.items():
        changed = dump(path, data) != dump(path, parse(path, text))
        if changed:
            write(path, dump(path, data))
        if agent:
            print(f"{agent}: {'updated' if changed else 'unchanged'} {path}")
        if changed and path in review:
            print("codex: will ask you to review the new hooks when it starts")
    for note in notes:
        print(note)


if __name__ == "__main__":
    main()
