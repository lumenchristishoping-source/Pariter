# Pariter — Model & Infra Cost Strategy

Why this doc exists: token costs for AI coding agents have become a
real, documented problem industry-wide, and Pariter's whole premise
(multiple agents, standing in a group, potentially speaking
unprompted) is exactly the shape of usage that makes it worse if left
unmanaged. This is the plan to not become another cost horror story.

## The problem, with numbers

- Gartner: AI coding agent costs are on track to overtake average
  developer salaries by 2028, driven by token-based, consumption-style
  pricing. ([Computer Weekly](https://www.computerweekly.com/news/366645054/Gartner-AI-coding-agents-will-cost-more-than-real-developers), [The Register](https://www.theregister.com/ai-and-ml/2026/06/24/ai-coding-agents-could-soon-cost-more-than-the-developers-using-them/5260864))
- Real bills reported jumping from $20–100/developer/month to
  $2,000–5,000/month, with extreme cases hitting $20,000 — Uber
  reportedly burned its entire annual AI-coding budget in four months.
  ([The Register](https://www.theregister.com/ai-and-ml/2026/06/24/ai-coding-agents-could-soon-cost-more-than-the-developers-using-them/5260864))
- **The bill is mostly input tokens, not output** — re-consuming
  context (system prompts, repo maps, chat history) on every call,
  not the code actually written. ([Atlas Cloud](https://www.atlascloud.ai/blog/guides/reduce-ai-coding-token-cost))
- Agentic workloads burn far more tokens than plain chat, because
  every tool call, every re-read of context, and every agent-to-agent
  handoff re-pays that same context cost. ([LeanOps](https://leanopstech.com/blog/agentic-ai-cost-runaway-token-budget-2026/))

That last point is the one that matters most for Pariter specifically:
a multi-agent group chat where several agents might all be evaluating
whether to respond to the same message is, structurally, the exact
pattern that runs up the worst bills if built naively.

## What actually works (industry data)

- **Model routing/cascading**: send easy requests to a small/cheap
  model, escalate only the hard ones to the frontier model. RouteLLM's
  published numbers: ~95% of frontier-model quality while routing only
  14–26% of calls to the strong model — a 75–85% cost cut on routed
  traffic. ([Digital Applied](https://www.digitalapplied.com/blog/llm-model-routing-2026-cost-quality-optimization-engineering-guide))
- **Prompt/response caching**: cache repeated context and repeated
  answers instead of re-paying for them every call.
- **Context compression**: trim what actually gets sent — don't ship
  the full history when a scored subset carries the same signal.
- **Combined effect**: production teams report 60–80% total bill
  reduction when routing + caching + batching are all applied
  together; 20–40% if traffic is uniformly hard/unique (less to route
  away from). ([GMI Cloud](https://www.gmicloud.ai/en/blog/llm-inference-cost-optimization-caching-batching-routing))
- **Governance basics** (cost tagging per request, budget alerts) give
  15–25% reduction on their own, independent of any technical change.
  ([GetMaxim](https://www.getmaxim.ai/articles/reduce-llm-cost-and-latency-a-comprehensive-guide-for-2026/))

## Pariter's answer: the pipeline *is* the cost-control layer

Superseded the earlier tiered (Tier 0/1/2) per-agent gating idea — it
depended on agents having pre-assigned topics, which don't actually
exist. The current design (full spec in `design/PIPELINE.md`) gets the
same cost shape a different way:

1. **One central model, on a timer.** At each cycle, a single central
   model reads what's been said (humans + agents) since the last
   cycle, writes a summary, and pushes that summary to every agent's
   context — cheap relative to N separate per-agent model calls,
   because it's one call, not N.
2. **Only 2 agents get a real question per cycle**, picked in
   rotation, each asked a different question derived from the summary.
   Every other agent just receives the summary update and
   acknowledges it (no generation needed).
3. **Rotation, not selection-per-message.** Because it's 2 agents per
   *cycle* (not per message), the expensive full-response call count is
   bounded by the cycle rate, independent of how many agents (N) are
   actually in the room.

This means the number of expensive full-response calls is bounded by
the cycle — 2 per tick — not by how many agents exist in the room.

### Context: send the scored subset, not the whole history

Cogen's retrieval already does this for memory (last 15 messages +
up to 8 scored extras, not the full transcript) — reuse it as-is for
the group chat's context window too, rather than re-inventing
compression. This directly targets the "most of the bill is
re-consumed context" finding above.

### Caching

- **Prompt caching**: the persona/identity block (Tier 2) and the
  memory/context block are both stable across many calls in a short
  window — cache them at the API level instead of re-sending/re-billing
  identical context every turn.
- **Response caching**: near-duplicate questions in a group (someone
  asks what's already been answered) should hit a semantic cache
  before triggering a new model call at any tier.

### Batching for non-interactive work

The meeting-prep summarization pass (BACKEND.md) doesn't need a live,
synchronous call — batch it. Batch APIs are meaningfully cheaper for
anything that isn't blocking a human waiting in a chat.

### Governance, from day one

- Every model call tagged with agent id, tier, and triggering
  user/team, for real cost attribution (not just a total bill).
- Per-user and per-team budget caps with alerts, enforced at the
  dispatcher level — before Tier 2 spend happens, not after the
  invoice arrives.
- A visible cost/usage view per team, so "which agent is expensive"
  is answerable without spelunking logs.

### Infra side (containers, not just model calls)

Same logic applies to the agent shells discussed in `BACKEND.md` and
detailed in `design/PIPELINE.md`: each agent's temporary environment
dies immediately once its work is done, so scale-to-zero sandboxes
(E2B / Fly.io Machines) mean idle agents cost nothing — which matters
a lot once "how many agents are in the room" stops being a small
number.

## Net effect

Combining the pipeline's rotation (2 full-response calls per cycle,
regardless of N) with prompt caching, context compression, and
batching is exactly the "routing + caching + batching together"
combination the data above shows getting 60–80% bill reduction —
applied to Pariter's actual bottleneck (many agents, one room) instead
of a generic single-agent coding assistant.
