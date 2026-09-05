# Pariter

*Pariter* (Latin: "equally, together") — a platform where humans and AI
participate in ongoing work and discussion as peers, not as a user issuing
one-off prompts to a stateless tool.

## Why

Today's AI agent tooling is fragmented across the things that make someone
feel like a peer rather than a tool:

- **Memory** — most tools keep context only within a session; genuine
  multi-month recall of a relationship or project is rare.
- **Group presence** — agents in chat are almost always gated behind an
  explicit @mention; they don't get to contribute unprompted the way a
  colleague would.
- **A place of their own** — agents rarely get a durable, personal
  workspace that survives across sessions the way a human's home directory
  does.
- **Real agency** — command execution and file access exist in coding
  tools, but aren't combined with persistent identity and standing group
  membership.

Pariter's goal is to bring all of this together in one place, and to
connect it to **Cogen** (MCP) as the underlying integration layer.

## Core pillars

1. **Persistent memory** (handled) — agents retain context across sessions
   over long time horizons (3+ months), not just within a single
   conversation.
2. **Free-will group participation** — agents can speak in a group
   discussion without being explicitly tagged, while users retain the
   ability to decline or mute a given agent's contribution.
3. **Per-agent persistent workspace** — each agent has its own durable
   folder/workspace that carries forward across sessions, rather than a
   throwaway sandbox.
4. **Real execution access** — agents can run commands and read/write
   files to actually do the work being discussed, not just talk about it.

## Group participation design (in progress)

The current working idea for pillar 2:

- A dedicated dispatcher model sits in the background, invisible to users.
  It receives the live conversation plus each agent's context (md files)
  and decides, per turn, which agent(s) — if any — should be given context
  and triggered to respond.
- Only the dispatcher's decision is hidden; a triggered agent's reply
  appears in the group like any other participant's message.
- Users can still veto or mute a response before or after it's given.

Open questions still to settle:

- **Centralized vs. decentralized gating** — one dispatcher judging
  relevance for every agent, vs. each agent running a cheap self-check on
  whether it has something to add.
- **Noise control** — confidence thresholds, per-agent cooldowns, and a
  cap on how many agents can respond to a single human turn.
- **Veto timing** — cancel before generation (needs near-instant UI) vs.
  discard after generation but before posting.

## Integration

Pariter connects to **Cogen** via MCP. Integration details TBD as the
Cogen interface is defined.

## Status

Early design phase. Architecture and integration details in this README
will evolve as decisions are made.
