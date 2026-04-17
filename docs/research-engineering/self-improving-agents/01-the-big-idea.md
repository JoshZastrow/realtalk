# Lesson 1: The Big Idea

## A story

Imagine you sit down to play RealTalk tonight. You pick the coffee-shop scene, same as yesterday. The character greets you. The opening line feels... familiar. Too familiar. The game has no memory of yesterday's session. It doesn't know that when stakes got high you reached for humor, or that you're working on staying present instead of redirecting.

A blank slate, every time.

Now imagine a slightly different world. You open the game, and before the character speaks, an invisible narrator reminds the model:

> The player tends to deflect with humor when intimacy increases. They've said they're working on that.

The first line lands differently. The game is now a mirror that deepens.

That's the product. The rest of this course is how to build it.

## The wrong turns most teams take first

When people first hear "agent that learns across sessions" they usually jump to one of two places.

**Fine-tuning.** Train the model on transcripts. This is expensive, slow to iterate, and fundamentally the wrong tool — you don't want the model to *internalize* the player, you want to *remind* it of the player. Fine-tuning bakes facts into weights; you want facts to be editable in a text file.

**Vector databases.** Embed everything, retrieve the top-k. This works eventually but is the wrong first step because it hides what's being remembered behind opaque cosine similarity. You can't read a vector. You can't edit it. You can't show it to the user and ask "is this right?"

Both are real tools. Both are probably overkill for your first version.

## The answer, which is less impressive than you'd like

The `hermes-agent` project inside `lib/hermes-agent/` has a working, production-grade learning loop. When you read the code, the thing that surprises you is how *small* it is. The entire mechanism is four moving parts:

1. A **markdown file on disk.**
2. A **tool** the model can call to append to that file.
3. A **frozen snapshot** of that file injected into every turn's system prompt.
4. A **turn counter** that occasionally nudges the model to reflect.

That's it. No embeddings. No database. No reinforcement learning. A few hundred lines of code total, most of it paperwork.

## Why this is a good idea

Three properties fall out of this design that you don't get from the fancier alternatives.

**It's inspectable.** You can literally `cat` the memory file and see what your agent "knows." If it said something weird yesterday, you open the file and find the line that caused it.

**It's editable.** If the model wrote something wrong about the player, you fix it with a text editor. You don't re-train. You don't re-embed. You save the file.

**It's cheap.** The entire learning loop adds one tool definition to your prompt and a few kilobytes to the context. Prompt caching (which we'll talk about in Lesson 2) makes the overhead effectively free.

The uninteresting answer is the right one. Markdown files are the spine of your learning agent.

## The claim

Here's the thesis of this course, stated bluntly so you can disagree with it:

> Ninety percent of the perceived-intelligence gain of a "memory-enabled" agent comes from four ingredients that fit in one file of code. Everything else — vectors, graph stores, scratchpads — is optimization on top.

In Lesson 2 we'll look at each of the four ingredients. In Lesson 3 we'll build it in RealTalk. In Lesson 4 we'll cover the three ways people accidentally ruin it.

Ready? Turn the page.
