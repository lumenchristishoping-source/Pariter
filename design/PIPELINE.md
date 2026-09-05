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

## Update — attribution, context pruning, and busy-state fallback

Verbatim, again preserved exactly as written:

> save it pls add that the model response must contain @handles instead its summary like who said what and why not just a plain thing you get. Now one question though if a model reads something and its deleted does the model still remember it cos if yes that'll help cos my plan is to delete every other context md file in the context folder that is not the latest, cos at least the model has previous knowledge and necessarily does not need to read a long thing everytime and it saves time so they dont go searching. Now when asked to retrieve something from memory the model myst reply, why you ask its because before this whole process happens what might be asked has been passed so the model will retrieve from cogen and then respond do you get. agents are mostly there for replying and carrying out work they dont call cogen except when the model is busy and if the model is busy and then a new md file of the humans is passed immediately an agent must take it up and then reply immediately or appropriately to the discussion now you know we share the summaty to all agents for context. Now when the model and an agent is busy and then something is needed another agent takes up the role or whatever is needed whether its to search reply based on a question cos once an md is sent the agent can still decide there nothing to reply to and can still decide to reply or run a command or whatever depending on the content of the md file. once all agents are busy the users all receive a notification that all agents are busy because they'll be able to see what every agent is doing?..

### Plain restatement

1. **Summaries must attribute, not flatten.** The central model's
   summary has to read like "@Atlas said X, @user asked Y, because
   Z" — who said what, and why — never a generic paraphrase that
   loses who's responsible for which point.

2. **Context pruning — corrected reasoning.** Deleting every
   context.md entry except the latest is fine, but *not* because the
   model "remembers" what was in the deleted ones. A model has no
   memory between separate calls — it only knows what's actually in
   front of it at that moment. Pruning is only safe because the
   central model's rolling summary is written to carry forward
   whatever matters from the older entries *into* the latest file
   before those older files are deleted. Anything that didn't make it
   into that summary is genuinely gone for the live agents once
   deleted — which is exactly why the deep archive (Cogen) exists
   separately: it's the full, permanent record underneath the
   short-lived rolling summary.

3. **Memory retrieval goes through the central model.** If a question
   requires pulling something up from further back than the rolling
   summary covers, the central model is the one that queries Cogen and
   replies — not an agent.

4. **Agents normally don't call Cogen** — replying and carrying out
   work is their job, using the shared summary already in their
   context. The one exception: if the central model is busy when a new
   human message arrives, an available agent must pick it up
   immediately (using the context it already has, calling Cogen itself
   if needed) rather than leaving the human waiting on the model.

5. **Deeper fallback.** If both the central model and the
   "expected" agent are busy, a different available agent can step in
   for whatever's needed — search, reply, or otherwise. Any agent that
   receives a new message file independently judges, from its actual
   content, whether there's nothing worth reacting to, or whether it
   should reply, run a command, or take some other action.

6. **All-busy state is visible, not silent.** If every agent is
   genuinely busy, humans get a notification saying so, backed by a
   status view showing what each agent is currently doing — so "why
   isn't anyone responding" is never a mystery.
