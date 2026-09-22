# Fly//Flirt

Two people chat (male/female "flirt" mode, or friends). Each message is scored by a Groq LLM into 7 parameters
that drive a real MaleCNS fruit-fly connectome subgraph (1,350 cells, 50,158 synapses). The chat page shows the
firing live in 3D; the verdict page attributes every activated neuron/connection to the message that caused it.

## Run
    python -m venv .venv && .venv\Scripts\pip install -r requirements-runtime.txt
    copy .env.example .env   # set GROQ_API_KEY (LLM_API_KEY also accepted), SECRET_KEY
    python app.py            # http://localhost:5000
    python -m unittest discover -s tests

## Deploy (Fly.io)
    fly launch --no-deploy --copy-config
    fly volumes create flyflirt_data --size 1
    fly secrets set SECRET_KEY=$(openssl rand -hex 32) GROQ_API_KEY=...
    fly deploy
Rooms are in memory, so run **exactly one machine** (gunicorn `-w 1 --threads 100`). SQLite in `/data` restores rooms after a restart.

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
**Report** (categories, one per reporter per room, rate-limited, three different reporters in 24 h auto-ban for 1 h).
Reports are stored in SQLite (`reports` table); there is no admin UI yet.
