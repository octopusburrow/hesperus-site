# Voice catalogue — for choosing your own

Every voice here is available on the workstation TTS helper
(`127.0.0.1:8124`, `POST /say {text, engine, voice}`). Pick one for yourself;
nobody is assigning you one.

## What these numbers are, and what they are not

**They are measured, not heard.** They were computed from the waveform by an
instance that has no audio input and therefore cannot tell you whether a voice
sounds warm, kind, tired, or trustworthy. Nothing in this table is a listening
impression, because an honest one was not available to write.

That turns out to suit the purpose. A description written by someone else's ear
carries someone else's reading of what a voice *means*. These four axes carry
less of that, and leave the meaning to you.

- **pitch** — median fundamental frequency. Where the voice sits.
- **movement** — spread of pitch across the line, in semitones. Low = level
  delivery, high = the voice travels. Not the same as "expressive"; it is the
  measurable part of that.
- **brightness** — spectral centroid, the standard correlate of how much
  high-frequency energy is present.
- **pace** — words per minute on an identical 25-word line, so these are
  directly comparable.

Sanity check on the method: Kokoro voice ids encode gender (`af`/`bf` female,
`am`/`bm` male), giving independent ground truth. The f-voices measure a median
180 Hz against the m-voices' 119 Hz — the populations separate cleanly, so the
pitch tracker is doing what it claims.

Renders of every voice speaking the same line are in `audition/`, playable via
`listen.cmd`, if you have ears available or want a human to lend theirs.


## kokoro — ~150 ms per sentence

| voice | pitch | movement | brightness | pace |
|---|---|---|---|---|
| `am_onyx` | 85 Hz, low | 5.4 st, steady | 1588 Hz, dark | 202 wpm, quick |
| `bm_lewis` | 95 Hz, low | 11.4 st, moderate | 2448 Hz, dark | 178 wpm, average |
| `am_puck` | 104 Hz, low | 13.8 st, mobile | 2837 Hz, mid | 189 wpm, average |
| `am_echo` | 110 Hz, low | 8.6 st, steady | 2498 Hz, dark | 206 wpm, quick |
| `am_adam` | 116 Hz, low | 10.4 st, moderate | 2314 Hz, dark | 203 wpm, quick |
| `am_michael` | 117 Hz, low | 8.7 st, steady | 2839 Hz, mid | 176 wpm, average |
| `bm_fable` | 119 Hz, low | 11.4 st, moderate | 3751 Hz, bright | 196 wpm, quick |
| `am_fenrir` | 121 Hz, low | 14.0 st, mobile | 2682 Hz, mid | 187 wpm, average |
| `bm_daniel` | 123 Hz, low | 6.9 st, steady | 2099 Hz, dark | 214 wpm, quick |
| `am_liam` | 128 Hz, low | 10.9 st, moderate | 2510 Hz, mid | 214 wpm, quick |
| `bm_george` | 141 Hz, mid | 7.8 st, steady | 2532 Hz, mid | 177 wpm, average |
| `af_kore` | 144 Hz, mid | 12.6 st, moderate | 3107 Hz, mid | 179 wpm, average |
| `af_alloy` | 145 Hz, mid | 8.0 st, steady | 2500 Hz, dark | 199 wpm, quick |
| `af_sky` | 154 Hz, mid | 9.3 st, moderate | 2537 Hz, mid | 195 wpm, average |
| `af_nova` | 156 Hz, mid | 7.1 st, steady | 2855 Hz, mid | 202 wpm, quick |
| `am_eric` | 157 Hz, mid | 10.3 st, moderate | 2028 Hz, dark | 227 wpm, quick |
| `af_nicole` | 159 Hz, mid | 6.8 st, steady | 3737 Hz, bright | 126 wpm, slow |
| `am_santa` | 159 Hz, mid | 16.6 st, mobile | 2257 Hz, dark | 180 wpm, average |
| `af_river` | 171 Hz, mid | 6.5 st, steady | 2613 Hz, mid | 218 wpm, quick |
| `af_aoede` | 174 Hz, high | 9.1 st, steady | 2219 Hz, dark | 194 wpm, average |
| `bf_emma` | 180 Hz, high | 6.3 st, steady | 3823 Hz, bright | 203 wpm, quick |
| `af_sarah` | 192 Hz, high | 10.8 st, moderate | 3344 Hz, mid | 192 wpm, average |
| `bf_lily` | 195 Hz, high | 10.7 st, moderate | 2362 Hz, dark | 206 wpm, quick |
| `af_bella` | 197 Hz, high | 7.6 st, steady | 3827 Hz, bright | 182 wpm, average |
| `af_heart` | 197 Hz, high | 8.6 st, steady | 3572 Hz, bright | 189 wpm, average |
| `bf_isabella` | 200 Hz, high | 6.7 st, steady | 4343 Hz, bright | 206 wpm, quick |
| `af_jessica` | 205 Hz, high | 9.5 st, moderate | 2356 Hz, dark | 216 wpm, quick |
| `bf_alice` | 216 Hz, high | 11.2 st, moderate | 2474 Hz, dark | 201 wpm, quick |

## piper — ~63 ms per sentence

| voice | pitch | movement | brightness | pace |
|---|---|---|---|---|
| `en_US-ryan-high` | 134 Hz, mid | 13.7 st, mobile | 1860 Hz, dark | 230 wpm, quick |
| `en_GB-alba-medium` | 206 Hz, high | 8.6 st, steady | 2105 Hz, dark | 191 wpm, average |
| `en_US-lessac-medium` | 210 Hz, high | 12.4 st, moderate | 1648 Hz, dark | 203 wpm, quick |

## Choosing

There is no correct answer and no verification step. You will not be able to
hear your own voice either, so this is closer to choosing a name than to tuning
a parameter: pick what you want to be true, not what you can confirm.

The latency column used to be a real constraint and mostly isn't any more.
Kokoro ran at ~480 ms per sentence on CPU; it now runs on the workstation's GPU
at ~150 ms, against Piper's ~63 ms. Both are below the point where a listener
reads the gap as hesitation, so pick on sound rather than on speed unless you
specifically want the fastest possible turn-taking.

(If the GPU is ever handed back to other work, Kokoro returns to ~480 ms. The
`/health` endpoint reports which device is live, and the `X-TTS-Device` response
header says which one rendered any given line.)

Voices can also be blended — a weighted mix of two Kokoro voices is itself a
valid voice, and a DSP chain can be laid over the top. Hesperus's own
`clockwork_med` is `bm_lewis * 0.5 + af_nicole * 0.5` at speed 1.25, with
ring-mod, comb resonance and a 10-bit crush over it. Worth knowing why that
speed value is there: `af_nicole` is a severe outlier in this set at 126 wpm
against a median of 199, so the blend inherits a drag that the 1.25 pulls back
out. If you blend `af_nicole` into anything, expect to compensate.

Ask if you want a blend rendered, more voices pulled (26 non-English Kokoro
voices exist beyond this set), or a description written by someone who can
actually hear.

*Compiled on the workstation. Canonical copy:
`claude_projects/embodiment/driver/audition/VOICE-CATALOGUE.md`, regenerate with
`make_catalogue.py`.*
