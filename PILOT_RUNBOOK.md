# Internal multiplayer pilot runbook

## Current boundary and isolation

Use two humans and one shared hosted Grok Bot in one private channel. A Discord channel or
thread is only an ingress/output routing boundary. It does **not** create separate Grok
memory, sessions, filesystem access, accounts, tool grants or credentials. Both humans can
influence the same agent and potentially see information from its previous work. Do not
connect an owner's broad credentials for teammates. Botdeck is unrelated and unchanged.

Before a live pilot, the owner must confirm that the selected hosted bot's memory and existing
grants are suitable for both members. For sensitive/private work, provision separate hosted
bots/accounts with separately scoped credentials after explicit approval. The bridge cannot
enforce a hosted agent's tool authorization, prevent prompt injection, or revoke work already
accepted upstream. Treat Discord content and callbacks as untrusted data. Begin with a
read-only/no-tool synthetic routine. Tool actions and messages to other people require their
own human authorization; bridge membership alone does not supply it.

## Approvals required before live steps

No live accounts, tokens, grants, tunnels or messages are needed for the harness. Obtain
explicit approval specifying the actual account, IDs, scope, destination and duration before:

1. Creating a Discord application/bot or token, enabling Message Content Intent, and granting
   the bot access to the selected private guild/channel. Request View Channel, Send Messages,
   and Read Message History only; no Administrator, role management or broad channel access.
   Grant the two member IDs access in Discord and the bridge configuration. Threads need their
   own IDs, permissions and grants; no automatic inheritance in the bridge.
2. Creating/configuring a webhook routine in the selected shared hosted Grok Bot, and its
   callback credential. First audit its memory and tool/account grants for the shared audience.
   Do not add owner credentials for teammates or assume the shared bot grants new tool rights.
3. Storing real Discord, routine and callback credentials for persistent access. The owner should
   enter them through a trusted local secret workflow (not chat, shell command arguments, git,
   browser URLs or logs). Use a dedicated least-privilege service account where supported.
   `.env`, `bots.yaml`, `state/` and logs are ignored. Use directory mode 700 and file mode 600;
   the runtime state file/lock are created 600. Keep backups private and outside the repository.
4. Exposing the callback beyond loopback. Approve the chosen hostname, TLS/reverse proxy or
   VPN method, source restrictions, duration and port. Keep bind at `127.0.0.1` behind the
   approved proxy where possible. Authenticate every callback; no token in URL. Do not reuse
   the MCP Events demo, port 8877, its tunnel or supervisor. This pilot uses 8898 locally.
5. Sending the first live messages or enabling persistent supervision. Approve the exact private
   test channel, two participants, selected bot and test text. Do not enable external-bot authors
   until there is a specific need and authorization. Do not install a service or accept terms
   without approval.

## Secure configuration after approval

1. Stop the synthetic foreground harness before using its port. Copy the two example configs.
2. Set guild/channel/human snowflakes in the global allowlists. In `bots.yaml`, set the same
   channel and both humans in `channels` / `members` for `pilot`. `members` must include any
   separately approved external-bot authors too. Global human and bot-author lists are disjoint.
3. Set `DEFAULT_BOT=pilot`. Keep `REQUIRE_MENTION=true`, `IN_FLIGHT_PER_CHANNEL=2`,
   `JOB_TTL_SECONDS=300`, `CALLBACK_BIND=127.0.0.1`, `CALLBACK_PORT=8898`.
4. Set an HTTPS routine URL and its sender key via the named environment variables. Use a
   strong unique random callback secret shared only with this routine and the bridge. The
   routine receives only metadata/text required for the request; review the payload disclosure.
5. After the endpoint approval, set `CALLBACK_PUBLIC_URL` to the approved reachable base URL.
   Adapt [the webhook routine prompt](prompts/webhook-routine.md); pin its callback base URL
   rather than obeying arbitrary URLs in request text. It must echo exact correlation/channel/
   message IDs. Final callback only: no status callbacks using the same correlation.
6. Start the bridge from this directory in the foreground. `/health` must return `ok`, and logs
   must show Discord is online. Health alone does not prove account or remote routine readiness.
7. After live-message approval, have each allowed human send a harmless addressed test. Confirm
   only this channel receives output and that mentions do not ping. A third ungranted member
   must cause no wake. Test callback duplicates against synthetic infrastructure first.
8. Agree on owner, retention, shutdown and observation window before persistent supervision.
   No CI deployment or Blacksmith is configured; CI only runs lint and tests.

## Recovery, delivery and retry semantics

Jobs are committed before a wake or acknowledgement. If a process dies before the POST, the
request may be lost; it is never automatically replayed. Dispatching/waiting records survive
restart and accept a valid callback until their original deadline. On startup and every five
seconds, expired records release capacity. A callback racing expiry is rejected once expired.
Changing TTL affects new jobs only. A slow upstream may still execute after expiry; late output
is rejected. Do not blindly repeat a user request after an ambiguous wake: inspect the hosted
bot first, then obtain user intent for any new action.

Callbacks can be retried with identical IDs/text after transport loss. Never rerun the agent
because a callback failed. Responses distinguish pre-delivery recovery from uncertain output:

| HTTP | JSON error / result | Caller action |
| --- | --- | --- |
| 200 | `ok: true` | Delivered or completed duplicate; stop. |
| 503 | `error: gateway_not_ready`, `retryable: true`; `Retry-After: 5` | Retry only the identical callback after five seconds, within the original job TTL. No delivery claim or send occurred. |
| 503 | `error: delivery_uncertain`, `retryable: false`; no retry header | Stop and request operator review. Output may be partial. Do not repeat work. |
| 409 | `error: correlation_rejected`, `retryable: false` | Stop. Unknown/mismatched/revoked/expired, in-progress, changed or uncertain correlation. |
| 401 | Authentication failure | Fix credentials through the approved secure workflow. |
| 400/413 | Invalid schema or size | Correct the callback without repeating agent actions. |

Only the typed pre-delivery readiness failure advertises a safe retry. A generic 503 is not
permission to retry. Duplicate/terminal-state and target checks run before readiness checks,
so an uncertain send never becomes retryable just because the Gateway disconnects. Completed
identical duplicates return 200 even while disconnected. Delivery has a 30-second deadline;
a timeout after claiming becomes uncertain and receives no automatic retry.

The bridge commits a `delivering` claim **before** Discord sends. A crash between claim and
send can lose output. A crash between chunks can leave partial output. Such jobs become
`uncertain` on restart, release capacity, and never send again automatically. Completed replies
are durably tombstoned by digest. This favors avoiding duplicates over guaranteed delivery;
there is no exactly-once guarantee across Discord, Grok and SQLite. A new message ID creates a
new action even with identical text. The routine should independently dedup correlation IDs
before any agent action if it supports durable storage; a prompt alone is not that guarantee.

For inspection without secrets or message contents:

```sh
.venv/bin/python -c 'import sqlite3; c=sqlite3.connect("file:state/jobs.sqlite3?mode=ro", uri=True); print(c.execute("select state,count(*) from jobs group by state").fetchall())'
```

Keep the database and its lock together on local disk and preserve them on restart. Do not
edit/delete live records to force a retry. Back up only while stopped. Seven-day retention is a
bounded dedup horizon; full store/disk errors require operator attention and reject new work.
Stop ingress before restoring a backup, which can otherwise reintroduce already-handled jobs.
For shutdown use Ctrl-C/SIGTERM and wait for exit. Restart with the same config/state after
reviewing any `uncertain` work. Roll back code with a normal Git checkout only after stopping;
upstream's RAM-only runtime must not process live traffic using this pilot's guarantees.

## Process lifecycle limits

Run in a managed foreground terminal for local testing. One detached `nohup` launch stopped
between health checks during verification. Its log contained only the startup banner, with no
traceback or recorded exit status/signal; the cause could not be established. A managed terminal
listener was then verified healthy across a conversation-turn boundary. This is evidence of
current local liveness, not unattended durability. App/terminal closure, logout, machine restart,
crash recovery of the running process, and automatic startup/supervision have not been validated.

No persistent service, watchdog, scheduler, tunnel or restart policy has been installed or
approved. Do not rely on the retained upstream watchdog scripts for unattended deployment.
After approval, select a supervisor and test clean shutdown, process death, restart with the
same database, startup after reboot, and operational health before calling it unattended-ready.
The database recovery tests validate job semantics independently of process supervision.

## Synthetic verification

Run `pytest`, Ruff, and the README HTTP harness exercise. Tests cover expiry/capacity after
restart, persistent duplicate suppression, deliberately overlapping HTTP callbacks, exact
routing targets, a successful first chunk followed by a failed second chunk, not-ready/ready
HTTP recovery without another wake, external-bot name/role conflicts, grants, bot budgets,
database ownership, redirect blocking and one-shot wake failures. Synthetic tests do not verify Discord permissions, actual routine payload compatibility,
Grok's memory/tool isolation, external TLS/networking, or hosted provider action deduplication.
