# Villager Explorer

[![tests](https://github.com/1Robert1Business1/villager-explorer/actions/workflows/tests.yml/badge.svg)](https://github.com/1Robert1Business1/villager-explorer/actions/workflows/tests.yml)

An interactive Streamlit app for exploring the Animal Crossing villagers: browse and filter them, find a match, and compare them side by side.

> Work in progress. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the spec and build plan. The full README (live link, screenshots) comes at the end of the build.

## Run locally (Windows PowerShell)

Requires Python 3.11 or newer (pandas 3); developed on 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Tests:

```powershell
pip install -r requirements-dev.txt
pytest
```

`$env:NETWORK_TESTS="1"; pytest` also runs a live check that concurrent image fetches reject untrusted TLS certificates. GitHub Actions runs the suite on Linux (Python 3.12, as deployed) on every push, and the live TLS check weekly.

## How it's built

```
app.py                entry point + navigation
src/data.py           load + clean the committed CSV (cached)
src/filters.py        the shared filtering engine every view uses
src/images.py         fetch -> validate -> resize -> cache; any failure -> placeholder
src/components.py     villager card, progressive card grid, details dialog
views/browse.py       Browse & Filter
views/diagnostics.py  deployment monitoring (see below)
```

- **Images** are fetched server-side, decoded and re-encoded before they reach the browser, so a dead or wrong image URL shows a placeholder, never a broken-image icon. The grid paints immediately and images fill in as they arrive.
- **Security:** the fetcher only talks HTTPS to an allowlisted image host and never follows redirects (no SSRF). Dataset text is escaped before it reaches markdown or HTML. Name-chip text colour is chosen for WCAG AA contrast on every villager's colour.

### Deployment monitoring

`/diagnostics` (not linked in the navigation) reports the running deployment's health: Python, platform and package versions, memory use against the container limit, image-cache size and timings, and a TLS self-test. The self-test fetches concurrently from the image host and from a host with an untrusted certificate, and passes only if images load and every bad certificate is rejected.

## Data

`data/villagers.csv` is a committed snapshot of 490 villagers from [Nookipedia](https://nookipedia.com)'s public Cargo tables. The app never fetches data live; only images are loaded at runtime. To refresh the snapshot:

```powershell
python scripts/build_dataset.py
```

Notes for Animal Crossing fans:

- **Catchphrases** are the New Horizons ones wherever a villager appears in New Horizons, and the series-wide catchphrase otherwise. They differ for a few villagers (Pudge says "pudgy" in New Horizons, "golly" series-wide).
- **73 villagers** appear only in games before New Horizons. Nookipedia has no hobby, favourite styles/colours or favourite song for them; the app shows those as "Unknown" and never drops them silently.

Villager data courtesy of Nookipedia, licensed [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). This is a fan-made tool, not affiliated with or endorsed by Nintendo.
