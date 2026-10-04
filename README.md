# Thittam: Regional-Language AI Agent for Welfare Schemes
An AI agent that tells citizens which government schemes they qualify for, in their own language (voice or text). Tamil Nadu is the first state; central schemes work everywhere; a new state is one JSON file (`schemes_<state>.json`).

## Why it is agentic
Gemini extracts the profile from conversation (JSON), asks a follow-up question for missing info, then deterministic code matches schemes and returns documents, conditions and official links. Eligibility logic is deterministic code, so the LLM never invents amounts or rules.

## Run
```
pip install -r requirements.txt
export GEMINI_API_KEY=your_key   # free key: aistudio.google.com
python app.py   # open http://localhost:5000
```

## Architecture
- `data/<ISO country>/*.json`: curated packs. Rules are deterministic code; the LLM never invents eligibility.
- Countries without a pack: the agent does cited web research limited to official government sources and labels results **AI-researched, unverified**.
- Add a country: create `data/<ISO>/<name>.json` using the same schema.

## Data verification status
Each scheme has `verification` and `last_checked`. Verified so far (Oct 2026): PM-KISAN (government sources). Kalaignar Magalir Urimai Thogai is partially verified (third-party sources). All others are **unverified** and the app labels them so. Contribute verified data via `schemes_<state>.json`. Rules change; Tamil Nadu had an election in 2026.

## Deploy (Render, free tier)
1. Push this repo to GitHub. 2. On render.com choose New > Web Service, connect the repo (it reads `render.yaml`). 3. Add `GEMINI_API_KEY` as an environment variable. 4. Open the URL and test.

## Demo script (2-3 min)
1. Problem: millions miss welfare benefits because information is hard to find in their language.
2. Speak in Tamil (mic) as a farmer; the agent asks a follow-up, then lists schemes with documents and official links.
3. Switch country to United States, age 67: Medicare appears. Switch to an unsupported country: the agent researches official sources and labels results unverified.
4. Close: curated packs plus honest AI research; adding a country is one JSON file.
