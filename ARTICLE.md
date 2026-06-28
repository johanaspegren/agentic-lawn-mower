# I got into the head of my robot lawn mower in four hours

*Or: yes, AI-assisted hacking of consumer IoT is now genuinely easy, and
we should probably talk about that.*

---

You may have caught the recent round of "AI security tool finds
everything" headlines. The current poster child is **Claude Mythos**, the
Anthropic preview model that surfaced a [27-year-old OpenBSD
vulnerability][mythos-devto] and is reported to have discovered
["thousands" of zero-day vulnerabilities in weeks][scworld] across
operating systems, browsers, and major open-source projects. The White
House reportedly stepped in to slow access expansion. The IEEE
[wrote it up][ieee]. So did everyone else.

It would be easy to be sceptical. The "AI found N vulnerabilities in M
time" genre of claim has a long, sad history going all the way back to
the antivirus-era "we detect a million viruses" press releases that
counted polymorphic variants of the same family. Most engineers I know
glance at numbers like this and assume the same trick is being played.

The unsettling thing about Mythos is that you can roll your eyes at the
framing while the underlying claim remains correct. Even discounting
press-release inflation by a generous factor, the picture is that
AI-assisted vulnerability discovery is *real, fast, and improving*.

And that picture shouldn't surprise anyone. To make the point with
something cheaper than an OpenBSD zero-day, I picked a target sitting in
my own garden: a Lyfco E1800 robotic lawn mower. It has a phone app, a
Wi-Fi connection, and a healthy attitude of *"the manufacturer didn't
bother documenting the protocol because the user doesn't need to know."*
Four hours later I can drive it from my Mac terminal with a single Python
command, my laptop knows its Wi-Fi password (oops, more on that later),
and the protocol fits in a 100-line README.

This comparison is unfair to Mythos. What Claude Mythos is doing is
genuinely difficult: chained vulnerability discovery and exploit synthesis
against hardened operating systems. What I did is closer to a CTF Easy
challenge. But that's the point — *the difficulty floor for IoT
reverse-engineering has dropped so far that the question isn't whether
your appliances can be cracked, but how long it takes a curious neighbor
to do it on a slow Sunday.*

[mythos-devto]: https://dev.to/themachinepulse/claude-mythos-the-27-year-bug-that-should-terrify-you-1g0h
[scworld]: https://www.scworld.com/news/anthropic-claude-mythos-preview-finds-thousands-of-vulnerabilities-in-weeks
[ieee]: https://spectrum.ieee.org/anthropic-claude-mythos-preview-code

---

## The setup

The Lyfco E1800 (sold in some markets as the EGROBOT M10) is a perimeter-wire
robotic mower with a phone app for control. The app does the usual things:
schedule mowing, see voltage, send the mower home, drive it around with a
"remote" mode. What it *doesn't* do is offer an API, expose the mower to
Home Assistant, or in any way acknowledge that you might want to script it.

I wanted to script it.

The mower briefly broadcasts a Wi-Fi access point named `ESP_5818FA` during
setup — the giveaway that there's an Espressif chip inside running custom
firmware, and that the suffix `5818FA` is the last three bytes of its MAC
address. After provisioning, it sits silently on the LAN with a stable IP
(`192.168.68.108` in my house, after a DHCP reservation).

Phase one was the sniffing: I installed PCAPDroid on the phone, recorded
the app's traffic during simple actions (open the app, tap remote, drive
forward, stop, return home, set the clock), and exported the captures as
hex dumps. Nothing exotic. The interesting fact was that the app talks to
the mower *locally* over TCP port 9600 — not through a cloud. So replay
attacks should work, in theory, with no auth handshake to defeat.

That's where Claude came in.

---

## The XOR reveal

I dropped the first few hex captures into Claude with a casual prompt:
"Can you see what's going on here?" The capture looked like this when
printed alongside its ASCII decoding:

```
30 68 00 50 00 00 00 00  55 30 30 78 30 30 30 31  0h.P....U00x0001
30 30 30 30 73 5f 54 55  7e 51 5d 55 0d 77 55 44  0000s_TU~Q]U.wUD
65 51 42 44 74 51 44 51  16 73 58 5e 0d 00 16 7c  eQBDtQDQ.sX^...|
...
```

A previous chat session (with a different assistant) had concluded that the
gibberish part was "obfuscated" — possibly XOR-encrypted, definitely
unreadable. Vendors love to do that to make casual prying harder.

Claude noticed the pattern almost immediately. The bytes `73 5f 54 55 7e
51 5d 55`, XORed with `0x30`, spell `CodeName`. From there the whole frame
fell over: every text byte in the body is XORed with `0x30`, key/value
pairs are separated by `&` (which becomes `0x16` after the XOR), `=`
becomes `0x0d`, and binary blobs ride inside a `UserBinaryData=##…` field
that *isn't* XORed. The 8-byte header starts with the magic `"0h"`
followed by a big-endian length.

This is a fun moment for a couple of reasons:

1. The "obfuscation" is a single-byte XOR. The same technique an undergrad
   CTF solver runs on autopilot. A senior engineer at the vendor likely
   added it specifically *so the bytes don't look like ASCII text in
   Wireshark*. Mission half-accomplished.

2. The keys are wonderfully self-documenting. `CodeName=Search`,
   `CodeName=SearchAck`, `CodeName=GetUartData`, `CodeName=UartUpLoadData`,
   plus fields like `DevName`, `Mac`, `StaId`, `StaPd`. Once the XOR is
   undone, the protocol reads like a config file.

We had the envelope. The actual *commands* still lived in the binary blob
inside `UserBinaryData=##…`, and that was its own onion.

---

## "Did the mower beep?"

The first replay attempt was the "initiate remote mode" packet I'd
captured. I ran the Python script Claude had drafted, against the mower's
real IP, with no idea what would happen.

```bash
python -m mower initiate-remote 192.168.68.108
```

No reply. No error. Just silence.

Then, from across the room: **beep**.

That single beep was probably my favorite moment of the project. The mower
had accepted the packet, entered remote-control mode, and acknowledged it
physically. No telemetry parsing needed — the bot itself confirmed we'd
gotten in. The replay attack worked exactly as expected because the
protocol has no auth, no sequence number, no nonce. The vendor's threat
model presumably stopped at "the user is on Wi-Fi and the phone is paired."

I made coffee.

---

## The 0x69 family

With remote mode entered, the next captures (drive forward, stop, drive
reverse, home, blade engine on) revealed a beautifully clean pattern.
Every control packet looked like this:

```
00 08 69 <param> 76 75 73 <checksum>
```

Eight bytes, with a single parameter byte that selected the action and a
checksum that made every variant sum to `0x1D8`. After a few captures
Claude spotted it: `checksum = (0x09 − param) & 0xFF`. Trivial. And once
you know the checksum function, you don't need to capture every command
— you can *synthesize* them.

I added a `param` subcommand to the CLI so we could sweep the unknown
opcodes:

```bash
python -m mower param 192.168.68.108 0x05
```

The mower beeped. Then it started preparing for autonomous mow. `0x05` is
"start autonomous mowing." I stopped it before it tried to actually mow
through the living room.

Sweep complete:

| Param | Action |
|-------|---------------------|
| 0x00  | stop                |
| 0x01  | forward             |
| 0x02  | reverse             |
| 0x03  | left                |
| 0x04  | right               |
| 0x05  | autonomous mow      |
| 0x06  | enter remote mode   |
| 0x07  | home (return dock)  |
| 0x08  | blade engine on/off |

Nine commands. Roughly thirty minutes from the first hex dump.

---

## Set-time, and the satisfying click of a cracked checksum

The drive commands were too easy — they're all eight-byte frames with one
variable. Things got more interesting when I captured the "set date and
time" packet, which is 22 bytes long. After XORing with `0x30`, those 22
bytes spell ASCII:

```
22T20260629123000FC11
```

Once you see it, the format is obvious: length `22`, type marker `T`, year
`2026`, month `06`, day `29`, ISO weekday `1` (Monday), hour `23`, minute
`00`, seconds `00`, trailer `FC`, and… two trailing chars that I didn't
immediately understand: `11`.

This was the part where Claude and I went in circles for a bit. The
trailing bytes change between captures. Were they a sequence number? CRC?
Random salt? I had two samples and couldn't tell.

I went back to the app, set a deliberately different date/time (Sat
2026-06-27, 10:03), captured it, and dropped it in. Diff:

```
A (Mon 23:00): 22T 2026 06 29 1 23 00 00 FC 11
B (Sat 10:03): 22T 2026 06 27 6 10 03 00 FC 0F
```

Sum the 15 digit values between the `T` and the `FC`:

```
A: 2+0+2+6+0+6+2+9+1+2+3+0+0+0+0 = 33,   trailer = 0x11 = 17
B: 2+0+2+6+0+6+2+7+6+1+0+0+3+0+0 = 35,   trailer = 0x0F = 15
```

In both cases `digits + trailer = 50 = 0x32`. The same constant-target-sum
trick as the drive commands, just over decimal digits instead of raw
bytes. **Two samples, cracked.**

A few minutes later there was a `set_time(datetime)` function in the
library that builds a byte-perfect packet for any date and time you hand
it. Verified against both captures by direct comparison.

---

## The failsafe

Around hour three I realized there was an operational problem with
interactive control: if the mower is rolling forward because I sent
`forward`, and my Python script crashes for any reason, the mower keeps
going. That's a real problem with a real machine that can drive into a
fence.

So the REPL got a three-layer STOP failsafe:

1. **Context-manager exit.** `with MowerClient(ip) as m:` always sends
   `stop` when leaving the block — including on uncaught exceptions.
2. **atexit hook.** Catches the case where someone forgot the context
   manager.
3. **Signal handlers.** `SIGINT` (Ctrl-C) and `SIGTERM` route to the
   same idempotent stop, with a console message.

All three funnel into one function so we never accidentally double-stop
or send a stale `stop` on a closed socket. Defensive, slightly paranoid,
appropriate for the threat model: "Johan is going to wreck his lawn
mower."

I tested it the way you should test failsafes — by genuinely crashing the
script mid-drive — and it worked. The mower stopped.

---

## The unintentional security disclosure

One funny aside. The mower's UDP `SearchAck` reply contains all sorts of
diagnostic info: device name, model, MAC, current IP, configured AP. It
also contains, in plaintext, the SSID and password of the home Wi-Fi
network the mower is connected to. Any device on your LAN can broadcast
a `Search` packet and read your Wi-Fi credentials back.

That's not a vulnerability the AI found in two milliseconds. It's a
vulnerability anyone with `tcpdump` and twenty minutes of curiosity
finds on a Sunday. I'm sure the vendor would say it's not a "real" issue
because you have to already be on the LAN — and they'd be technically
right. But it's the kind of design choice that makes you think about
who's actually doing the security work on consumer IoT devices, and what
shortcuts they're taking under deadline pressure.

I rotated the Wi-Fi password.

---

## What the AI actually did

Here's the part the headlines miss. "AI found 23,000 vulnerabilities in
two milliseconds" suggests a hands-off process where you feed the AI a
device and out comes a report. That's not what happened here, and I'd
argue it's not what happens in the serious security research that *does*
use AI tools either.

What Claude did well:

- **Pattern recognition on byte streams.** Spotting the XOR-0x30 in 30
  seconds. Recognizing the checksum target across multiple captures.
  These are tasks where having seen a lot of protocols in the past
  actually helps, and an LLM has seen a lot.
- **Writing structured Python code.** Encoding, decoding, the
  `MowerClient` class with a clean API, the failsafe-wrapped REPL — all
  drafted quickly and correctly, with sensible naming.
- **Honesty about what didn't add up.** When the set-time bytes didn't
  match what I thought I'd set on the phone, Claude flagged the
  discrepancy and asked for a second capture instead of confabulating a
  parse that worked. That's the trait I value most when the AI is
  collaborating, not autocompleting.

What I did:

- **Captured the traffic.** PCAPDroid, the phone, button presses, picking
  the right scenarios to capture (what's the diff between idle and the
  remote panel? between forward and stop?).
- **Validated everything physically.** The AI doesn't know whether the
  mower beeped, started mowing, or drove into a wall. I do. Every
  hypothesis got a real-world test.
- **Made judgment calls about safety.** Sweeping unknown param bytes
  against a machine with a spinning blade is a decision a human should
  make, not an agent.
- **Decided what was actually worth doing.** The AI would happily have
  continued reverse-engineering the opaque 32-byte telemetry response.
  I decided I didn't care enough to spend another two hours on it.

That last one is important. AI assistance compresses the *implementation*
work — coding, parsing, structuring — to nearly zero. It doesn't
compress the parts that need a human: capturing scenarios, deciding what
matters, and putting hands on the physical thing.

---

## So is Claude Mythos the threat, or the appliance in your garden?

The honest answer is: both, but in different ways and for different
people.

Mythos, if the reporting is accurate, is the threat to nation-state-tier
infrastructure. Twenty-seven-year-old OpenBSD bugs, chained zero-days,
72% exploit success rates — those aren't problems your lawn mower has
or that you can do anything about. The institutional response (US
government slowing access expansion, vetted partners, Project Glasswing
guardrails) suggests the people responsible for that tier of risk are
at least *taking it seriously*, even if the marketing reads breathlessly.

What I did with the mower is something else. It's not a vulnerability
discovery in the Mythos sense — there's nothing to discover. The mower
has no authentication, no session keys, no replay protection, no rate
limiting, single-byte "obfuscation," and *broadcasts the home Wi-Fi
password to anyone on the LAN who asks*. That's not a bug. That is, as
shipped, the design. None of this would have surprised anyone who has
looked at consumer IoT in the last decade.

What's new is that I'm not a security researcher. I'm an engineer with
a hobby project and an AI assistant. The barrier to doing what I did
used to be "patient electrical engineering student with a weekend free."
Now the barrier is "Sunday afternoon, mediocre Python, and a willingness
to read hex dumps." Claude carried the parsing, the structuring, the
checksum algebra, the package design, and most of the writing of this
article. I supplied curiosity, the physical robot, and the judgment
calls about which buttons to press and which to leave alone.

Two things this is *not* an argument for:

- **It is not an argument that Mythos is overhyped.** The claims I can
  verify externally — the OpenBSD bug, the model card, the IEEE
  coverage — are serious work by serious people. Discounting the
  marketing factor still leaves you with something that matters.
- **It is not an argument that your lawn mower will be hacked tomorrow.**
  Most attackers don't care about your lawn mower. The threat model is
  not "an adversary specifically targets you"; it's "the next time
  vendor firmware leaks credentials at scale, or a botnet operator
  decides garden robots make good DDoS amplifiers, it costs them
  approximately zero engineering hours to weaponise."

What it *is* an argument for is that the floor of what amateur-tier
reverse-engineering can do has dropped, and the long tail of unscripted,
unsecured, unpatchable consumer IoT is much closer to the surface than
it used to be. A robot in my garden, fifty more in my neighborhood. None
of us were notified that any of this was scriptable. Now, evidently, it
is.

If that doesn't surprise you, good — it shouldn't. But if your reaction
to Mythos was *"impressive but not my problem"*, you might want to
revisit which devices on your home network you assume are well-behaved.
The lawn mower wasn't.

---

## The repo

If you have an E1800, an M10, or any other mower with the EGROBOT app,
the full library is here:

**https://github.com/johanmullernaspegren/robo-lawn-mover**
*(replace with your actual repo URL when you publish)*

It includes:

- `MowerClient` — a context-manager Python client with `forward`,
  `reverse`, `left`, `right`, `auto`, `home`, `blade`, `stop`,
  `initiate_remote`, `set_time`, `poll`, `state`, and `version` methods.
- A CLI: `python -m mower forward 192.168.68.108`.
- An interactive REPL with the three-layer STOP failsafe.
- The complete protocol breakdown in the README.
- All of the PCAPDroid captures that got us there.

You'll need the mower's local IP (`ping` from your laptop, after a DHCP
reservation), and you'll need to close the official app before connecting
— the mower accepts one TCP client at a time.

Be careful with the `blade` command. That's a literal spinning blade.

---

*This project was built collaboratively with Claude. The code, the
checksum derivation, the package layout, and most of the prose drafting
were AI-assisted. The capturing, the physical testing, the judgment
calls, and the beer were not. — Johan*

*Claude Mythos numbers and capabilities are drawn from public press
coverage and Anthropic's own red-team write-ups, linked above. Treat
the marketing language with appropriate scepticism; treat the
underlying engineering as real until proven otherwise.*
