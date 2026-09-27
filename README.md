# Villager Explorer

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

`$env:NETWORK_TESTS="1"; pytest` also runs a live check that concurrent image fetches reject untrusted TLS certificates.

## How it's built

```
app.py              entry point + navigation
src/data.py         load + clean the committed CSV (cached)
src/filters.py      the shared filtering engine every view uses
src/images.py       fetch -> validate -> resize -> cache; any failure -> placeholder
src/components.py   villager card + card grid
views/browse.py     Browse & Filter
```

Images are fetched server-side, decoded and re-encoded before they reach the browser, so a dead or wrong image URL shows a placeholder, never a broken-image icon.

## Data

`data/villagers.csv` is a committed snapshot of 490 villagers from [Nookipedia](https://nookipedia.com)'s public Cargo tables. The app never fetches data live; only images are loaded at runtime. To refresh the snapshot:

```powershell
python scripts/build_dataset.py
```

Notes for Animal Crossing fans:

- **Catchphrases** are the New Horizons ones wherever a villager appears in New Horizons, and the series-wide catchphrase otherwise. They differ for a few villagers (Pudge says "pudgy" in New Horizons, "golly" series-wide).
- **73 villagers** appear only in games before New Horizons. Nookipedia has no hobby, favourite styles/colours or favourite song for them; the app shows those as "Unknown" and never drops them silently.

Villager data courtesy of Nookipedia, licensed [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). This is a fan-made tool, not affiliated with or endorsed by Nintendo.
