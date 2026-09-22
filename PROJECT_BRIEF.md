# Villager Explorer — Project Brief

**An interactive web app for exploring the ~480 Animal Crossing villagers — browse and filter them, find ones that match your preferences, and compare them side by side. Built and deployed as a live Streamlit application.**

This document is the specification for the project: what it is, how it's built, and what "done" looks like. It sits alongside the code so a recruiter skimming for thirty seconds and an engineer reading the source both understand the work and the reasoning behind it.

---

## 1. What this is, and why it exists

This is a deliberate change of form from the rest of the portfolio. The other projects are analytical studies delivered as notebooks — rigorous, but they all read as "Jupyter Notebook" on GitHub and none of them is something a person can *use*. This project fills that gap: a deployed, interactive tool with a real UI, a **live clickable link**, and a codebase that reads as **Python application code** rather than a notebook. The point being demonstrated here is not another statistical finding — it is that I can build and ship a clean, usable interactive application.

Because the point is the engineering and the interactivity rather than an analytical result, the quality bar sits in a different place than the other projects. There is no clever finding to lean on. So the app has to be *genuinely good to use* — fast, polished, thoughtfully designed, and robust to the messy realities of real data (dead image links, missing fields). The honest-rigour signature that runs through the whole portfolio moves here from *the analysis* to *the engineering*: clean modular code, sensible state handling, gracefully handled edge cases, and a real README. A filter tool that's clunky reads as decorative; one that's polished and robust reads as "this person can build good software."

## 2. The dataset

**Primary: the Nookipedia "Animal Crossing villagers (entire series)" dataset — ~480 villagers, CC BY-SA 3.0.** It carries exactly the fields this app needs: name, species, personality, gender, birthday, hobby, catchphrase, favourite song, styles/colours, and — critically — an **image URL** per villager. The licence is permissive with attribution, which the app and repo will provide (a credit line to Nookipedia, per their terms).

**Fallback:** the TidyTuesday 2020-05-05 `villagers.csv` (~391 NH villagers) if the Nookipedia set proves awkward to obtain cleanly — smaller field set but the same core dimensions and an image URL. The app is built to the richer schema; degrading to the smaller one is a documented contingency, not the plan.

**The data reality to design around:** the images live at external wiki URLs. External image links rot, rate-limit, or fail intermittently. **Rendering images gracefully — a clean placeholder when a URL fails, never a broken-image icon — is therefore a core requirement, not polish.** How the app handles a dead image link is one of the clearest engineering-quality signals in the whole project. The villager data itself is committed to the repo (it's small and static) so the app runs without a live data fetch; only the images are fetched at runtime.

This data is a static reference catalogue of fictional characters — there is no outcome variable, no inference, no "is this effect real." That is fine and expected: this project's value is the tool, not a finding. The README will be honest that this is an exploration/utility app, not an analytical study.

## 3. What the app does — one spine, three views

The app has **one spine and two views built on it**, sharing the same data and the same filtering logic. This hierarchy is deliberate: one primary interaction, two that extend it, so the app reads as thoughtfully designed rather than a grab-bag of features.

**Browse & Filter (the spine, and the landing view).** Filter the full roster by species, personality, gender, hobby (and any other clean categorical field), with a text search by name. The matching villagers render as a grid of cards — image, name, and key attributes — updating live as filters change. This is what the app is fundamentally *for*: "show me the villagers that match these criteria."

**Find Your Match (a view built on the spine).** Instead of setting filters directly, the visitor answers a few light preference questions (favourite species? preferred personality? a hobby you like?) and the app surfaces the villagers that best fit. Mechanically this is the same filtering engine driven by a friendlier, more playful front end — the memorable hook that makes the app fun rather than merely functional.

**Compare (a view built on the spine).** Pick two (or a few) villagers and see their attributes side by side, images included. Useful for the "which of these should I invite" decision the game actually poses.

All three read from one shared data layer and one shared set of filtering functions. A visitor should be able to move between the views without losing their place — shared state, handled correctly, is part of the engineering being demonstrated.

## 4. How it's built

- **Streamlit**, current version, as the app framework — chosen because it turns Python directly into a deployed interactive web app with no separate front-end stack, which fits both the goal (a live Python app) and the timeframe.
- **Multi-view structure via `st.navigation` / `st.Page`** (the current preferred Streamlit multipage mechanism), or `st.tabs` if a single-page feel is better for three lightweight views — the build will choose based on which gives cleaner shared-state behaviour. Either way, `st.session_state` carries state across views; the known trap (navigation resetting state, or callback/rerun feedback loops) is handled deliberately, not stumbled into.
- **Data cached** with Streamlit's caching so the CSV loads once, not on every rerun.
- **Images fetched at runtime with a graceful fallback** — a placeholder rendered whenever a URL fails, so the grid never shows broken images.
- **Code factored into modules** — data loading, filtering logic, and the card/rendering components separated from the view code, so the three views genuinely share one implementation (single source of truth) rather than each re-implementing filtering. This modular structure is itself part of what the project demonstrates.
- **Deployed to Streamlit Community Cloud** (free, connects straight to the GitHub repo) → the live clickable link.
- **Seeds/config and pinned `requirements.txt`** so it's reproducible and deploys cleanly.

## 5. What "done" looks like — the standard

**For the recruiter / hiring manager (first thirty seconds):** they click the live link, the app loads fast, and within seconds they're filtering villagers and seeing images render — it's immediately, obviously an interactive tool they can *use*, not a notebook to read. The GitHub repo reads as a Python application. It's visibly a different kind of artifact from the rest of the portfolio.

**For the engineer reading the source:** the code is clean and modular; filtering logic lives in one place and all three views use it; state is handled correctly across views (no lost-place-on-navigation, no rerun loops); images fail gracefully; the data is cached; dependencies are pinned; and the README explains how to run it locally and where the live version is. Nothing is half-working — a flaky deployed app is worse than none, so the bar is "it runs cleanly and handles the messy cases," not "it has the most features."

Both must be true. The first proves it's interactive and shipped; the second proves it's *well* built. And the app is honest about what it is — a polished exploration tool for a fun dataset, deliberately different from the analytical projects, demonstrating the interactivity and deployment those projects don't.

## 6. Repository structure

Following the portfolio conventions — lowercase-hyphen naming, one project per repo. Note this repo is *app code*, not notebooks, so the structure differs from the analytical repos by design:

```
villager-explorer/
├── README.md                 # what it is, live link, screenshots, run instructions, data credit
├── requirements.txt          # pinned (streamlit, pandas, requests/pillow as needed)
├── app.py                    # entry point + navigation
├── src/
│   ├── data.py               # load + cache the villager data
│   ├── filters.py            # the shared filtering logic (single source of truth)
│   └── components.py         # villager card / image-with-fallback rendering
├── views/
│   ├── browse.py             # Browse & Filter (the spine)
│   ├── match.py              # Find Your Match
│   └── compare.py            # Compare
├── data/
│   └── villagers.csv         # committed static villager data
└── assets/
    └── placeholder.png       # fallback image
```

(A companion **Kaggle notebook** — a light exploration of the same dataset — is published separately for the Kaggle audience; the GitHub repo is the app's home and the live link.)

## 7. The build sequence

Built so there's a runnable, deployable thing early, then progressively better — because a deployed app benefits from being live and iterated, unlike an analysis that's written once.

1. **Skeleton + environment + data loading.** Repo structure, pinned `requirements.txt`, the villager data committed and loading via a cached `src/data.py`. Verify it loads and the fields are clean.
2. **The shared filtering engine + the Browse & Filter spine.** `src/filters.py` and `src/components.py` (including the image-with-fallback component), and the browse view rendering the filtered grid. This is the core; get it genuinely good — fast, clean, robust images — before adding views.
3. **Deploy early.** Get the spine live on Streamlit Community Cloud as soon as it works, so the rest is built against a real deployment and deployment problems surface early, not at the end.
4. **Find Your Match view**, built on the shared filtering engine.
5. **Compare view**, built on the shared engine; shared state across all three views verified.
6. **Polish + README.** Visual polish, empty-state and edge-case handling, then the README (live link, screenshots, run instructions, Nookipedia attribution) written last.

Commits stage by stage, honest history.

## 8. Honest notes

- **This is a utility/exploration app, not an analytical study** — stated plainly in the README. Its job in the portfolio is to demonstrate interactivity, application-building, and deployment, which the notebook projects don't.
- **The data is a static catalogue of fictional characters** — no inference, by design.
- **Images depend on external URLs** and are handled with a fallback; if wiki URLs change en masse, the graceful degradation keeps the app usable (cards without images) rather than broken.
- **Attribution:** villager data from Nookipedia (CC BY-SA 3.0), credited in-app and in the README per the licence.

---

*Villager data courtesy of Nookipedia (CC BY-SA 3.0). This is a fan-made exploration tool and is not affiliated with or endorsed by Nintendo.*
