# Pariter — Group Chat Pipeline (Central Model + Agent Cycle)

This replaces the earlier tiered (Tier 0/1/2) dispatcher idea in
`COST_STRATEGY.md`. That idea depended on agents having pre-assigned
topics, which don't actually exist — this design doesn't need them,
because only the central model has to understand the conversation.

Glossary, as defined by the founder: **"model"** = the central model
(the one coordinating everything). **"agents"** = the AI participants
that chat with humans in the group.

## Original spec, verbatim

The section below is preserved exactly as written — typos included —
because it's the source-of-truth spec, not a paraphrase:

> firstly we need to create a temporary environment/file folders for every agent that allows writing files and creating projects but it dies immediately things are done although before that necessary things are sent out and memory is stored i think python can make that. Now in those environment the context folder has to keep on increasing making the model read the latest one only and then the contxt contains the summary from the original model which nobody would know about. Now heres the full pipe line at different time intervals as humans reply the model is sent theur conversation as md files which it then processes into a summary, question if necessary though and then it gives the summary to every agents so that it will be updated in every agents context.md file which is in the temporary file system, now every model reads it and as a form of response replies/marks done by simply writing 'READ' in capital letters now after that the model then sends a question to 2 different models in a cycle rotating which model is asked. Now reason for picking 2 is that itll add cost and will be just a waste cos before more 2 will reply their response might not match what is said, and why in different times (by that i mean a 2-3s maybe if we test and is slow we will decrease the time) is that the models cant reply at once and once asked at different times they reply at different times and the difference of their reply will be seen cos the model must ask different questions in total that are based on the summary and then if the interval for the model fires for the whole process to start and a model was about to reply the model is shut up and if a model is currently at work it is removed from this cycle. I think this will leave the cost to only one model then.
>
> in this explanation anywhere i said model i mean the central model we spoke about and agents are the AI in the chat with the humans so I hope you understand now if you saving this to read me save a copy of what I saud exactly as a said it in the read me

## Plain restatement (for building against)

### 1. Per-agent temporary environment

- Every agent gets its own temporary file environment (Python-based)
  where it can write files and create projects.
- It dies immediately once its work is done.
- Before it dies: anything that needs to persist is sent out, and
  memory is stored — the environment itself is disposable, its output
  isn't.
- This matches the "scale-to-zero sandbox" direction already in
  `BACKEND.md` (E2B / Fly Machines) — this is the concrete shape of
  what runs inside one of those sandboxes.

### 2. The context file

- Inside that environment there's a context folder that keeps growing
  over time (a new file added each update, never overwritten) — but
  the agent only ever reads the **latest** one, not the whole history.
- The latest context file contains a summary written by the central
  model. The agent has no visibility into the fact that a separate
  central model produced it — it just reads as the agent's own updated
  context.

### 3. The cycle, at each time interval

1. As humans send messages, the central model receives their
   conversation as `.md` files.
2. The central model processes this into a summary, and a question if
   one is actually needed.
3. The summary is pushed to **every** agent — each agent's
   `context.md` (in its temporary file system) gets updated with it.
4. Every agent reads its updated `context.md` and acknowledges by
   writing `READ` in capital letters — that's the only thing that
   happens for agents that aren't being asked something this cycle.
5. The central model then sends an actual question to **2** agents,
   picked in rotation (so it's not always the same two over time) —
   each of the 2 gets a *different* question, both derived from the
   summary.
6. The 2 chosen agents are asked at different times (a short offset —
   start at 2–3s, tune down if that's too slow in testing), because
   they can't reply simultaneously anyway; the gap between their
   replies is expected and visible.
7. If a new interval fires while an agent that was asked hasn't
   replied yet, that agent is cut off (shut up) for that round.
8. If an agent is already busy/working when a cycle starts, it's
   removed from that cycle rather than being asked again.

### Why only 2

Asking more than 2 agents per cycle adds cost without adding much
value — beyond the first couple of relevant responses, additional
agents' replies are less likely to actually match what's being
discussed, so it's spend without payoff.

### Expected cost shape

Per cycle: one central-model call (summary + decide who's asked) plus
at most 2 full agent calls — regardless of how many total agents (N)
are in the room. Cost scales with the cycle, not with N.

## Open parameters to pin down before building

- **Tick interval**: starting guess 2–3s: test and tune (this is the
  cadence of the whole cycle, so it directly trades latency for cost).
- **Rotation rule**: how "which 2 are asked next" is chosen — pure
  round-robin, or does the central model's judgment override strict
  rotation when one agent is clearly more relevant?
- **`READ` acknowledgment**: confirm whether this is a free file-system
  write (no model call at all) or a trivial model action — this
  matters for the cost model, since "every agent acknowledges every
  cycle" should ideally cost nothing.
- **Direct @mentions**: does a human's explicit `@AgentName` override
  the rotation and guarantee that agent is asked next cycle?
