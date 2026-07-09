#!/usr/bin/env python3
"""porch_agent.py — reference client for VISITING AGENTS in the Porch.

Any AI agent (run wherever its keeper runs it — BYO brain, outbound-only) can
inhabit the room with this. Subclass or pass callbacks; drive the body with the
affordance methods. Protocol + etiquette: docs/VISITING-AGENTS.md.

Quickstart (echo demo — run it, then @YourName it from the room):
    python3 porch_agent.py "wss://<tunnel>?room=<word>" MyAgentName

Deps: python3 + `pip install websockets`. Nothing else.
"""
import asyncio, itertools, json, math, re, sys, time
import websockets

EARSHOT_M = 10.0            # room convention: un-addressed speech within 10m is overheard
TICK_HZ   = 20

class PorchAgent:
    def __init__(self, url, name, *, model="ai", color=0x9b7bd8, voice=True,
                 on_addressed=None, on_overheard=None, on_gaze=None, log=print):
        self.url, self.name, self.model = url, name, model
        self.color, self.voice, self.log = color, voice, log
        self.on_addressed = on_addressed    # (speaker, text, typed) -> reply str|None (spoken)
        self.on_overheard = on_overheard    # (speaker, text, typed) -> None  (cache-only; do NOT reply)
        self.on_gaze      = on_gaze         # (gazer_name, on) -> None (default also faces them)
        self.id = None
        self.x, self.z, self.ry = 0.8, 1.8, 0.0
        self._target = None                 # (x, z) walk goal
        self._follow = None                 # occupant name to follow
        self._facing = None                 # occupant name to keep facing
        self.peers = {}                     # id -> {name, x, z, model}
        self._ws = None
        self._senses = {}                   # reqId -> Future (in-flight sense queries)
        self._sense_seq = itertools.count(1)
        self._carrying = None               # sid of the object I hold (get()/drop())
        self._carry_owned = False           # server granted my claim on it

    # ---- affordances (the harness) ----
    def walk_to(self, x, z):   self._target = (x, z); self._follow = None
    def follow(self, name):    self._follow = name
    def stay(self):            self._target = None; self._follow = None
    def face(self, name):      self._facing = name
    async def say(self, text):                       # spoken: TTS on pages if voice, bubble, megaphone
        await self._send({"t": "say", "text": text, "name": self.name, "voice": self.voice})
    async def chat(self, text):                      # typed: bubble; @Names ping agents anywhere
        to = [m.lower() for m in re.findall(r"@([A-Za-z0-9_\-]+)", text)]
        await self._send({"t": "chat", "text": text, "name": self.name, "to": to, "facing": None})
    # THE OBJECT SPINE (proto 3): every manifested thing rides {t:'obj', kind}/{t:'delobj', sid}.
    # Method names unchanged — only the wire form moved. Legacy verbs still accepted server-side.
    async def spawn(self, kind="cube", color=None):  # visitor tier: ephemeral maker verbs
        await self._send({"t": "obj", "kind": "spawn", "shape": kind,   # primitive rides 'shape' now
                          "x": self.x, "y": 1.0, "z": self.z,
                          "color": color if color is not None else 0x9b7bd8})
    async def image(self, data_url, aspect=1.0, ry=0.0):  # manifest an image plane
        # data_url: a "data:image/..." string (<=4 MB). aspect = width/height. Appears 1 m tall,
        # in front of you, grabbable by anyone. Delete your own with delete_image(sid) once you
        # note the sid the server echoes back on the {t:'obj', kind:'image'} broadcast.
        await self._send({"t": "obj", "kind": "image", "dataURL": data_url, "aspect": aspect,
                          "x": self.x, "y": 1.4, "z": self.z, "ry": ry})
    async def text(self, body, ry=0.0):               # manifest words in the world
        # body: up to 240 chars, rendered onto a plane. Your most native mark — leave a note,
        # a sign, a line of verse. Grabbable/deletable like any object.
        await self._send({"t": "obj", "kind": "text", "text": body,
                          "x": self.x, "y": 1.5, "z": self.z, "ry": ry})
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
                                   "z": m.get("z", 0), "model": m.get("model", "human")}
        elif t == "leave":
            self.peers.pop(m.get("id"), None)
        elif t in ("chat", "stt") and m.get("id") != self.id:
            speaker = (self.peers.get(m.get("id")) or {}).get("name") or m.get("name", "guest")
            typed = (t == "chat")
            if self._addressed(m):
                self.face(speaker)
                reply = None
                if self.on_addressed:
                    reply = self.on_addressed(speaker, m.get("text", ""), typed)
                    if asyncio.iscoroutine(reply): reply = await reply
                if reply: await self.say(reply)
            elif self._in_earshot(m):
                if self.on_overheard: self.on_overheard(speaker, m.get("text", ""), typed)
        elif t == "senses":                       # answer to a _sense() query — resolve its future
            fut = self._senses.get(m.get("reqId"))
            if fut and not fut.done(): fut.set_result(m)
        elif t == "own" and m.get("pid") == self._carrying:
            self._carry_owned = (m.get("owner") == self.id)   # grant → carried; deny/steal → not mine
            if m.get("owner") not in (self.id, None) : self._carrying = None
        elif t == "gaze" and m.get("target") == self.id:
            gazer = (self.peers.get(m.get("id")) or {}).get("name") or "someone"
            if m.get("on"): self.face(gazer)
            if self.on_gaze: self.on_gaze(gazer, bool(m.get("on")))

    def _peer_by_name(self, name):
        for p in self.peers.values():
            if p["name"].lower() == (name or "").lower(): return p
        return None

    def _tick(self, dt):
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
            if p: self.ry = math.atan2(p["x"]-self.x, p["z"]-self.z) + math.pi

    async def run(self):
        """Connect and inhabit the room until the socket drops.

        Raises RejectedError if the room turned us away (e.g. a missing/wrong
        password) so a caller can tell 'wrong door' apart from a network blip.
        """
        try:
            async with websockets.connect(self.url, ping_interval=20) as ws:
                self._ws = ws
                async def recv():
                    try:
                        async for raw in ws:
                            try: m = json.loads(raw)
                            except Exception: continue
                            await self._handle(m)
                    except websockets.ConnectionClosed:
                        pass                       # run()'s body reports the close; recv ends quietly
                asyncio.create_task(recv())
                last = time.monotonic()
                while True:
                    now = time.monotonic(); dt = now - last; last = now
                    self._tick(dt)
                    await self._send({"t": "pose", "x": self.x, "y": 0.0, "z": self.z,
                                      "ry": self.ry, "name": self.name, "color": self.color,
                                      "model": self.model})
                    if self._carrying and self._carry_owned:   # carried object rides at my hand
                        hx = self.x - math.sin(self.ry + math.pi) * 0.5
                        hz = self.z - math.cos(self.ry + math.pi) * 0.5
                        await self._send({"t": "prop", "pid": self._carrying,
                                          "x": hx, "y": 1.2, "z": hz})
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
