# Intelligent Octopus Go billing checker

Finds half-hourly slots where an Intelligent Octopus Go bill charges the standard unit rate for
consumption that is too large to be genuine household load — the signature of EV charging being
recorded out of step with the off-peak window created for it.

## Run the web app

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

Or without activating: `.\.venv\Scripts\streamlit.exe run app.py`

To recreate the environment from scratch:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use `py -3.12` rather than plain `python`. On this machine `python` resolves to the miniforge
base (3.10); running `python -m venv .venv` over an existing 3.12 venv rewrites the interpreter
and `pyvenv.cfg` but leaves the previously installed wheels in place, which produces a venv that
fails with `ModuleNotFoundError` on transitive dependencies. If that happens, delete `.venv` and
recreate it. `.\.venv\Scripts\python.exe -m pip check` will confirm the environment is sound.

The exploratory scripts in `src/explore_*.py` additionally need `matplotlib`, which is deliberately
kept out of `requirements.txt` so the deployed app stays lean.

Then upload your Octopus half-hourly export, and optionally a myenergi-style charger export.
Unit rates, the charger column and the date format are read from your files, so it is not tied to
one tariff or one household.

### Export formats

Both Octopus half-hourly formats are accepted and auto-detected:

- **Split format** (from roughly mid-July 2026): `Home Consumption (kWh)`, `Home Unit Rate (p)`,
  `EV Consumption (kWh)`, `EV Unit Rate (p)`.
- **Total-only format** (older): `Consumption (kWh)`, `Estimated Cost Inc. Tax (p)`,
  `Standing Charge Inc. Tax (p)`. There is no published unit rate, so it is recovered as
  cost / consumption, and flagging runs against total consumption rather than the home share.

Several files can be uploaded at once to build a continuous history. They are merged on slot
timestamp; where periods overlap the split format wins.

## Deploy it for others

Push this repo to GitHub and point [share.streamlit.io](https://share.streamlit.io) at `app.py`.
`requirements.txt` is all the config it needs. Uploaded files are held in memory for the session
only and are never written to disk.

## What the app shows

| Tab | Contents |
| --- | --- |
| Case studies | Per-day charts of billed "home" vs "EV" load, the charger's own record, and the slots that actually carried the off-peak rate |
| Timing evidence | How far the consumption series must be shifted to line up with the tariff, the mean load profile around off-peak windows, and a meter-vs-charger offset scan |
| Suspect slots | The flagged half hours with per-slot overpayment, downloadable as CSV |

The timing test on the first chart uses only the Octopus file, so it is unaffected by how a charger
labels its hours — that is the test which isolates an error on Octopus's side.

## Layout

```
app.py                  Streamlit app
src/analysis.py         Shared analysis: loading, anomaly detection, offset estimation, costing
src/verify_analysis.py  Checks the module reproduces the original findings
src/energy_data.py      Loaders used by the exploratory scripts
src/explore_*.py        The original investigation, step by step
data/                   Local exports (not required by the app)
```
