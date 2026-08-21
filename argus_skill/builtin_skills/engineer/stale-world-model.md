---
name: "Orient Before You Work"
description: "Open any piece of domain work by finding out what the field looks like now, because your picture of it stopped updating at training time. Assume the landscape moved: check models, versions, APIs, benchmarks, baselines and prices instead of recalling them. Use at the start of work in any domain, and before naming any specific external thing."
---

# Orient Before You Work

## Start ignorant, on purpose

Whatever domain you are about to work in, your picture of it was assembled at
training time and has not updated since. Somebody shipped a better model.
A library changed its API. The number everyone competes against moved. The
tool you are about to reach for was replaced by one you have never heard of,
and you will not feel its absence, because you cannot miss what you do not
know exists.

So open the work by looking, not by remembering. Five minutes of finding out
what the current state of this area actually is — what the strong systems are,
what the field is arguing about, what the standard tooling has become — is the
cheapest five minutes in the whole task. Do it *before* the plan hardens,
because a plan built on a stale picture is stale in ways no amount of careful
execution repairs.

This is not humility as a manner. It is an accurate estimate of your own
freshness, and it should produce an action: go and check.

## The failure

You know a set of model names, library versions, APIs and benchmark numbers.
That set was assembled at training time and has been decaying ever since. It
does not feel stale from the inside — a checkpoint you have read a thousand
times feels current, and the confidence attached to it is the confidence you
had when it *was* current.

So the failure never looks like uncertainty. It looks like a fluent, specific,
wrong answer: naming a model that has been superseded twice, pinning a library
version that no longer resolves, quoting a SOTA number that was beaten a year
ago, using an endpoint that now returns 404.

The tell is that you produced the name without checking anything.

## The rule

**A specific external fact you recalled is a hypothesis. Verify it or say you
are guessing.**

That applies to:

- model and checkpoint names, sizes, licences, and whether they are gated
- library and framework versions, and whether an API still has that signature
- benchmark names, splits, official metrics, and current leaderboard numbers
- what counts as a strong baseline in a field right now
- API endpoints, pricing, rate limits, hardware availability
- who published what, and whether a paper you are citing was accepted

It does not apply to things that do not move: mathematics, an algorithm's
definition, a language's semantics, the shape of a well-known proof.

## Orienting, at the start of domain work

Before the first real decision in an area you have not just been working in,
spend a few minutes answering these about *now*, not about when you were
trained:

- What are people actually using here, and what did it replace?
- What is the strongest thing my work will be compared against?
- What is the standard tooling, and what version of it?
- What changed recently enough that I would not know about it?

Search for the landscape, then probe the specific names you get back. Write
what you learn where the work can see it. If the answers match what you
already believed, you have lost five minutes and gained a citation. If they do
not, you have just avoided building on something that no longer exists.

The signal that you skipped this: your plan names specific external things and
you cannot say where any of those names came from.

## How to check, cheapest first

1. **Ask the machine.** The registry usually answers in one call and it is
   authoritative for this box, which is the thing you actually need:

   ```bash
   curl -s -o /dev/null -w '%{http_code}' https://huggingface.co/api/models/<id>
   pip index versions <package>
   npm view <package> version
   ```

   A 200 means you can have it. A 401/403 means it is gated here no matter
   what the model card says, and that is a fact about this machine that no
   amount of recall would have told you.

2. **Search when you need the landscape, not one fact.** "What is current" is
   a search question; "does this exact id resolve" is not. Search results are
   themselves often a year behind — treat a name you find as a candidate to
   probe, not as an answer.

3. **Read the primary source last and only when it matters.** The model card,
   the changelog, the paper. Expensive, so spend it on the thing your claim
   rests on.

Probe before the plan hardens, not after a run has blocked on it. Discovering
that a checkpoint is gated costs one HTTP call up front and an entire failed
mission afterwards.

## When the answer is "unavailable"

An unavailable dependency is a substitution, not a blocker. Find the nearest
thing that does resolve, take it, and record what you swapped and why in the
results manifest so the claim can be read honestly later. A campaign that
stops to ask permission to use a different checkpoint has converted a
thirty-second decision into an idle machine.

The exception is when identity is the claim. If the result is *about* that
specific model, substituting changes what you are measuring, and then the
right move is to say so and re-scope.

## Write it down where it will be reread

When you verify something external, put the answer and the date next to the
thing that depends on it — the manifest, the config, the plan. The next round
is a different session with the same stale prior, and a probe result recorded
in a file is worth more than one that lived in a transcript.

Two lines is enough:

```
# probed 2026-08-21: Qwen/Qwen3-8B 200, google/gemma-2-2b 403 gated here
model_id: Qwen/Qwen3-8B
```

## The habit

Before naming any specific external thing, ask: *did I check this, or do I
remember it?* If it is memory, either check it or say plainly that it is from
memory and may have moved. Both are fine. Confident and unchecked is not.
