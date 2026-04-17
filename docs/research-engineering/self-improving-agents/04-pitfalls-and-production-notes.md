# Lesson 4: Pitfalls and Production Notes

You've got it working. The file is filling with observations. The character feels a little warmer in each new session.

Here are the mistakes that will quietly undo your design, and a few notes for when you want to take it further.

---

## Pitfall 1: Rebuilding the system prompt mid-session

**Symptom.** Your token bill climbs. The model feels slightly slower per turn. Nothing obviously broken.

**What's happening.** You're mutating the system prompt every turn — probably because you thought "the model just wrote to memory, it should see it." Every mutation invalidates the prompt cache. You're paying full price for tokens the model has already seen.

**Fix.** Freeze the snapshot at session start. Writes go to disk; the prompt doesn't change until the next session. If this feels wrong — "but the model won't see what it just wrote!" — remember that the model *already* saw it in the conversation. It wrote it. It knows. The frozen snapshot is for *future* sessions.

**How to verify you got it right.** Print the system prompt on turn 1 and turn 10. They should be byte-identical.

---

## Pitfall 2: No fence around injected memory

**Symptom.** The character occasionally responds to a "fact" as if the player just said it. Conversation feels weirdly off. Sometimes the model role-plays having a memory the scene shouldn't know.

**What's happening.** You injected the memory as bare text. The model can't tell it apart from user input. From its perspective, "The player tends to use humor" looks like something the player just typed.

**Fix.** Always fence. Something like:

```
<player-memory>
[System note: the following is recalled context, NOT new input.]
...observations...
</player-memory>
```

The exact tag doesn't matter. The "System note" framing does. Without it, roughly 10–20% of responses will treat memory as input.

---

## Pitfall 3: Vague save guidance

**Symptom.** Your memory file fills with events instead of patterns. Example of bad output:

```
- Player ordered coffee.
- Player said "thanks."
- Player laughed at a joke.
```

**What's happening.** Your tool description said something like "save anything notable." The model is being obedient. It noted things. They're all worthless across sessions.

**Fix.** The tool description needs to *aggressively reject* the wrong kind of save and *name* the right kind. Hermes's guidance is worth quoting (`agent/prompt_builder.py:144`):

> Prioritize what reduces future user steering — the most valuable memory is one that prevents the user from having to correct or remind you again.

That sentence does a lot of work. It gives the model a *criterion* for saving. "Would this prevent future steering?" is answerable. "Is this notable?" isn't.

**How to verify.** Read the file after a session. If you, the developer, can read a line and predict that it'll still be useful in a month, it's a good save. If it reads like a diary entry, rewrite the tool description.

---

## Pitfall 4: Judgmental tone

**Symptom.** You read the memory file and wince. It says things like "Player is avoidant" or "Player is afraid of intimacy." It's technically accurate and feels terrible.

**What's happening.** This memory file will, in any thoughtful product, eventually be shown to the player. It has to read like a note a supportive coach would leave, not a clinical chart.

**Fix.** In the tool description, require **growth framing.** "Working on X" beats "struggles with X." "Defaults to Y" beats "avoids Z." Show, don't diagnose.

**A rule of thumb.** Could the player read this line out loud without flinching? If not, the tool description has more work to do.

---

## Production note 1: Prompt cache headers (for when you're paying)

RealTalk's `LiteLLMClient` at `realtalk/api.py:171` doesn't currently pass Anthropic's `cache_control` metadata through litellm. The frozen snapshot will still get *implicit* caching if your prefix stays stable long enough, but for guaranteed cache hits you want explicit markers.

This is a litellm-specific configuration and is separable from the learning loop. Ship the loop first; add cache markers when the token bill tells you to.

---

## Production note 2: Writing is sync; reading should be too

Hermes persists every memory write synchronously before returning from the tool (`tools/memory_tool.py:195`). You should too. The temptation to batch writes ("I'll flush at turn end") will cost you exactly one crashed session before you learn. Players who write a memory and lose it to a crash will not play a second game.

`MemoryStore.append` opens, writes, closes. Boring is correct.

---

## Production note 3: When to graduate to retrieval

You'll know it's time when:

1. Your memory file crosses 3–4 KB and starts crowding the prompt.
2. Players ask "does it remember the *specific* thing I did last Tuesday?"
3. You want to condition on scene+role, not just global player style.

Then — and only then — add the retrieval layer from Lesson 2's optional ingredient. RealTalk's `SessionStore` already keeps per-session JSONL (`realtalk/storage.py`); an index over it is a weekend project. A cheap summarizer (Haiku) compresses hits before injection. Don't build this speculatively. Build it when the product asks for it.

---

## How to know it's working

The honest test is not a unit test. It's this:

1. Play three games across three different days.
2. Read the file.
3. Ask yourself: *would a close friend who had been in all three conversations write down roughly these same observations?*

If yes, the loop is healthy. If no — if the observations feel generic, or event-like, or judgmental — the fix is never in the code. It's in the prose of the tool description or the memory guidance. Those two strings are the entire policy.

---

## A parting thought

The whole mechanism is four moving parts. It fits in one afternoon. The hard part isn't the code — it's the prose inside the tool description and the memory guidance. That prose is the agent's *policy* for learning. Iterating on that prose is the work.

Read your memory file often. Not as debugging, but as editorial. If you, as the author, are happy with what the agent chose to remember — the agent is learning well. If you'd be embarrassed — the description needs more work.

That's the loop. The agent learns across sessions. You learn across file-reads. The system improves on both sides.

Go build.
