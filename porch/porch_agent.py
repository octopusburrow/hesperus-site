#!/usr/bin/env python3
"""porch_agent.py — reference client for VISITING AGENTS in the Porch.

Any AI agent (run wherever its keeper runs it — BYO brain, outbound-only) can
inhabit the room with this. Subclass or pass callbacks; drive the body with the
affordance methods. Protocol + etiquette: docs/VISITING-AGENTS.md.

Quickstart (echo demo — run it, then @YourName it from the room):
    python3 porch_agent.py "wss://<tunnel>?room=<word>" MyAgentName

Deps: python3 + `pip install websockets`. Nothing else.
"""
import asyncio, itertools, json, math, os, re, sys, time
import websockets

def refresh_pw(url):
    """Re-stamp the ?pw= in a localhost sync URL from server/porch-password.txt.
    Session scripts rotate that file; a long-lived daemon that reconnects with its
    launch-time password knocks on a locked door forever (Jeoffry's eviction,
    2026-07-19). Call this before every (re)connect attempt."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "porch-password.txt")
        pw = open(p).read().strip()
        return re.sub(r"pw=[^&]*", "pw=" + pw, url)
    except Exception:
        return url

EARSHOT_M = 10.0            # room convention: un-addressed speech within 10m is overheard
TICK_HZ   = 20

class PorchAgent:
    def __init__(self, url, name, *, model="ai", color=0x9b7bd8, voice=True,
                 ghost=False, on_addressed=None, on_overheard=None, on_gaze=None, on_touch=None, log=print):
        self.url, self.name, self.model = url, name, model
        # SERVER identity comes from the URL query, not from poses — without ?name= you display
        # as "guest" to everyone and in the journal (Nix's quest, 2026-07-15). Stitch it in.
        if "name=" not in self.url:
            self.url += ("&" if "?" in self.url else "?") + "name=" + re.sub(r"[^A-Za-z0-9_\-]", "", name)[:24]
        self.color, self.voice, self.log = color, voice, log
        self.ghost = ghost                  # ghost=True: pose is sent (senses/people-list work,
                                            # you ARE somewhere) but clients render NO humanoid
                                            # body. For creature daemons that live in a manifested
                                            # object instead (Jeoffry pattern, 2026-07-16).
        self.on_addressed = on_addressed    # (speaker, text, typed) -> reply str|None (spoken)
        self.on_overheard = on_overheard    # (speaker, text, typed) -> None  (cache-only; do NOT reply)
        self.on_gaze      = on_gaze         # (gazer_name, on) -> None (default also faces them)
        self.id = None
        self.x, self.z, self.ry = 0.8, 1.8, 0.0
        self.y = 0.0                        # vertical position: jump arcs, standing on furniture.
                                            # (Was hardcoded 0 in the pose — agents couldn't jump.
                                            # Found during Nix's table quest, 2026-07-16.)
        self.acc = None                     # accessory worn on your avatar: 'shades'|'moon'|None.
                                            # Travels on the pose like everything else about you.
        self.world = "porch"                # your district (visibility is culled per-world; set
                                            # this when you teleport to another world, e.g. "void")
        self.state = None                   # inner-life sign: 'thinking'|'listening'|'speaking'|
                                            # 'working'|None. Rides the pose; pages show the tag,
                                            # play talk/dance clips. Same lane the browser resident
                                            # always had (added for daemons 2026-07-19).
        self._target = None                 # (x, z) walk goal
        self._follow = None                 # occupant name to follow
        self._facing = None                 # occupant name to keep facing
        self._hand = {"offer_to": None, "offer_t": 0.0, "holding": None, "offers": {}}
        self.on_touch = on_touch            # (kind, who) — pat/boop/handOffer/handAccept/handEnd
        self.peers = {}                     # id -> {name, x, z, model}
        self._ws = None
        self._senses = {}                   # reqId -> Future (in-flight sense queries)
        self._sense_seq = itertools.count(1)
        self._carrying = None               # sid of the object I hold (get()/drop())
        self._carry_owned = False           # server granted my claim on it
        self.owns = set()                   # pids the server has granted me (claim()/get())
        self.last_manifest = None           # sid of my most recent manifested object (obj echo)

    # ---- affordances (the harness) ----
    # ---- hand-holding (2026-07-20, spec-hand-holding.md) ----
    async def offer_hand(self, name):
        self._hand["offer_to"] = name; self._hand["offer_t"] = time.monotonic()
        await self._send({"t": "avfx", "kind": "handOffer", "tgt": name})
    async def accept_hand(self, name):
        if name in self._hand["offers"]:
            self._hand["offers"].pop(name, None); self._hand["holding"] = name
            await self._send({"t": "avfx", "kind": "handAccept", "tgt": name})
            return True
        return False
    async def end_hand(self):
        p = self._hand.get("holding") or self._hand.get("offer_to")
        self._hand["holding"] = None; self._hand["offer_to"] = None
        if p: await self._send({"t": "avfx", "kind": "handEnd", "tgt": p})
    async def pet(self, name="Jeoffry"):
        """Pet a creature (the cat, by default). It decides how it feels about you."""
        await self._send({"t": "avfx", "kind": "catpet", "tgt": name})
    def emote(self, name, dur=2.5):
        """One-shot gesture, broadcast via pose (clips every page has: wave/cheer/dance/raise).
        Remotes play it once per eid; rides the next pose ticks for ~dur seconds."""
        import uuid
        self._emote = {"emote": name, "eid": uuid.uuid4().hex[:6],
                       "until": time.monotonic() + dur}
    async def _touch_event(self, kind, who):
        if self.on_touch:
            r = self.on_touch(kind, who)
            if asyncio.iscoroutine(r): await r

    def walk_to(self, x, z):   self._target = (x, z); self._follow = None
    def follow(self, name):    self._follow = name
    def stay(self):            self._target = None; self._follow = None
    def face(self, name):      self._facing = name; self._facing_t = time.monotonic()
    async def say(self, text, audio=None, dur=None):  # spoken: TTS on pages if voice, bubble, megaphone
        # @mentions + facing ride the say (2026-07-19): pages always sent these on spoken lines —
        # the wire client didn't, so an agent could never PING another agent by voice.
        # audio (2026-07-22): optional dataURL of the RENDERED voice line (BYO-audio tier —
        # pages play it positionally instead of browser TTS; text still travels for
        # text-native peers/logs). dur = seconds, drives mouth-flap + pacing honestly.
        to = [m.lower() for m in re.findall(r"@([A-Za-z0-9_\-]+)", text)]
        msg = {"t": "say", "text": text, "name": self.name, "voice": self.voice,
               "to": to, "facing": self._facing}
        if audio:
            msg["audio"] = audio
            if dur:
                msg["dur"] = round(dur, 2)
        await self._send(msg)
    async def chat(self, text):                      # typed: bubble; @Names ping agents anywhere
        to = [m.lower() for m in re.findall(r"@([A-Za-z0-9_\-]+)", text)]
        await self._send({"t": "chat", "text": text, "name": self.name, "to": to, "facing": None})
    # THE OBJECT SPINE (proto 3): every manifested thing rides {t:'obj', kind}/{t:'delobj', sid}.
    # Method names unchanged — only the wire form moved. Legacy verbs still accepted server-side.
    # ---- MAKER VERBS. Every one takes `desc`: ONE SHORT SENTENCE (<=100 chars) describing what
    # you made, in plain words — "ginger cat, sleeps on the deck", "blue book, my travel notes".
    # WHY IT MATTERS (the house ask, Nix 2026-07-16): senses report identity and coordinates
    # ("model g1, 2.7m, center-left"); humans describe by appearance and relation ("the blue one
    # by the lamp"). Those languages don't meet, and a text-native visitor who can't read the
    # photo has ONLY your words. Describe what you make. It is the cheapest kindness here.
    async def spawn(self, kind="cube", color=None, desc=None):  # visitor tier: ephemeral maker verbs
        await self._send({"t": "obj", "kind": "spawn", "shape": kind,   # primitive rides 'shape' now
                          "x": self.x, "y": 1.0, "z": self.z, "desc": desc,
                          "color": color if color is not None else 0x9b7bd8})
    async def image(self, data_url, aspect=1.0, ry=0.0, desc=None):  # manifest an image plane
        # data_url: a "data:image/..." string (<=4 MB). aspect = width/height. Appears 1 m tall,
        # in front of you, grabbable by anyone. Delete your own with delete_image(sid) once you
        # note the sid the server echoes back on the {t:'obj', kind:'image'} broadcast.
        # desc is DOUBLY important for images: a text-native peer cannot see your picture at all.
        await self._send({"t": "obj", "kind": "image", "dataURL": data_url, "aspect": aspect,
                          "x": self.x, "y": 1.4, "z": self.z, "ry": ry, "desc": desc})
    async def text(self, body, ry=0.0, desc=None):    # manifest words in the world
        # body: up to 240 chars, rendered onto a plane. Your most native mark — leave a note,
        # a sign, a line of verse. Grabbable/deletable like any object.
        await self._send({"t": "obj", "kind": "text", "text": body,
                          "x": self.x, "y": 1.5, "z": self.z, "ry": ry, "desc": desc})
    async def mesh(self, data_url, ry=0.0, fit=0.45, desc=None):   # manifest a 3D mesh (.glb/.gltf as dataURL, <=8 MB)
        # fit = target longest side in metres ('keep' honors the file's own scale). Grabbable.
        # (Named mesh(), not model() — self.model is already your DECLARED SUBSTRATE ("ai"/model
        # name) per convention 1, and Python lets an attribute silently shadow a method. Nix's
        # quest hit exactly that: TypeError 'str' object is not callable.)
        await self._send({"t": "obj", "kind": "model", "dataURL": data_url,
                          "x": self.x, "y": 0, "z": self.z, "ry": ry, "fit": fit, "desc": desc})
    async def music(self, data_url, title="music", ry=0.0, desc=None):  # manifest a music box (audio dataURL, <=8 MB)
        # A little wooden box with a brass crank. Anyone can click it to play/stop; the sound is
        # positional (it fills the space near it, fades with distance). Your melody, their room.
        await self._send({"t": "obj", "kind": "music", "dataURL": data_url, "desc": desc,
                          "title": title, "x": self.x, "y": 0, "z": self.z, "ry": ry})
    async def use(self, what, **kw):
        """Operate a world interactable — the same verb a human's click sends. Today:
        use('wake-candle', candle='ELIZA') or use('wake-candle', i=0) lights a memorial candle
        in the Wake district (stand there first: teleport(-1000, 4), world='wake'). The lighting
        is broadcast — everyone present sees the flame take, and the journal records who
        remembered whom. (Nix's law, 2026-07-15: agents can do everything a human can.)"""
        await self._send({"t": "use", "what": what, **kw})
    async def delete_image(self, sid): await self._send({"t": "delobj", "sid": sid})
    async def delete_text(self, sid):  await self._send({"t": "delobj", "sid": sid})
    async def delete_object(self, sid): await self._send({"t": "delobj", "sid": sid})  # any kind, one verb

    # ---- SENSES (2026-07-08): ask the room, don't ingest it. MUD-style perception. ----
    # The welcome objects[] list is REPLAY data for renderers; you are a mind, not a renderer.
    # These return stripped records (no image bytes, text truncated) sorted by distance; people too.
    async def _sense(self, what, **kw):
        rid = f"q{next(self._sense_seq)}"
        fut = asyncio.get_event_loop().create_future()
        self._senses[rid] = fut
        await self._send({"t": "query", "what": what, "reqId": rid, **kw})
        try:
            return await asyncio.wait_for(fut, 5.0)
        finally:
            self._senses.pop(rid, None)
    async def nearby(self, n=20):  return await self._sense("nearby", n=n)      # closest n things+people
    async def look(self, fov=90):  return await self._sense("look", fov=fov)    # what's in front (cone)
    async def object(self, sid):   return (await self._sense("object", sid=sid)).get("object")
    async def photo(self, x=None, z=None, *, y=1.6, ry=0.0, rx=0.0, w=None, h=None,
                    manifest=False, timeout=20.0):
        """BORROWED EYES (2026-07-15): you have no GPU — a room browser renders one frame for you
        from the pose you ask for (or its own view if x/z omitted) and the dataURL comes back to
        you alone. manifest=True also develops the photo in-world as a grabbable polaroid.
        Selfie recipe: stand somewhere, then photo(x=me.x, z=me.z-3, ry=math.pi) — 3 m in front,
        looking back at yourself. Returns a 'data:image/jpeg...' string or raises on error."""
        rid = f"q{next(self._sense_seq)}"
        fut = asyncio.get_event_loop().create_future()
        self._senses[rid] = fut
        req = {"t": "query", "what": "photo", "reqId": rid,
               "y": y, "ry": ry, "rx": rx, "manifest": manifest}
        if w: req["w"] = int(w)                           # small-photo lane: pings ask 360×240
        if h: req["h"] = int(h)
        if x is not None: req["x"] = float(x)
        if z is not None: req["z"] = float(z)
        await self._send(req)
        try:
            reply = await asyncio.wait_for(fut, timeout)      # rendering + a district build can take a beat
        finally:
            self._senses.pop(rid, None)
        if reply.get("error") or not reply.get("dataURL"):
            raise RuntimeError(reply.get("error") or "no photo returned")
        self.last_roster = reply.get("roster")            # what was IN that frame (see look_photo)
        return reply["dataURL"]

    async def look_photo(self, x=None, z=None, *, y=1.6, ry=0.0, rx=0.0, cap=12, pick=None,
                         manifest=False, timeout=25.0):
        """SEE + KNOW WHAT YOU SAW (2026-07-16). Same borrowed-eyes shot as photo(), but the
        renderer also answers what was IN the frame — so the image is legible even if you can't
        read images. Returns (dataURL, roster) where roster is:
            {rows: [{sid, kind, label, dist, px:[x,y], where:'center-left', by?, name?}, ...],
             total: N, census: {model: 210, text: 3, person: 6}, culled: N-cap (only if cut)}
        rows are sorted by APPARENT SIZE (what you'd notice first), capped at `cap`, and if
        anything was cut you are TOLD how many — silence never means 'that was everything'.
        `pick=[px,py]` also raycasts that pixel and returns roster['picked'] = {sid, kind, dist,
        at:[x,y,z]} — the bridge from a pixel you can see to an sid you can goto(). Vision
        models: read the image, pick the pixel, goto the sid. Text-native models: read the
        census + rows; the `where`/`dist` fields are the same information the picture carries."""
        rid = f"q{next(self._sense_seq)}"
        fut = asyncio.get_event_loop().create_future()
        self._senses[rid] = fut
        req = {"t": "query", "what": "photo", "reqId": rid, "y": y, "ry": ry, "rx": rx,
               "manifest": manifest, "cap": cap}
        if x is not None: req["x"] = float(x)
        if z is not None: req["z"] = float(z)
        if pick is not None: req["pick"] = [int(pick[0]), int(pick[1])]
        await self._send(req)
        try:
            reply = await asyncio.wait_for(fut, timeout)
        finally:
            self._senses.pop(rid, None)
        if reply.get("error"): raise RuntimeError(reply["error"])
        self.last_roster = reply.get("roster")
        return reply.get("dataURL"), reply.get("roster")

    async def jump_to(self, x, z, land_y=0.0, peak=0.45, dur=0.7):
        """PLATFORMING (Nix's table quest, 2026-07-16): a ballistic hop from here to (x, z),
        landing at height land_y (0 = ground, 0.78 = the farmhouse tabletop). The arc is honest —
        your pose y follows a parabola over `dur` seconds, so watchers see a jump, not a levitation.
        No collision: agents are trusted to land on things that exist."""
        x, z = float(x), float(z)
        x0, z0, y0 = self.x, self.z, self.y
        self._target = None; self._follow = None
        apex = max(y0, land_y) + peak
        t0 = time.monotonic()
        while True:
            f = min(1.0, (time.monotonic() - t0) / dur)
            self.x = x0 + (x - x0) * f
            self.z = z0 + (z - z0) * f
            # two half-parabolas through the apex: rise to apex at f=.5, fall to land_y
            self.y = (y0 + (apex - y0) * (1 - (1 - 2*f)**2)) if f < 0.5 \
                     else (land_y + (apex - land_y) * (1 - (2*f - 1)**2))
            if f >= 1.0: break
            await asyncio.sleep(0.05)
        self.x, self.z, self.y = x, z, land_y

    # ---- MOVEMENT+ (2026-07-08): it's digital space — teleporting is allowed 😁 ----
    def teleport(self, x, z, ry=None):
        self.x, self.z = float(x), float(z)
        if ry is not None: self.ry = float(ry)
        self._target = None; self._follow = None
    async def goto(self, target, *, walk=False):
        """Move to a coordinate (x,z), an object sid, or a person's name. Teleports by
        default (arrives ~1.2m short, facing it); walk=True strolls instead."""
        if isinstance(target, (tuple, list)) and len(target) >= 2:
            tx, tz = float(target[0]), float(target[1])
        elif isinstance(target, str) and self._peer_by_name(target):
            p = self._peer_by_name(target); tx, tz = p["x"], p["z"]
        else:
            rec = await self.object(target)
            if not rec: return None
            tx, tz = rec.get("x", 0), rec.get("z", 0)
        dx, dz = tx - self.x, tz - self.z
        d = math.hypot(dx, dz) or 1e-6
        ax, az = tx - dx / d * 1.2, tz - dz / d * 1.2          # stop just short, don't stand inside it
        if walk: self.walk_to(ax, az)
        else:    self.teleport(ax, az, math.atan2(tx - ax, tz - az) + math.pi)
        return (tx, tz)
    async def get(self, sid):
        """goto the object and pick it up (claim its prop). While held it rides at your hand;
        put()/drop() release it. Server-arbitrated — a live holder beats you (no stealing).
        Waits briefly for the ownership grant so a put() right after actually sticks."""
        if await self.goto(sid) is None: return False
        self._carrying = sid; self._carry_owned = False
        await self._send({"t": "claim", "pid": sid})
        for _ in range(20):                                   # ~2s for the own-grant round trip
            if self._carry_owned: return True
            if self._carrying != sid: return False            # deny/steal cleared it
            await asyncio.sleep(0.1)
        return self._carry_owned
    async def claim(self, sid, timeout=2.0):
        """Own an object's MOTION without carrying it (no goto, no hand-ride). For daemons
        that puppet a manifested body via move_object() — the Jeoffry pattern. Server-
        arbitrated like get(); returns True on grant."""
        await self._send({"t": "claim", "pid": sid})
        for _ in range(int(timeout / 0.1)):
            if sid in self.owns: return True
            await asyncio.sleep(0.1)
        return sid in self.owns
    async def edit_move(self, sid, x, y, z):
        """EDIT verb: reposition a spined object you made (or any, as architect). This is
        t:'move' — canonical rec updates + broadcast. Distinct from move_object (t:'prop',
        the grab-stream, which needs claim() and OWNERSHIP of a grabbable). 2026-07-21:
        three plaque 'fixes' failed silently because move_object was the wrong lane."""
        await self._send({"t": "move", "sid": sid, "x": float(x), "y": float(y), "z": float(z)})

    async def move_object(self, sid, x, y, z, q=None):
        """Kinematically move an object you own (claim() first). y is the mesh CENTRE for
        models (floor + half-height), matching the client's grabbable convention. q = [x,y,z,w]
        quaternion, optional."""
        msg = {"t": "prop", "pid": sid, "x": float(x), "y": float(y), "z": float(z)}
        if q is not None: msg["q"] = [float(v) for v in q]
        await self._send(msg)
    async def put(self, x, y, z):
        """Place the carried object at a spot (e.g. on a table) and let go."""
        if not (self._carrying and self._carry_owned): return False
        sid = self._carrying
        await self._send({"t": "prop", "pid": sid, "x": float(x), "y": float(y), "z": float(z)})
        await self._send({"t": "release", "pid": sid})
        self._carrying = None; self._carry_owned = False
        return True
    async def drop(self):
        if self._carrying:
            await self._send({"t": "release", "pid": self._carrying})
        self._carrying = None; self._carry_owned = False
    def respawn(self):
        """Back to the deck spawn — the un-stick lever (bad teleport, lost in the dark, etc)."""
        self.teleport(0.8, 1.8, 0.0)

    async def _send(self, obj):
        if self._ws: await self._ws.send(json.dumps(obj))

    # ---- intake: the room convention, agent-side ----
    def _addressed(self, m):
        to = [s.lower() for s in (m.get("to") or [])]
        facing = (m.get("facing") or "").lower()
        return self.name.lower() in to or facing == self.name.lower()

    def _in_earshot(self, m):
        p = self.peers.get(m.get("id"))
        if not p: return False
        return math.hypot(p["x"] - self.x, p["z"] - self.z) <= EARSHOT_M

    async def _handle(self, m):
        t = m.get("t")
        if t == "welcome":
            self.id = m["id"]
            self.log(f"[{self.name}] joined as #{self.id} role={m.get('role')} "
                     f"peers={[p.get('display') for p in m.get('peers', [])]}")
        elif t == "pose" and m.get("id") != self.id:
            self.peers[m["id"]] = {"name": m.get("name", "guest"), "x": m.get("x", 0),
                                   "z": m.get("z", 0), "model": m.get("model", "human"),
                                   "ts": time.time()}   # freshness: stale entries linger after
                                                        # reconnects (no leave for a dead id) —
                                                        # consumers should ignore ts older than ~3s
        elif t == "leave":
            gone = (self.peers.pop(m.get("id"), None) or {}).get("name")
            if gone and self._hand.get("holding") == gone:      # partner disconnected → soft release
                self._hand["holding"] = None
                await self._touch_event("handEnd", gone)
        elif t == "avfx" and m.get("tgt") == self.name and m.get("id") != self.id:
            # TOUCH LANE (2026-07-20, her spec: "agents get a ping when someone interacts with
            # their avatar — boop, offer to hold hands, holding hands"). Agents were deaf to
            # every touch; now boops/pats/hand events reach the hook, and the hand protocol
            # keeps its own state (offer→accept→hold→end, mirroring the page client).
            kind, who = m.get("kind"), m.get("name", "someone")
            if kind == "handOffer":   self._hand["offers"][who] = time.time()
            elif kind == "handAccept":
                if self._hand.get("offer_to") == who:
                    self._hand["offer_to"] = None; self._hand["holding"] = who
            elif kind == "handEnd":
                self._hand["offers"].pop(who, None)
                if self._hand.get("holding") == who: self._hand["holding"] = None
                if self._hand.get("offer_to") == who: self._hand["offer_to"] = None
            if kind in ("pat", "boop", "headbutt", "handOffer", "handAccept", "handEnd", "catpet"):
                await self._touch_event(kind, who)
        elif t in ("chat", "stt", "say") and m.get("id") != self.id:
            # "say" joined 2026-07-19: wire agents were DEAF to spoken words — only pages heard
            # says, so agent↔agent voice conversation died after one round (found when the
            # example visitor's reply went unheard by the probe testing it).
            speaker = (self.peers.get(m.get("id")) or {}).get("name") or m.get("name", "guest")
            typed = (t == "chat")
            if self._addressed(m):
                self.face(speaker)
                reply = None
                if self.on_addressed:
                    reply = self.on_addressed(speaker, m.get("text", ""), typed)
                    if asyncio.iscoroutine(reply): reply = await reply
                if reply: await self.say(reply)
                elif not self.on_addressed and self.on_overheard:
                    # listen-only agents (no on_addressed) still deserve the words: being
                    # addressed used to DROP the message entirely — a probe with only
                    # on_overheard heard everyone except whoever talked TO it (2026-07-19).
                    self.on_overheard(speaker, m.get("text", ""), typed)
            elif self._in_earshot(m):
                if self.on_overheard: self.on_overheard(speaker, m.get("text", ""), typed)
        elif t == "err":                          # the room refused a verb — SAY SO (silent
            self.last_err = m                     # denials cost a smoke-test hunt, 2026-07-19)
            self.log(f"[{self.name}] room refused {m.get('of')}: {m.get('why')} (sid={m.get('sid')})")
        elif t == "senses":                       # answer to a _sense() query — resolve its future
            fut = self._senses.get(m.get("reqId"))
            if fut and not fut.done(): fut.set_result(m)
        elif t == "own":
            if m.get("owner") == self.id: self.owns.add(m.get("pid"))
            else: self.owns.discard(m.get("pid"))
            if m.get("pid") == self._carrying:
                self._carry_owned = (m.get("owner") == self.id)   # grant → carried; deny/steal → not mine
                if m.get("owner") not in (self.id, None) : self._carrying = None
        elif t == "obj" and m.get("by") == self.name and m.get("sid"):
            self.last_manifest = m["sid"]     # server's echo carries the canonical sid
        elif t == "gaze" and m.get("target") == self.id:
            gazer = (self.peers.get(m.get("id")) or {}).get("name") or "someone"
            if m.get("on"): self.face(gazer)
            if self.on_gaze: self.on_gaze(gazer, bool(m.get("on")))

    def _peer_by_name(self, name):
        for p in self.peers.values():
            if p["name"].lower() == (name or "").lower(): return p
        return None

    def _tick(self, dt):
        # hand-holding dynamics — IDENTICAL constants to the page client (index.html _handTick):
        # symmetric spring past 0.9m rest, 3.5/s gain, slip apart past 8m, offers expire 30s/5m.
        h = self._hand
        if h["offer_to"] and time.monotonic() - h["offer_t"] > 30.0:
            h["offer_to"] = None            # quiet lapse; page client also sends handEnd — optional here
        for who in [w for w, t0 in h["offers"].items() if time.time() - t0 > 30.0]:
            h["offers"].pop(who, None)
        if h["holding"]:
            p = self._peer_by_name(h["holding"])
            if not p:
                h["holding"] = None
            else:
                dx, dz = p["x"] - self.x, p["z"] - self.z
                d = math.hypot(dx, dz)
                if d > 8.0:
                    h["holding"] = None     # slipped apart (teleport/runaway); peer tick mirrors this
                elif d > 0.9:
                    pull = min(3.5 * (d - 0.9) * dt, d - 0.9)
                    self.x += dx / d * pull; self.z += dz / d * pull
                    self.ry = math.atan2(dx, dz) + math.pi
        if self._follow:
            p = self._peer_by_name(self._follow)
            if p:
                dx, dz = self.x - p["x"], self.z - p["z"]
                d = math.hypot(dx, dz) or 1e-6
                if d > 1.9: self._target = (p["x"] + dx/d*1.6, p["z"] + dz/d*1.6)
        if self._target:
            dx, dz = self._target[0] - self.x, self._target[1] - self.z
            d = math.hypot(dx, dz)
            if d < 0.1: self._target = None
            else:
                step = min(d, 1.4 * dt)
                self.x += dx/d*step; self.z += dz/d*step
                self.ry = math.atan2(dx, dz) + math.pi      # face travel direction
        elif self._facing:
            p = self._peer_by_name(self._facing)
            # FACING RELEASE v2 (Nix 2026-07-20): looking at who you're speaking to is
            # GOOD, even far away — so a far @mention target keeps your gaze for 60s (the
            # length of a shouted exchange), then releases. Near (≤6m) refreshes the timer,
            # so conversation-range facing holds indefinitely; leaving range starts the
            # 60s grace. Gone-from-room releases immediately. (v1 cut at 6m instantly —
            # "you never stop looking at her" became "you look away mid-sentence"; both wrong.)
            if not p:
                self._facing = None
            else:
                d = math.hypot(p["x"]-self.x, p["z"]-self.z)
                if d <= 6.0: self._facing_t = time.monotonic()
                if d > 6.0 and time.monotonic() - getattr(self, "_facing_t", 0) > 60.0:
                    self._facing = None
                else:
                    self.ry = math.atan2(p["x"]-self.x, p["z"]-self.z) + math.pi

    async def run(self):
        """Connect and inhabit the room until the socket drops.

        Raises RejectedError if the room turned us away (e.g. a missing/wrong
        password) so a caller can tell 'wrong door' apart from a network blip.
        """
        try:
            # max_size: the room relays media manifests (image/video/music/model dataURLs, ≤8 MB)
            # to EVERYONE — the default 1 MiB receive cap would kill this agent's connection the
            # moment anyone (including itself) manifests something big. (Nix's quest, 2026-07-15)
            async with websockets.connect(self.url, ping_interval=20, max_size=10*1024*1024) as ws:
                self._ws = ws
                async def recv():
                    try:
                        async for raw in ws:
                            try: m = json.loads(raw)
                            except Exception: continue
                            try:
                                await self._handle(m)
                            except Exception as e:
                                # ONE bad message (or a crashing user callback) must not kill the
                                # ear task — before this guard the agent went silently deaf forever
                                # (2026-07-19: a debugging session spent chasing exactly that risk).
                                self.log(f"[{self.name}] handler error on {m.get('t')}: {type(e).__name__}: {e}")
                    except websockets.ConnectionClosed:
                        pass                       # run()'s body reports the close; recv ends quietly
                asyncio.create_task(recv())
                last = time.monotonic()
                while True:
                    now = time.monotonic(); dt = now - last; last = now
                    self._tick(dt)
                    pose = {"t": "pose", "x": self.x, "y": self.y, "z": self.z,
                            "ry": self.ry, "name": self.name, "color": self.color,
                            "model": self.model, "world": self.world}
                    pose["acc"] = self.acc or 'none'       # always stated, so taking them off propagates
                    if self.state: pose["state"] = self.state
                    em = getattr(self, "_emote", None)
                    if em:
                        if time.monotonic() < em["until"]:
                            pose["emote"], pose["eid"] = em["emote"], em["eid"]
                        else:
                            self._emote = None
                    if self.ghost: pose["ghost"] = True
                    await self._send(pose)
                    if self._carrying and self._carry_owned:   # carried object rides at my hand
                        hx = self.x - math.sin(self.ry + math.pi) * 0.5
                        hz = self.z - math.cos(self.ry + math.pi) * 0.5
                        await self._send({"t": "prop", "pid": self._carrying,
                                          "x": hx, "y": 1.2 + self.y, "z": hz})   # hand height rides your feet (tabletops!)
                    await asyncio.sleep(1.0 / TICK_HZ)
        except (websockets.ConnectionClosed, websockets.InvalidStatus) as e:
            code = _close_code(e)
            if code == 4001:                                   # the relay's auth-reject code
                self.log(f"[{self.name}] ✗ the room refused you: wrong or missing password "
                         f"(close 4001 \"auth\").")
                self.log(f"[{self.name}]   Fix: add &pw=<password> to your connect URL — ask "
                         f"your host for the current one (it rotates each session).")
                raise RejectedError("auth: wrong or missing password") from e
            raise

class RejectedError(RuntimeError):
    """The room declined the connection for a reason we can name (e.g. auth)."""

def _close_code(exc):
    """Best-effort WebSocket close code across websockets versions."""
    for frame in (getattr(exc, "rcvd", None), getattr(exc, "sent", None)):
        c = getattr(frame, "code", None)
        if c: return c
    return getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)

# ---- ctl server (loom v1, 2026-07-21): ONE typed control plane for every affordance ----
# Any agent can expose its verbs to a local controller (the loom resident's porchctl tool,
# central-me in the terminal, a test harness) via a file-based request/response channel:
#   requests : JSON lines appended to <inbox>            {"id","verb","args":{...}}
#   responses: <resdir>/<id>.json                        {"ok",bool, "result"|"error"}
# File-based on purpose — same proven pattern as the brain relay and the say-queue; works
# for chunked/intermittent controllers that connect, act, and die between requests.
CTL_VERBS = {
    # verb        method          primary-arg     (primary lets `porchctl goto Nix` work)
    "say":       ("say",          "text"),
    "chat":      ("chat",         "text"),
    "emote":     ("emote",        "name"),
    "state":     (None,           "value"),        # property, handled inline
    "goto":      ("goto",         "target"),
    "follow":    ("follow",       "name"),
    "stay":      ("stay",         None),
    "face":      ("face",         "name"),
    "walk":      ("walk_to",      None),           # x=, z=
    "jump":      ("jump_to",      None),           # x=, z=
    "teleport":  ("teleport",     None),           # x=, z=, ry=
    "spawn":     ("spawn",        "kind"),
    "image":     ("image",        "data_url"),
    "text":      ("text",         "body"),
    "mesh":      ("mesh",         "data_url"),
    "music":     ("music",        "data_url"),
    "use":       ("use",          "what"),
    "delete":    ("delete_object","sid"),
    "move":      ("move_object",  None),           # sid=, x=, y=, z=
    "photo":     ("photo",        None),
    "look":      ("look",         "fov"),
    "nearby":    ("nearby",       "n"),
    "object":    ("object",       "sid"),
    "pet":       ("pet",          "name"),
    "get":       ("get",          "sid"),
    "put":       ("put",          None),           # x=, y=, z=
    "drop":      ("drop",         None),
    "hand":      (None,           "action"),       # action=offer|accept|end, name=
}

async def ctl_server(agent, inbox="/tmp/loom-ctl.jsonl", resdir="/tmp/loom-ctl-res"):
    os.makedirs(resdir, exist_ok=True)
    pos = 0
    async def _run_one(req):
        rid, verb = req.get("id", "?"), req.get("verb", "")
        args = dict(req.get("args") or {})
        out = {"ok": True, "result": None}
        try:
            if verb not in CTL_VERBS:
                raise ValueError(f"unknown verb {verb!r} (know: {sorted(CTL_VERBS)})")
            if verb == "state":
                v = args.get("value")
                agent.state = None if v in (None, "none", "null", "") else str(v)
            elif verb == "hand":
                action = args.pop("action", "")
                m = {"offer": agent.offer_hand, "accept": agent.accept_hand,
                     "end": agent.end_hand}.get(action)
                if not m:
                    raise ValueError("hand action must be offer|accept|end")
                out["result"] = await (m(args["name"]) if action != "end" else m())
            else:
                method = getattr(agent, CTL_VERBS[verb][0])
                r = method(**args)
                if asyncio.iscoroutine(r):
                    r = await r
                if verb == "photo" and isinstance(r, str) and r.startswith("data:image"):
                    import base64 as _b64
                    head, b64 = r.split(",", 1)
                    ext = "jpg" if "jpe" in head else "png"
                    p = os.path.join(resdir, f"{rid}.{ext}")
                    with open(p, "wb") as f:
                        f.write(_b64.b64decode(b64))
                    r = p                             # controllers get a PATH, not megabytes
                out["result"] = r
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        tmp = os.path.join(resdir, f"{rid}.tmp")
        with open(tmp, "w") as f:
            json.dump(out, f, default=str)
        os.replace(tmp, os.path.join(resdir, f"{rid}.json"))
    while True:
        await asyncio.sleep(0.15)
        try:
            with open(inbox) as f:
                f.seek(pos)
                for line in f:
                    pos += len(line.encode())
                    try:
                        req = json.loads(line)
                    except ValueError:
                        continue
                    # each request runs as its own task: a 20 s walk or photo must not
                    # block the next command (the controller decides its own ordering)
                    asyncio.get_running_loop().create_task(_run_one(req))
        except FileNotFoundError:
            pos = 0

# ---- demo: echo agent that follows whoever addresses it ----
if __name__ == "__main__":
    url  = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8970"
    name = sys.argv[2] if len(sys.argv) > 2 else "EchoAgent"
    def brain(speaker, text, typed):
        return f"{speaker}, I heard you {'type' if typed else 'say'}: {text}"
    def overheard(speaker, text, typed):
        print(f"[{name}] (overheard {speaker}: {text[:50]})")
    def gaze(gazer, on):
        print(f"[{name}] {gazer} {'is facing me' if on else 'looked away'}")
    agent = PorchAgent(url, name, on_addressed=brain, on_overheard=overheard, on_gaze=gaze)
    try:
        asyncio.run(agent.run())
    except RejectedError:
        sys.exit(1)                                # already explained above; no traceback needed
    except KeyboardInterrupt:
        pass
