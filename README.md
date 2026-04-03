# Get Latest

Get Latest is a responsive trend-search website that blends:

- Bluesky public posts
- Reddit public threads
- Hacker News stories
- Optional OpenAI summaries

## What it does

- Landing page shows a live top-10 feed built from Bluesky hot posts, Reddit popular threads, and Hacker News front-page stories.
- Suggested topics update automatically from current most-read Wikipedia topics as a free global-trend signal.
- Search mode combines:
  - Bluesky latest matches
  - Bluesky top matches
  - Reddit latest matches
  - Reddit top matches
  - Hacker News latest matches
  - Hacker News front-page matches
- Each feed gets a summary card.
- If `OPENAI_API_KEY` is configured, the backend upgrades the summary using OpenAI.
- If no key is set, the app still works and uses a lightweight local fallback digest.

## Run locally

1. Create a local env file:

   Copy `.env.example` to `.env`

2. Add your OpenAI API key to `.env` if you want AI summaries.

3. Start the server:

   ```powershell
   python server.py
   ```

4. Open:

   [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Host it on the internet

This app has a Python backend, so do not use GitHub Pages for deployment. GitHub Pages only hosts static files.

The easiest option is Render:

1. Push your code to GitHub.
2. Go to Render and create a new Blueprint or new Web Service from this repo.
3. Render can use the included `render.yaml` file in the repo root.
4. If prompted for environment variables, set:
   - `OPENAI_API_KEY` if you want AI summaries
   - `OPENAI_MODEL` optionally, default is `gpt-5-mini`
5. Deploy.

The app is already set up to read Render's `PORT` environment variable and bind to `0.0.0.0`.

## Notes

- Bluesky, Reddit, and Hacker News data is fetched server-side.
- OpenAI is optional, but recommended for better summaries.
- This app uses a Python standard-library server, so there are no package installs required.
