# Fly//Flirt

Two people chat (male/female "flirt" mode, or friends). Each message is scored by a Groq LLM into 7 parameters
that drive a real MaleCNS fruit-fly connectome subgraph (1,350 cells, 50,158 synapses). The chat page shows the
firing live in 3D; the verdict page attributes every activated neuron/connection to the message that caused it.

## Run
    python -m venv .venv && .venv\Scripts\pip install -r requirements-runtime.txt
    copy .env.example .env   # set GROQ_API_KEY (LLM_API_KEY also accepted), SECRET_KEY
    python app.py            # http://localhost:5000
    python -m unittest discover -s tests

## Deploy (Railway)
Rooms and the matchmaking queue live in memory, so this must always run as **exactly one instance** — don't
enable autoscaling or multiple replicas. SQLite lives on a volume so bans, blocks, LLM-usage counters and the
neural-activation record (see Privacy, below) survive a restart.

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → pick this repo. Railway
   detects the `Dockerfile` and builds it as-is; no changes needed.
2. Add a **Volume**, mount it at `/data` (Settings → Volumes).
3. Set variables (Settings → Variables): `SECRET_KEY` (`openssl rand -hex 32`), `GROQ_API_KEY`,
   `APP_ENV=production`, `DATA_DIR=/data`, `SESSION_COOKIE_SECURE=true`. Optionally `ADMIN_USERNAME` +
   `ADMIN_PASSWORD` to turn on the `/admin` reports page (see Admin page, below) — leave both unset to keep it off.
4. Make sure **Replicas is 1** and autoscaling is off (Settings → Deploy). Railway doesn't sleep a service
   that's receiving normal traffic, so no further "keep-alive" configuration is needed.
5. Deploy. Railway gives you a `*.up.railway.app` HTTPS domain immediately; add a custom domain later if you want one.

At 10-15 concurrent users (a handful of chat pairs) this comfortably fits Railway's smallest instance size —
each message costs roughly 8 ms of CPU on the standard connectome (up to ~45 ms on the optional full-pathway
one), and the whole process uses well under 512 MB of RAM.

**Why not Render's free tier:** it spins a service down after 15 minutes of inactivity and cold-starts on the
next request, which would silently kill any in-memory chat or matchmaking queue while nobody's browsing — a
dealbreaker for this app's in-memory design. Render's paid Starter tier removes that, but is no simpler than
Railway once you're paying anyway. **Fly.io** remains a solid, more "production-grade" option (this repo's
`Dockerfile` works there unchanged) if you'd rather manage it via the `flyctl` CLI and `fly.toml`.

## LLM fallback
Ordered chain (`LLM_CHAIN`) of Groq models with per-model circuit breakers and local RPM/TPM/RPD/TPD accounting.
If all are unavailable a built-in heuristic scorer answers instantly, so the app never stops.

## Layout
`flyflirt/` (engine, verdict, llm/, rooms, matchmaking, sockets, web/) · `templates/` · `static/` · `tools/` (offline connectome build) · `tests/`

## Full pathway (bigger brain)
`static/connectome_full.json` (4,300 cells, 152,284 synapse pairs) is an opt-in view: either person can flip
the "Full pathway" switch under the brain and the chat is replayed on the bigger graph for both. Rebuild it with
`python tools/build_connectome.py --context 4000 --out static/connectome_full.json` (needs `NEUPRINT_TOKEN`).
Env: `FULL_ENABLED`, `FULL_MAX_ROOMS` (concurrent full rooms, default 8; each step costs ~45 ms CPU),
`SIM_METER_SCALE_FULL` (re-fitted so both graphs read alike). Phones get a warning and a lighter render; the
page also warns if the frame rate drops below ~22 fps.

## Safety
Message filter (`flyflirt/moderation.py`: slurs, threats and sexual harassment are blocked and never reach the LLM;
profanity is starred out; emails, links and phone numbers are hidden), three strikes end the chat and ban for 15 min,
**Next person** (skipped pairs are not re-matched for 30 min), **Block** (permanent pairing ban, ~30 days),
**Report** (categories, one per reporter per room, rate-limited, three different reporters in 24 h auto-ban for 1 h),
delivery receipts ("Seen", purely live/in-memory — never written to disk), and a gentle "It's gone quiet, see your
verdict?" nudge after 5 minutes of silence in a verdict-ready chat (`IDLE_VERDICT_NUDGE_S`) that never ends or
redirects the chat on its own. Nicknames are compulsory (client pre-fills a random "emoji + adjective + noun" name
you can reroll or overwrite; server rejects a blank one too).

### Admin page
`/admin` lists unresolved reports and active bans, with buttons to resolve a report, ban the reported person for
24h, or lift a ban. It's disabled — every `/admin/*` route 404s, indistinguishable from not existing — unless
you set **both** `ADMIN_USERNAME` and `ADMIN_PASSWORD`. When set, it's gated by HTTP Basic Auth (only sensible
over HTTPS, which Railway/Fly give you by default) plus a per-session CSRF token on every action, and there's no
link to it from anywhere in the app. Pick a long random `ADMIN_PASSWORD`; it's compared with a timing-safe check
but is otherwise a plain shared secret, the same trust level as `SECRET_KEY`/`GROQ_API_KEY`.

## Privacy: what's actually on disk
**Your words are not written to the server's database in a real deployment.** While a chat is live, the process
always keeps the full transcript in memory so both people can see it and so each message can be sent to Groq for
scoring — that's unavoidable to make the feature work at all. What happens when a message is *persisted* to
SQLite (for verdicts and reconnects to survive a restart) depends on how the app is running:

- **`APP_ENV=production`** (the Railway guide above sets this) → text is **not** kept by default: only the
  *numbers* derived from a message are stored (the 7 LLM parameters, which neurons/synapses fired, which AI
  model was used). The message text and the fly's diary/tip paraphrase of it are stored as empty.
- **Anything else** (e.g. `python app.py` off your own clone, `APP_ENV` unset) → text **is** kept by default,
  since it's your own machine and a transcript is usually more useful there than a privacy risk.
- **`RETAIN_MESSAGE_TEXT=true`/`false`** overrides either default explicitly, server-wide.
- **Either person in a chat can override the server's choice for that one conversation**, in either direction,
  via the "Save this chat" switch under Chat options — e.g. opting out even on a server that keeps text by
  default, or opting in on one that doesn't. It only affects messages sent after the switch is changed.

Practical effect either way: the neural simulation and verdict are bit-for-bit reproducible after a server
restart; a chat whose text wasn't retained shows "(message text was not stored on the server)" in place of old
messages once restored from disk. Fully tested both ways (`tests/test_flow.py::RestoreTests`, and the
`set_privacy` tests in `ChatFlowTests`).

## Security notes
- Strict CSP (`script-src 'self'`, no inline scripts anywhere), CORS defaults to same-origin
  (`ALLOWED_ORIGINS` unset), HttpOnly + SameSite=Lax session cookie, `SESSION_COOKIE_SECURE` should be set
  `true` in production (see deploy steps above). `SECRET_KEY` is required and the app refuses to start
  without one when `APP_ENV=production`.
- Every state-changing socket event and the report endpoint is behind a per-IP or per-client token bucket
  (`flyflirt/util.py::TokenBucket`); nicknames and chat text are sanitised server-side regardless of what the
  client sends (`flyflirt/util.py::clean_nickname/clean_text`), and the DOM is built with `textContent`
  everywhere (`static/js/common.js`), never `innerHTML`, so user input can't inject markup.
- Runtime dependencies are version-pinned (`requirements-runtime.txt`) to what's actually tested, rather than
  left to float on every fresh deploy.
- Not done for you: a WAF/CDN in front (Cloudflare or similar) if you expect abuse traffic beyond what the
  built-in rate limits handle.
