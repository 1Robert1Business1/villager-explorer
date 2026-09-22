# Villager Explorer

An interactive Streamlit app for exploring the Animal Crossing villagers: browse and filter them, find a match, and compare them side by side.

> Work in progress. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the spec and build plan. The full README (live link, screenshots) comes at the end of the build.

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Data

`data/villagers.csv` is a committed snapshot of 490 villagers from [Nookipedia](https://nookipedia.com)'s public Cargo tables. The app never fetches data live; only images are loaded at runtime. To refresh the snapshot:

```powershell
python scripts/build_dataset.py
```

Villager data courtesy of Nookipedia, licensed [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). This is a fan-made tool, not affiliated with or endorsed by Nintendo.
