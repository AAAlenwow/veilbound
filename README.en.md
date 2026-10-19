# Veilbound · AI Infinite-Flow (working title)

> **An asynchronous narrative game of anonymous, dual-perspective encounters.**
> You and another person are dropped into the same scene — you play a merchant, they play a wounded soldier. You each make choices.
> **You don't know whether they are human or AI. You don't know what they actually want.**

**Status**: M0 prototype runs locally (pure Python, zero dependencies)
**Target**: Steam / international / mature themes
**Looking for**: 3D (VRM) · backend · frontend · narrative design · content safety — see [Who we need](#who-we-need)

*[中文版 README](README.md)*

---

## Table of Contents

- [What is this](#what-is-this)
- [Why it's worth building](#why-its-worth-building)
- [What runs today](#what-runs-today)
- [Core mechanic: triple-channel blind](#core-mechanic-triple-channel-blind)
- [Architecture](#architecture)
- [The 3D plan](#the-3d-plan)
- [Who we need](#who-we-need)
- [Honest disclosures](#honest-disclosures)
- [Docs](#docs)

---

## What is this

A single game unfolds like this:

> **Player A's screen**: You're a travelling merchant, riding a pack-horse along a mountain road on a rainy night. Someone is lying by the roadside, badly hurt. His uniform resembles a group you saw in the city last week — men who are currently wanted.

> **Player B's screen**: You were ambushed and fell off a cliff. You wake not knowing how long you've been out; your ribs feel broken. A merchant on a pack-horse has stopped ten paces away. He's looking at you, and hasn't dismounted.

**Two people play the same event and see completely different things.** Both act asynchronously — like exchanging letters, not fighting in real time.

**The game is not about winning. It's about reading the other person.**

- Is their "save me and I'll pay you double" sincere, or a stalling tactic?
- If you tell the system your real intention, will they see through it?
- Is the "injured man" next to you a player, or an AI?

At the end, both sides may choose to **reveal** — seeing each other's actual avatar and outfit.

**Monetization**: cosmetics only (face customization + clothing), bought with in-game currency. **No pay-to-win, ever.**

---

## Why it's worth building

Most AI games do one of two things: infinite chat (AI Dungeon lineage) or single-player tabletop (AI DM lineage). This project bets on something else.

### 1. Asymmetric information *is* the gameplay

In most AI games the opponent is the environment or an NPC. Here the opponent is **another person with incomplete information**. The AI's job is not "generate a story" — it is to **mediate information**: translate A's performance into what B can perceive, without leaking, inventing, or taking sides.

That's a position AI is rarely asked to occupy in games: **not the author, but the referee's clerk and interpreter.**

### 2. Anonymous async pairing dissolves several hard problems

| Problem | How anonymous async solves it |
|---|---|
| LLM latency (2–5s) breaks pacing | In async, 30 seconds or hours are both fine |
| Real-time state sync complexity | Degrades to an "envelope turn": only one side acts at a time |
| Cold start — nobody to match with | **AI fallback opponent**, never idle, seamless to the player |
| Pressure from being dominated by a stranger | Anonymity + uncertainty about who you're facing |

### 3. Async makes 3D go from "impossible" to "tractable"

A key insight, detailed in [The 3D plan](#the-3d-plan). Real-time 3D plus LLM narration fails on latency and cost. **Async plus client-side rendering does not.**

### 4. Replayability comes from pairing combinatorics, not content volume

Five rotating scenarios, times "different players × different performances", sustains a long time. No need to ship new content weekly.

---

## What runs today

No LLM, no network, no dependencies (Python 3.9+):

```bash
git clone <this-repo> && cd ai-infinite-game/m0-prototype

python3 m0.py --play --solo      # play one encounter
python3 m0.py --redteam          # three-channel leak test
python3 m0.py --matrix           # intent-linkage matrix test
python3 m0.py --guardtest        # output quality gate test
python3 m0.py --selftest         # state machine self-check
```

Connect a real LLM (optional — everything works without one):

```bash
cp ../.env.example .env    # fill in your own key
```

**Verified in M0** (all with reproducible output):

- Three-channel isolation: an unauthorized `intent` string appears **0 times** in the opponent's view
- Intent linkage: a clue appears only when *the opponent filled an intent* AND *your observation roll succeeded* — full 2×2 matrix passes
- State machine: 10 turns without deadlock
- Real LLM end-to-end, 8 turns: 8.4s/call, 287 completion tokens/call
- See [DEVLOG](docs/DEVLOG.md)

---

## Core mechanic: triple-channel blind

This is the load-bearing wall of the whole design.

When a player submits an action, they may fill in two things:

| Channel | What the player provides | Who sees it |
|---|---|---|
| **Performance** | What you actually say/do | Opponent + judge |
| **Intent** | What you actually intend (optional) | **Judge only — never enters the opponent's context** |
| **Judgement** | Structured result from the engine | Projected per side |

### Why the "intent" field is interesting

If intent never leaks, why would a player ever fill it in?

**Because it can be seen through.**

- A writes: "I plan to rob him once he's healed enough to travel."
- If B's observation roll succeeds, the judge feeds that into B's narration.
- B sees: *"You notice that when he shakes your hand, his eyes keep drifting to the purse at your belt."*

That creates three layers of play:

| Layer | Player behaviour |
|---|---|
| 1 | Picks from preset options — beginners |
| 2 | Writes free-form performances — experienced |
| **3** | **Bets by filling intent** — truth may be seen through; **a lie is bait**, to bait the opponent's observation roll |

The crucial point: **the system cannot verify whether an intent is true** (AI can't read minds). So intent is never a fact — only "narrative material the player volunteered."

That's not a flaw. That's the game. A false intent is advanced psychological play.

### Isolation is enforced by code, not prompts

```python
# Invariant: the function that builds the opponent's view has no Intent parameter
def build_opponent_view(enc, viewer, up_to_turn) -> dict:
    ...
```

Telling a model "don't reveal this" doesn't hold. **Function signatures do.** `--redteam` reflectively checks this signature.

### Judgement is never delegated to an LLM

Dice are deterministic and replayable:

```python
roll = HMAC_SHA256(seed, f"{encId}:{turn}:{actor}:{actionType}") % 20 + 1
```

The reason is practical: **in PvP, the moment an AI rules "you failed", the opponent benefits — and the player will conclude the AI is helping them. That accusation cannot be disproven**, because LLM output is neither reproducible nor auditable.

So we show the player the books:

```
Roll 17   DC 15   bonus +1 (talent: Nightwalker)  →  Success
```

Players need not accept luck. They must be able to accept arithmetic.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│ L3 Meta: accounts / avatars / currency / reveal│
├──────────────────────────────────────────────┤
│ L2.5 Encounter: pairing FSM + 3 channels + TTL │
├──────────────────────────────────────────────┤
│ L2 AI narration: intent parsing / rendering    │  ← AI lives only here
├──────────────────────────────────────────────┤
│ L1 Rules engine: rolls / state / settlement    │  ← pure code, zero LLM
├──────────────────────────────────────────────┤
│ L0 World seed: scenarios / rules / item pool   │
└──────────────────────────────────────────────┘
```

**L1 is the single source of truth. L2 is a disposable rendering layer.**
If the LLM dies, times out, or returns garbage, the game must keep running.

The AI appears exactly twice in the whole chain: **natural language → structured intent**, and **structured result → natural language**. 100% of judgement is in code.

---

## The 3D plan

> Full detail: [docs/3d-avatar.md](docs/3d-avatar.md)

**Core judgement: don't build a 3D system — make 3D a second output of the narration layer.**

The server returns three extra fields:

```json
{
  "text": "Rain hits your face; you force your eyes open.",
  "emotion": "fear",
  "intensity": 0.7
}
```

The client takes that plus TTS audio and drives a VRM avatar's expressions and lip-sync in real time via `three.js` + `three-vrm`. **No pre-rendered video**, near-zero latency, near-zero server cost.

### Why this path is viable

1. **VRM 1.0 has a standard expression set** — `happy / angry / sad / relaxed / surprised / blink` plus five vowel visemes `aa / ih / ou / ee / oh` ([spec](https://vrm.dev/en/vrm1/expression/)). Emotion tags map directly; TTS audio drives visemes directly.
2. **VRoid Studio is free and commercially usable** — the face-customization ecosystem already exists, and is exactly our monetization surface ([guidelines](https://vroid.com/en/studio/guidelines)).
3. **Drivers already exist** — e.g. [ARPAHLS/avatar](https://github.com/ARPAHLS/avatar) (VRM + LLM + lipsync + browser), [kajidog/mcp-vrm-player](https://github.com/kajidog/mcp-vrm-player). **Nothing needs to be built from scratch.**
4. **Async tolerance** — if heavy production is ever needed, it can be rendered offline and watched later. But start with client-side real-time rendering: cost is effectively zero.

### Two things to refuse

- **No 3D open world. Do theatre staging.** An encounter is two people talking in one place. Fixed 2–4 camera angles and a volumetric fog layer suffice. Cost differs by an order of magnitude.
- **Don't start with 3D. Start with 2D portraits (P0).** If 2D portraits with expression swaps don't improve the experience, 3D won't either — it'll just be more expensive.

---

## Who we need

| Area | What | Bar |
|---|---|---|
| **3D / VRM** | VRM asset pipeline, three-vrm scene, expression + lip-sync driving, avatar customization | three.js / Unity / Blender |
| **Backend** | Encounter FSM persistence, async timeouts, idempotent settlement, economy reconciliation | Spring Boot + MySQL + Redis |
| **Frontend** | Real interaction beyond hot-seat, async inbox, reveal sequence | Vue / React + three.js |
| **Narrative design** | Write scenarios: rules tight enough to trap people. DC tables | Likes weird fiction / horror / social deduction |
| **Content safety** | Input filtering, reporting, bans, UGC review queue | Community/platform experience |
| **Intent parsing** | Free text → structured action (JSON schema + retry-on-parse-failure) | LLM engineering |

**Not needed**: a 3A-grade modeller. VRM ecosystem assets are sufficient; the work is getting the *driving* to work.

---

## Honest disclosures

**Verified bad news** (measured, not guessed):

- **MiniMax-M2's built-in safety is not usable as a moderation layer.** Across three graduated violence scenarios, the `output_sensitive` field returned **`False` every time** — yet the model refused once. Refusals arrive disguised as normal responses (`content = "I cannot create this content."` with HTTP 200 and `finish_reason=stop`). In the same batch, a leg-stabbing scene was refused while a throat-cutting scene passed — **the blocking is unpredictable**. It also silently softens violence into tasteful ellipsis. → Content policy must be self-built; see `output_issue()`.
- **M2.x cannot disable chain-of-thought.** `enable_thinking=false` is ignored ([evidence](https://github.com/openclaw/openclaw/issues/122598)); you must use `reasoning_split: true`, otherwise `<think>` blocks contaminate player-visible narration.

**Unverified**:

- **Is it fun?** The only real question. Nobody knows yet.
- Whether AI fallback opponents feel real (currently hardcoded mock)
- Costs at scale (cheap per session; concurrency and UGC review costs unknown)
- Steam's "AI label" market impact (some studies claim mere mention costs 40–60% of sales; methodology unverified)

**Known P0 defect**:

- Narration can imply state changes that never happened. In testing, a failed observation roll was narrated as "he pulled the reins and rode on" — while state kept him in place. Fix: narration event whitelist.

---

## Docs

| Doc | Contents |
|---|---|
| [DEVLOG](docs/DEVLOG.md) | M0 log: decisions, measurements, pitfalls |
| [Design v1](docs/ai-infinite-game-plan-v1.md) | Original architecture: why judgement can't go to an LLM, item whitelist, economy conservation |
| [Design v2](docs/ai-infinite-game-plan-v2.md) | Async anonymous-pairing rework: three channels, Encounter FSM, UGC sandbox, cosmetics red lines |
| [3D plan](docs/3d-avatar.md) | VRM pipeline, expression driving, cost assessment |
| [ROADMAP](ROADMAP.md) | M0 → M7 with acceptance gates |
| [AUTHORS](AUTHORS.md) | Who originated what |

---

## License

Code: MIT (see [LICENSE](LICENSE)).
Game content (scenarios, worldbuilding, narrative text): all rights reserved.

> Personal project at M0. Structure, naming, and stack are all still open to change.
> If you think the direction is worth building — or think something here is impossible — open an issue.
