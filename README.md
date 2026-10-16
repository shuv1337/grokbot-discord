# shuvbot: internal Discord pilot

A hardened fork of [RudeDude/grokbot-discord](https://github.com/RudeDude/grokbot-discord),
based on upstream `7d096fc` (including source revision `9ff7e07`). Upstream authorship,
history, and [MIT license](LICENSE) are preserved. Upstream comparison notes remain in
[COMPARISON.md](COMPARISON.md).

The pilot is two explicitly granted humans and one shared hosted Grok Bot in one private
Discord channel. This is a bridge to hosted Grok webhook routines, not an independent model
API or a per-user agent sandbox. See [PILOT_RUNBOOK.md](PILOT_RUNBOOK.md) before live setup.

## Account-free local run

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-pilot.txt '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/python -m grokbot_discord.synthetic --port 8898
```

In a second terminal:

```sh
curl --fail http://127.0.0.1:8898/health
curl --fail -H 'Content-Type: application/json' \
  -d '{"id":"10001","author":"301","text":"pilot ping"}' \
  http://127.0.0.1:8898/simulate
curl --fail http://127.0.0.1:8898/outbox
```

The harness binds only `127.0.0.1`, reads no live config, logs in to no account, and uses
fake Discord members `301` / `302`, guild `100`, channel `200`, and a local fake Grok
endpoint. `/outbox` shows synthetic acknowledgements and replies, bounded to 100 entries.
The literal `synthetic-only` token is a public test fixture, never a production credential.
Restarting retains dedup records in `state/synthetic.sqlite3`; use a new message ID for
another test. Stop with Ctrl-C. The synthetic HTTP ingress/outbox routes do not exist in
the live server. No tunnel or system service is installed.

## Pilot behavior

- Exact guild, channel, human and external-bot allowlists, plus per-hosted-bot member/channel
  grants. No wildcard grants, DMs, webhook messages, implicit thread inheritance, or self wakes.
- SQLite correlations and message dedup survive restart. Accepted/ambiguous wakes expire
  after `JOB_TTL_SECONDS` (default 300). A 5-second sweep releases expired capacity.
- One wake POST per accepted message. No automatic remote-action retries, including after
  HTTP errors, redirects, timeout, crash or restart. Unknown outcomes await a callback or
  expire; they cannot safely be declared unaccepted. Timeout cannot cancel hosted agent work.
- Callback bearer authentication, constant-time comparison, strict field types/limits, exact
  stored target matching, current grants rechecked, unknown/expired targets rejected (409).
- Repeated completed callbacks with identical text return success without sending again.
  In-progress, changed, expired or uncertain callbacks return 409. A send failure returns 503;
  with `error: delivery_uncertain`, `retryable: false`; its job is uncertain and further sends
  are suppressed. Pre-delivery Gateway unavailability instead returns `gateway_not_ready`,
  `retryable: true` and `Retry-After: 5`, leaving the job available for an identical callback.
  Discord sends and SQLite commits
  cannot be atomic; see the runbook for possible loss/partial output.
- All output disables user, role, everyone, and reply mentions. At most 7600 characters and
  four chunks per reply, 8000 characters per request. Empty output is rejected; send a concise
  result instead. Embeds/link previews are not sanitized: use Discord channel settings if needed.
- Default two outstanding jobs/channel, ten accepted wakes/channel/minute, and one accepted
  wake/external-bot/channel/minute. External bots are off by default and resolve only the
  leading name/alias; role mentions cannot override that target and defaults are unavailable.
- Metadata only in SQLite: IDs, selected bot, timestamps, status, and reply digest. No message
  body, reply text, token, or webhook URL. Terminal records are retained seven days; 10,000
  records is a hard cap that rejects further work until retention frees space. Monitor disk.
  Discord redelivery older than retention can be treated as new work.

Only one bridge process may open a state database (POSIX file lock). Keep it on local disk;
this is not a multi-replica queue. `/health` is process liveness, not Discord/Grok readiness.
Local foreground execution is tested; unattended startup and supervision remain unvalidated.
See the runbook’s process lifecycle limits, including the unexplained detached-launch exit.

## Live configuration

After the approvals described in the runbook, copy `.env.example` and `bots.example.yaml`
to ignored `.env` and `bots.yaml`, set exact IDs and local secrets, then run:

```sh
.venv/bin/python -m grokbot_discord
```

`pilot ping`, `@pilot ping`, bridge mentions with a configured default, and mapped roles
route to a configured hosted bot. `REQUIRE_MENTION=true` is recommended. Ambiguous role
selection fails. Missing member/channel grants, missing credentials, duplicate routing aliases
or roles, empty allowlists, invalid TTL/capacity/port, or non-HTTPS hosted webhook URLs fail
configuration. `DRY_RUN=true` refuses live startup; use the synthetic harness instead.

`watch.sh`, `ensure-up.sh` and the upstream keep-alive prompt are retained for attribution and
reference. They are not installed or enabled by this pilot. Use the foreground process until
persistent supervision has been separately approved. Never run two replicas against different
copies of the same database: both could act on a Discord event.
