# Pariter — Backend Architecture Notes

Working notes on everything the backend needs to cover, beyond the
memory layer (Cogen) and the group-chat dispatcher already discussed.
This is a planning doc — pin down specifics as decisions get made.

## Core pieces

### 1. Auth
Who a user is. Standard identity (email/OAuth/passkey) — not much
Pariter-specific here, so lean on a managed provider rather than
building it (see Hosting below).

### 2. Authorization — separate from auth
Auth tells you *who*; this is *what an agent is allowed to do on a
user's behalf*: run a command, read/write which files, join which
meeting, post in which group. Needed the moment an agent can act
autonomously (per the group-chat "speak at will" design and the
meeting-proxy idea below). Model as scoped, revocable grants per
agent/persona, not a single all-or-nothing permission.

### 3. Deployment of projects
Whatever a user/agent builds inside Pariter needs a path to actually
ship somewhere (a live URL, a running service) — this is a distinct
capability from the agent's own workspace/shell.

**Decision: Pariter uses Vercel for deployment** — not just as a
reference model, as the actual choice. This is a good fit since
Pariter's frontend/client is itself a web app, and agent-built
projects that are web apps/static sites can deploy the same way.
(Anything an agent builds that *isn't* web-shaped — a background
service, a script — still needs the container-based execution side
from the Shells section above; Vercel covers the deployable-project
case, not agent execution itself.)

**How Vercel does it:** deploys trigger off git,
not a manual step. Every push to any branch gets built and deployed
automatically to its own unique, ephemeral URL (a "preview
deployment") — so a PR/branch can be checked live before it touches
production; merging to the production branch is what promotes a build
to the real domain. If two commits land on the same branch while a
build is still running, the running one finishes, the newer commit
queues, and anything queued behind *that* gets cancelled in favor of
whichever commit is most recent — so production always reflects the
latest push rather than working through a backlog.
([Vercel: Deployments](https://vercel.com/docs/deployments), [Vercel: Git](https://vercel.com/docs/git))

Worth copying directly for whatever an agent deploys on a user's
behalf inside Pariter: automatic build-on-push, a disposable
preview URL per change so nothing touches production by accident,
and "latest commit wins" queueing so a burst of agent-driven commits
doesn't pile up a slow backlog of stale builds.

### 4. Remote / distributed work
Agents and users aren't all on one machine. The backend is the thing
that makes an agent's workspace and memory reachable from any device a
person is on — phone, desktop, web — not tied to whichever one started
the session.

### 5. Team / known-people sharing
Sharing a workspace, a conversation, or an agent's output with a team
or specific people — access control at the level of a workspace/group,
not just a single user's account.

### 6. Agent-as-meeting-proxy + prep briefing
An agent joins a meeting in place of the user, then later gives the
user a prep/briefing on what happened. This needs, concretely:
- A bot-join integration with the actual meeting platform (Zoom/Google
  Meet/Teams) — not implied by anything else here, its own piece of work.
- Speech-to-text transcription of the call.
- A summarization pass that turns the transcript into the "prep" —
  reuses the same summarization muscle as Cogen's continuity summaries,
  but running over a meeting transcript instead of a chat history.

### 7. Shells / execution environments for agents
Real command + file access per agent (pillar 4 from the original
README), addressed in detail below since it's the one with a genuine
platform question attached.

## Shells & environments: server-side, not on-device

The question on the table: can we build the shell/environment in
Python and run it standalone on Android (no Termux dependency)?

**Yes, technically** — via **Chaquopy** (embeds a real CPython
interpreter into an Android app through JNI; this is the standard way
to ship Python inside a standalone APK) or **python-for-android** if
the app is Kivy/BeeWare-shaped. Both give a real on-device interpreter
with no separate Termux install.

**But that's the wrong primary answer for Pariter.** Two problems:
- Bundling a full interpreter inflates the APK, and any agent tooling
  that leans on native-extension packages needs Android-cross-compiled
  wheels — a real ongoing maintenance cost.
- Multi-agent coordination (the whole point of Pariter) needs agents
  reachable and running consistently regardless of whether the user's
  phone is on, charged, or even present. An on-device interpreter ties
  an agent's execution environment to one physical device.

**Recommendation:** the phone app is a thin client. The actual
shell/execution environment for each agent runs **server-side** — an
ephemeral or persistent sandboxed container per agent/session, the
same shape this Claude Code environment itself uses. Chaquopy becomes
a fallback tier for offline/low-connectivity use, not the primary
execution backend.

## Other gaps worth flagging

- **Real-time transport** — group chat + "agent speaks unprompted"
  needs a live pub/sub layer (WebSocket or equivalent), not just
  request/response REST.
- **Credential/secrets vault** — an agent joining a meeting or touching
  a calendar needs scoped, revocable tokens to those services, held
  server-side, never on-device.
- **Usage metering/billing** — every dispatcher check and agent turn
  costs tokens; multi-agent group chat multiplies that fast. Needs
  per-user/per-team tracking and rate limits from day one. See
  `design/COST_STRATEGY.md` for the full model/infra cost plan (tiered
  routing, caching, context compression, batching).
- **Audit log** — if an agent can act as a user's proxy (meetings,
  commands), there needs to be a record of what it actually did, both
  for accountability and so the "prep" summary itself is trustworthy.
- **Cross-device session continuity** — Cogen's memory is
  local-SQLite-first. If the same agent identity has to be consistent
  across phone/desktop/web, that needs a canonical server-side store,
  with local mode as an offline cache, not the source of truth.

## Hosting & cost strategy

Flagging the cost concern directly: the expensive part isn't auth or a
database, it's the **agent shells** — spinning up real execution
environments per agent. The fix is not running always-on VMs per
agent; it's using infrastructure that scales to zero and bills
per-second of actual use.

A cheap-to-start stack, each piece replaceable later without a rewrite:

| Need | Cheap-start option | Why |
|---|---|---|
| Agent shells/sandboxes | **E2B** or **Fly.io Machines** | Built for (or well-suited to) short-lived AI-agent code execution — scale-to-zero, pay per second, no idle VM cost |
| Auth | **Supabase Auth** or **Clerk** (free tier) | Don't build this — generous free tiers cover early usage |
| Database (canonical memory/state) | **Supabase** or **Neon** (Postgres, free tier) | Managed Postgres, no ops burden, cheap at low volume |
| File/workspace storage | **Cloudflare R2** | S3-compatible with no egress fees — matters once agents move files around |
| Real-time transport | Supabase Realtime, or self-hosted Redis/NATS on one small box | Start managed; self-host once volume justifies the ops cost |
| Push notifications | Firebase Cloud Messaging | Free, standard for Android |

This is a starting point, not a final decision — worth revisiting once
we know rough scale (just you, a small team, or public users) since
that changes which tier stops being "free."
