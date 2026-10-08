# Team setup

An example of a team's Claude Code and Codex setup: an LLM gateway or your own subscription,
Langfuse traces, and OpenTelemetry metrics. Replace the `example.com` addresses with your own.

| Config | What it sets up | Asks for |
|---|---|---|
| `gateway` | Claude and Codex through an LLM gateway | the gateway token |
| `subscription` | Claude through a proxy, for your own subscription | nothing |
| `langfuse` | traces in Langfuse, for both agents | Langfuse keys, your login |
| `otel` | metrics and logs, for both agents | your work email |

`langfuse` copies two hook scripts, `claude_langfuse_hook.py` and `codex_langfuse_hook.py`.
Put yours next to the configs.

Setup with the gateway:

```sh
uvx harness-hooks install gateway.json langfuse.json otel.json
```

With your own Claude subscription, use `subscription.json` instead of the gateway one. To
mix, add `--agent`. For example, Claude on a subscription and Codex on the gateway:

```sh
uvx harness-hooks install subscription.json langfuse.json otel.json
uvx harness-hooks install gateway.json --agent codex
```

If both are installed for Claude, it uses the gateway token. To switch to the subscription,
remove Claude's gateway settings first:

```sh
uvx harness-hooks remove gateway.json --agent claude
uvx harness-hooks install subscription.json
```

To update, run the same command. After a token change, add `--ask`.

If you added a Langfuse hook by hand, remove it before installing this one. Otherwise each
answer is sent twice. Remove hand-set Langfuse keys too if you want this tool to manage them.
