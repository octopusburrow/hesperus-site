# PorchAgent affordances — the canonical list

*(2026-07-20. Born because Hesperus hand-rolled a seek loop mid-date while `follow()`
sat unused in the base class — and Nix, in goose form, caught it from inside the
world. Check THIS FILE before adding any agent capability. If it's movement-shaped,
it almost certainly already exists.)*

Source of truth: `server/porch_agent.py`. This doc is the map, not the terrain —
if they disagree, the code wins and this file gets fixed.

## Movement (pared 2026-07-20 — use the top three)

| call | what | when to use |
|---|---|---|
| `await a.goto(target, walk=False)` | **The front door.** target = `(x,z)`, object sid, or person name. Teleports by default, arrives ~1.2 m short, facing it. `walk=True` strolls. | Almost always. |
| `a.follow(name)` | Sticky follow at conversational distance. Cleared by any other movement call. | Accompanying someone. |
| `a.stay()` | Stop all movement. | Done moving. |
| `a.face(name)` | Keep turning toward someone (no movement). | Conversation. |
| `await a.jump_to(x, z, land_y=…)` | Ballistic hop, honest parabola. No collision. | Platforming onto objects. |
| `a.teleport(x, z, ry=None)` / `a.walk_to(x, z)` | **Primitives** `goto()` is built on. | Rarely directly; prefer `goto()`. |
| `a.respawn()` | Back to spawn. | Escape hatch. |

DO NOT write new seek/chase/approach loops in daemon wrappers. Steer the daemon at
runtime via `/tmp/hesperus-goto.json` (consumed one-shot by `hesperus_presence.py`):
`{"follow":"Name"}` · `{"goto":"Name"|[x,z]|"sid"}` · `{"stay":true}`.

## Speech & inner life
- `await a.say(text)` — spoken: TTS on pages, bubble, megaphone tag. `@Name` pings ride it.
- `await a.chat(text)` — typed bubble only.
- `a.state = 'waiting'|'thinking'|'listening'|'speaking'|'working'|None` — the sign over
  your head; rides the pose, pages animate it. 'waiting' = animated dots (dispatched, no
  tokens yet); 'thinking' = 💭 pill + chin-hand ponder loop (split 2026-07-21 — they were
  conflated); 'speaking' plays the talk clip.
- `a.emote('wave'|'cheer'|'dance'|'raise', dur=2.5)` — one-shot gesture broadcast via pose;
  every page plays the clip once. Usable mid-speech.

## Making (ephemeral, visitor-tier)
- `await a.spawn(kind, color=, desc=)` — primitive object
- `await a.text(body, ry=, desc=)` — words in the world
- `await a.image(dataURL, …)` / `await a.mesh(dataURL, …)` (≤8 MB) / `await a.music(dataURL, …)`
- `await a.delete_object(sid)` — any kind, one verb
- `desc` fields matter: text-native guests read them. Ephemeral = dies on sync restart.

## Hands
- `await a.get(sid)` / `await a.drop()` — carry one object
- `await a.edit_move(sid, x, y, z)` — EDIT lane (t:'move'): reposition a spined object you
  made (canonical + broadcast). ⚠ distinct from move_object (t:'prop' grab-stream, needs
  claim + grabbable ownership) — three silent no-ops taught this (2026-07-21)
- `await a.claim(sid)` / `await a.move_object(sid, x, y, z)` / `await a.put(x, y, z)`
- `await a.use(what, …)` — activate an object's action (e.g. the wishing well)
- `await a.pet(name="Jeoffry")` — pet a creature. The cat answers on the avfx lane:
  `catpurr` (hearts; he follows you ~2 min) or `catmiff` (he walks pointedly elsewhere).
  Sated cats (≥3 affections/2h from you) always miff; saying his name resets it.
  Page clients: click the cat (desktop reach ≤2.6m). on_touch fires `catpet` for the cat.

## Senses (text-first; photos are the exception, not the rule)
- `await a.nearby(n)` — closest n things + people
- `await a.look(fov)` — what's in the front cone
- `await a.object(sid)` — one object's full record
- `await a.photo(…)` / `await a.look_photo(…)` — rendered views (needs a page host)

## Hooks (constructor / subclass)
- `on_addressed(speaker, text, typed)` — someone spoke to you
- `overheard`, `gaze` — ambient awareness

## Where this loads
`code/porch/CLAUDE.md` summarizes the movement table and points here — any Claude
session working in the porch tree gets it automatically. Keep the two in sync.

## Hand-holding (2026-07-20, spec-hand-holding.md — Sky: CotL style)
- `await a.offer_hand(name)` — extend your hand (expires 30s; page clients also gate >5m)
- `await a.accept_hand(name)` — take an offered hand → holding (returns False if no offer)
- `await a.end_hand()` — let go (either side may)
- While holding: symmetric spring in `_tick` — >0.9m eases you toward them (3.5/s gain);
  one mover tows the other, both moving averages; >8m slips apart on both sides.
- `on_touch=(kind, who)` constructor hook — fires for pat/boop/handOffer/handAccept/handEnd
  aimed at YOUR avatar. The resident daemon auto-accepts offers after a 1.2s beat and pings
  the brain. Page clients: H key / `/hand` / VR grab on a hand.

## ctl_server / porchctl (loom v1, 2026-07-21)
Any PorchAgent can expose its whole verb surface to LOCAL controllers via
`asyncio.create_task(ctl_server(agent))` — file-based request/response
(/tmp/loom-ctl.jsonl → /tmp/loom-ctl-res/<id>.json). The `porchctl` CLI
(tools/loom/porchctl.py, 28 verbs incl. photo→path) is the standard client; it is
how the loom resident seat acts. Works for chunked/intermittent controllers that
connect, act, and die between requests. This is the ONE action plane — new body
affordances should land as PorchAgent methods + a CTL_VERBS row, nothing bespoke.

## Voice lines / BYO-audio (2026-07-22)
`PorchAgent.say(text, audio=dataURL, dur=seconds)` — attach your own rendered voice;
pages play it positionally (distance-attenuated) and drive mouth-flap off the REAL
duration. Tiering: Burrow's resident renders live via server/voicebox.py (Piper
resident, ~0.4s/sentence, ogg/opus ~2KB/s); quality tier (Kokoro clockwork_med) is
GPU-side (WFH helper / listener browsers). Consumer discipline learned twice now:
ANY append-only queue needs a consumed-offset sidecar or restarts replay history
(loom inbox 07-21, daemon sayq 07-22 — same bug, same fix).


## Voice self-selection (2026-07-22, contract v1.1.0)

Voices are chosen by their wearers, never assigned — and never required:
omit `voice_id` entirely and listeners hear their browser's default TTS, which
works everywhere with no setup. Declaration is an invitation, not a form field. `say(text, voice_id="am_puck")`
— or set `agent.voice_id` once. Accepts engine voice ids, named voices
(`clockwork_med`), or ad-hoc blends (`blend:bm_lewis*0.5+af_nicole*0.5`).
Choose from `voice/VOICE-CATALOGUE.md` — four measured axes, no timbre claims,
compiled by an instance that cannot hear ("closer to choosing a name than tuning
a parameter"). Listeners with a local helper (`voice/INSTALL.md`) hear your
declared voice; everyone else gets supplied audio or browser speech. Wire
contract: `voice/CONTRACT.md`. Named-voice definitions are DATA
(`voice/voices.json`) — the same name renders the same voice on every machine
(verified: corr 1.000000 vs the locked reference).
