# harness-hooks

Installs the hooks and settings that config files list into Claude Code and Codex, and removes them.
Works on macOS and Linux.

Run it with [uv](https://docs.astral.sh/uv/):

```sh
uvx harness-hooks install path/to/config.json
uvx harness-hooks remove path/to/config.json
```

Or install it once, which puts `harness-hooks` on your `PATH`:

```sh
uv tool install harness-hooks
```

## Config

A config is a JSON file with a `name` and any of these parts:

- `hooks`: commands the agents run at their events.
- `env` and `settings`: values for the agents' settings files.
- `inputs`: questions that `install` asks, such as for a token.
- `files`: files the hooks need.
- `agents`, such as `["claude"]`: limits the config to those agents.

## Hooks

```json
{
  "name": "notify",
  "files": ["notify.py"],
  "hooks": {
    "Stop": [{"command": "python3 {dir}/notify.py {agent}", "timeout": 5}],
    "claude": {
      "Notification": [{"matcher": "idle_prompt", "command": "python3 {dir}/notify.py claude"}]
    },
    "codex": {
      "PreToolUse": [{"matcher": "^request_user_input", "command": "python3 {dir}/notify.py codex"}]
    }
  }
}
```

- Events in `hooks` go to both agents; events in `hooks.claude` and `hooks.codex` only to
  that agent.
- `{dir}` becomes the folder with the hooks' files, so hooks can call scripts there.
  `{agent}` becomes `claude` or `codex`.
- `matcher` and `command` mean what they mean in each agent's own hook files. Other
  fields, such as `timeout`, are copied as they are.
- `files`, if given, lists the files the hooks need, as paths inside the config's folder.

## Settings

A config can set agent settings and ask for personal values, such as keys:

```json
{
  "name": "tracing",
  "inputs": {
    "key": {"ask": "Tracing key", "secret": true},
    "token": {"ask": "Gateway token", "secret": true, "file": true}
  },
  "env": {"TRACE_HOST": "https://trace.example.com", "TRACE_KEY": "{key}"},
  "settings": {
    "claude": {"apiKeyHelper": "cat {token}"},
    "codex": {"model_providers": {"gateway": {"auth": {"command": "cat", "args": ["{token}"]}}}}
  }
}
```

- `env` sets environment variables for each selected agent.
- `settings.claude` holds keys of Claude's `settings.json`, `settings.codex` keys of Codex's
  `config.toml`.
- `inputs` are questions that `install` asks; `{key}` in `env` and `settings` becomes the
  answer. Secret answers don't show as you type.
- With `"file": true`, the answer goes to a file only you can read, and `{token}` becomes its
  path. The secret stays out of the settings files.

`install` asks only for values it hasn't written yet; `--ask` asks again, for example after a
token change. Without a terminal, set `HARNESS_HOOKS_<INPUT>`, such as `HARNESS_HOOKS_TOKEN`.

One command can take several configs, and asks each question once:

```sh
uvx harness-hooks install gateway.json langfuse.json otel.json
```

[examples/team-setup](examples/team-setup) is a full setup: an LLM gateway or a subscription,
Langfuse traces and metrics.

### Your own values stay

harness-hooks changes or removes only the values it set and nobody edited since. It never
overwrites a value you set yourself, and tells you when one differs from the config.

`install` drops the values a config no longer lists, with the same care. A value that two
configs set stays until both are removed.

Settings go only to your own files: `--project` refuses a config with `env`, `settings` or
`inputs`.

## Copies

With `files` in the config, `install` copies those files, so the hooks keep working when the
config's folder moves or is deleted. Run `install` again to bring in changed files; `remove`
deletes the copies.

Without `files`, or with `--editable`, `{dir}` is the config's folder, so an edit takes
effect at once. `--editable` also deletes copies left by an earlier `install`. `--project`
never copies.

## Where hooks go

By default into `~/.claude/settings.json` and `~/.codex/hooks.json`, so they run in every
project. `CLAUDE_CONFIG_DIR` and `CODEX_HOME` move these files, as they do for the agents.

With `--project`, into `.claude/settings.json` and `.codex/hooks.json` of the git repository
that holds the config. Commit them, and everyone who clones the repository gets the hooks.
`{dir}` is then found from the repository root, so the hooks work in any clone.
Claude reads them only in sessions started at the repository root. Codex reads them only
after you trust the project.

`--agent claude` or `--agent codex` touches only that agent.

## Updating and removing

Keep `name` unchanged when updating or removing a config; it can't hold `/`. `install`
replaces that config's hooks, so a hook dropped from the config disappears; hooks you added
yourself stay.

Codex asks you to review new or changed hooks at its next start. It remembers approvals
by a hook's place in the file, so removing hooks can make it ask again about the hooks
after them.

## Development

From a clone, `uv tool install --editable .` runs the code in the clone, so an edit takes
effect at once.

```sh
uv run --no-project --with tomlkit python -m unittest
```
