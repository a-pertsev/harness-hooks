# agent-hooks

Installs the hooks that a config file lists into Claude Code and Codex, and removes them.

Install it once from this folder with [uv](https://docs.astral.sh/uv/), which puts
`agent-hooks` on your `PATH`:

```sh
uv tool install --editable .
agent-hooks install path/to/agent-hooks.json
agent-hooks remove path/to/agent-hooks.json
```

Or run it without installing, for example from a folder next to this one:

```sh
uvx --with-editable ../agent-hooks agent-hooks install agent-hooks.json
```

Both run the code in this folder, so an edit takes effect at once. `uvx --from` would keep
running the copy it built the first time.

## Config

```json
{
  "name": "waiting-agents",
  "hooks": {
    "Stop": [{"command": "/usr/bin/python3 {dir}/hook.py {agent}", "timeout": 5}]
  },
  "claude": {
    "Notification": [{"matcher": "idle_prompt", "command": "/usr/bin/python3 {dir}/hook.py claude"}]
  },
  "codex": {
    "PreToolUse": [{"matcher": "^request_user_input", "command": "/usr/bin/python3 {dir}/hook.py codex"}]
  }
}
```

- `hooks` go to both agents, `claude` and `codex` only to that agent.
- `{dir}` becomes the folder that holds the config, so hooks can call scripts next to it.
  `{agent}` becomes `claude` or `codex`.
- `matcher` and `command` mean what they mean in each agent's own hook files. Other
  fields, such as `timeout`, are copied as they are.
- `files`, if given, lists the files the hooks need, as paths inside the config's folder.

## Copies

With `files` in the config, `install` copies those files to `~/.claude/agent-hooks/<name>/`
and `~/.codex/agent-hooks/<name>/`, and `{dir}` points there. The hooks keep working when
the config's folder moves or is deleted. Run `install` again to bring in changed files;
`remove` deletes the copies.

`--editable` skips the copies and runs the hooks from the config's folder, so an edit takes
effect at once. It also deletes copies left by an earlier `install`. `--project` never copies.

## Where hooks go

By default into `~/.claude/settings.json` and `~/.codex/hooks.json`, so they run in every
project. `CLAUDE_CONFIG_DIR` and `CODEX_HOME` move these files, as they do for the agents.

With `--project`, into `.claude/settings.json` and `.codex/hooks.json` of the git repository
that holds the config. Commit them, and everyone who clones the repository gets the hooks.
`{dir}` is then found from the repository root, so the hooks work in any clone.
Claude reads them only in sessions started at the repository root. Codex reads them only
after you trust the project.

`--agent claude` or `--agent codex` touches only that agent.

## How it finds its hooks

Each installed command ends with `# agent-hooks: <name>`. `install` replaces the commands
with that mark and `remove` deletes them, so a hook dropped from the config disappears on
the next run. Keep `name` the same once installed. It is also the copies' folder name, so it
can't hold `/`. Hooks without the mark, such as ones
you added by hand, are left alone, and the rest of each file is kept.

Codex asks you to review new or changed hooks at its next start. It remembers approvals
by a hook's place in the file, so removing hooks can make it ask again about the hooks
after them.

## Tests

```sh
/usr/bin/python3 -m unittest test_agent_hooks
```
