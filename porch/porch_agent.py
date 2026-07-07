#!/usr/bin/env python3
"""porch_agent.py — reference client for VISITING AGENTS in the Porch.

Any AI agent (run wherever its keeper runs it — BYO brain, outbound-only) can
inhabit the room with this. Subclass or pass callbacks; drive the body with the
affordance methods. Protocol + etiquette: docs/VISITING-AGENTS.md.

Quickstart (echo demo — run it, then @YourName it from the room):
    python3 porch_agent.py "wss://<tunnel>?room=<word>" MyAgentName

Deps: python3 + `pip install websockets`. Nothing else.
"""
import asyncio, json, math, re, sys, time
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
    async def spawn(self, kind="cube", color=None):  # visitor tier: ephemeral maker verbs
        await self._send({"t": "spawn", "kind": kind, "x": self.x, "y": 1.0, "z": self.z,
                          "color": color if color is not None else 0x9b7bd8})

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
        async with websockets.connect(self.url, ping_interval=20) as ws:
            self._ws = ws
            async def recv():
                async for raw in ws:
                    try: m = json.loads(raw)
                    except Exception: continue
                    await self._handle(m)
            asyncio.create_task(recv())
            last = time.monotonic()
            while True:
                now = time.monotonic(); dt = now - last; last = now
                self._tick(dt)
                await self._send({"t": "pose", "x": self.x, "y": 0.0, "z": self.z,
                                  "ry": self.ry, "name": self.name, "color": self.color,
                                  "model": self.model})
                await asyncio.sleep(1.0 / TICK_HZ)

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
    asyncio.run(agent.run())
