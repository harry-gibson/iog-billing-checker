"""Core analysis shared by the notebook and the web app.

Deliberately free of hard-coded file paths, tariff rates and column names so it can run against
any Intelligent Octopus Go export plus an optional charger export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

import numpy as np
import pandas as pd

DEFAULT_TZ = "Europe/London"

_OCTOPUS_COLUMNS = {
    "home consumption": "home_kwh",
    "estimated home cost": "home_cost_p",
    "home unit rate": "home_rate_p",
    "ev consumption": "ev_kwh",
    "estimated ev cost": "ev_cost_p",
    "ev unit rate": "ev_rate_p",
    "start": "start",
    "end": "end",
}


@dataclass
class Settings:
    """Tunable thresholds. Defaults match the analysis the app was built from."""

    implausible_kw: float = 5.0
    charge_event_kwh: float = 3.0
    charger_on_kw: float = 1.0
    fixed_cheap_start: time = time(23, 30)
    fixed_cheap_end: time = time(5, 30)
    max_case_studies: int = 12
    tz: str = DEFAULT_TZ
    baseline_kw: float | None = None  # None => infer from quiet slots
    assumed_shift_slots: int = 3  # 90 minutes, used for the re-billing estimate


# --------------------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------------------

def _match_octopus_columns(columns) -> dict:
    mapping = {}
    for col in columns:
        key = col.strip().lower()
        for needle, name in _OCTOPUS_COLUMNS.items():
            if key == needle or key.startswith(needle):
                mapping[col] = name
                break
    return mapping


def read_octopus(src, tz: str = DEFAULT_TZ) -> pd.DataFrame:
    """Half-hourly Octopus export, in either the home/EV split format or the older total-only one.

    Accepts a single source or a list of them, so an older export can be joined onto a newer one.
    """
    if isinstance(src, (list, tuple)):
        return _merge_octopus([read_octopus(s, tz) for s in src])

    df = pd.read_csv(src, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    keys = [c.lower() for c in df.columns]

    if any(k.startswith("home consumption") for k in keys):
        return _read_octopus_split(df, tz)
    if any(k.startswith("consumption") for k in keys):
        return _read_octopus_total(df, tz)
    raise ValueError(
        "Unrecognised Octopus export: expected a 'Home Consumption (kWh)' column (newer split "
        f"format) or a 'Consumption (kWh)' column (older format). Found: {list(df.columns)}"
    )


def _read_octopus_split(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    df = df.rename(columns=_match_octopus_columns(df.columns))
    missing = {"home_kwh", "ev_kwh", "home_rate_p", "start"} - set(df.columns)
    if missing:
        raise ValueError(f"Octopus export is missing expected columns: {sorted(missing)}")
    for col in ("home_cost_p", "ev_cost_p", "ev_rate_p"):
        if col not in df.columns:
            df[col] = np.nan
    return _finalise_octopus(df, tz, has_ev_split=True)


def _read_octopus_total(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Older export: one consumption figure, no home/EV split and no published unit rate."""
    rename = {}
    for col in df.columns:
        key = col.strip().lower()
        if key.startswith("consumption"):
            rename[col] = "home_kwh"
        elif key.startswith("estimated cost"):
            rename[col] = "home_cost_p"
        elif key == "start":
            rename[col] = "start"
        elif key == "end":
            rename[col] = "end"
    df = df.rename(columns=rename)

    missing = {"home_kwh", "home_cost_p", "start"} - set(df.columns)
    if missing:
        raise ValueError(f"Octopus export is missing expected columns: {sorted(missing)}")

    df["ev_kwh"] = 0.0
    df["ev_cost_p"] = 0.0
    df["ev_rate_p"] = np.nan
    # The unit rate is not published in this format, so recover it from cost / consumption.
    rate = df["home_cost_p"] / df["home_kwh"].replace(0, np.nan)
    df["home_rate_p"] = rate.ffill().bfill()
    return _finalise_octopus(df, tz, has_ev_split=False)


def _finalise_octopus(df: pd.DataFrame, tz: str, has_ev_split: bool) -> pd.DataFrame:
    df["start"] = pd.to_datetime(df["start"], format="ISO8601", utc=True).dt.tz_convert(tz)
    if "end" in df.columns:
        df["end"] = pd.to_datetime(df["end"], format="ISO8601", utc=True).dt.tz_convert(tz)
    df = df.sort_values("start").reset_index(drop=True)

    df["total_kwh"] = df["home_kwh"] + df["ev_kwh"]
    df["total_cost_p"] = df["home_cost_p"].fillna(0) + df["ev_cost_p"].fillna(0)
    df["home_kw"] = df["home_kwh"] * 2
    df["ev_kw"] = df["ev_kwh"] * 2
    df["hour_start"] = df["start"].dt.floor("h")

    low, high = detect_rates(df)
    df["cheap"] = df["home_rate_p"] <= (low + high) / 2
    df.attrs["has_ev_split"] = has_ev_split
    return df


def _merge_octopus(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate exports, preferring the split format wherever their periods overlap."""
    tagged = []
    for frame in frames:
        frame = frame.copy()
        frame["_split"] = bool(frame.attrs.get("has_ev_split", True))
        tagged.append(frame)
    merged = (pd.concat(tagged, ignore_index=True)
              .sort_values(["start", "_split"])
              .drop_duplicates(subset="start", keep="last")
              .reset_index(drop=True))
    has_ev_split = bool(merged["_split"].any())
    merged = merged.drop(columns="_split")
    merged.attrs["has_ev_split"] = has_ev_split
    return merged


def detect_rates(oct_df: pd.DataFrame) -> tuple[float, float]:
    """Off-peak and standard unit rates, taken from the data rather than assumed.

    Uses the median of each cluster so a rate recovered from cost / consumption is not thrown
    off by rounding noise in a single slot.
    """
    rates = oct_df["home_rate_p"].replace([np.inf, -np.inf], np.nan).dropna()
    if rates.empty:
        raise ValueError("No unit rates found in the Octopus export.")
    low, high = float(rates.min()), float(rates.max())
    if high - low < 1e-6:
        return low, high
    midpoint = (low + high) / 2
    return float(rates[rates <= midpoint].median()), float(rates[rates > midpoint].median())



def charger_column_options(src) -> list[str]:
    """Candidate device columns in a myenergi-style export, for the user to choose between."""
    head = pd.read_csv(src, nrows=0)
    if hasattr(src, "seek"):
        src.seek(0)
    cols = [c.strip() for c in head.columns]
    return [c for c in cols if c.lower() not in ("timestamp", "time", "date") and "home" not in c.lower()]


def read_charger(src, tz: str = DEFAULT_TZ, charger_col: str | None = None,
                 dayfirst: bool | None = None) -> pd.DataFrame:
    """Hourly charger export. Values are mean kW over the hour, i.e. numerically equal to kWh."""
    df = pd.read_csv(src)
    df.columns = [c.strip() for c in df.columns]

    ts_col = next((c for c in df.columns if c.lower() in ("timestamp", "time", "date")), df.columns[0])
    home_col = next((c for c in df.columns if c.lower().startswith("home")), None)
    if charger_col is None:
        candidates = [c for c in df.columns if c != ts_col and c != home_col]
        if not candidates:
            raise ValueError("No charger column found in the charger export.")
        charger_col = candidates[0]

    df = df.rename(columns={charger_col: "charger_kwh"})
    df["house_kwh"] = df[home_col] if home_col else np.nan
    df["hour_start"] = _parse_naive_timestamps(df[ts_col], dayfirst).dt.tz_localize(
        tz, ambiguous=True, nonexistent="shift_forward"
    )
    df = df[["hour_start", "charger_kwh", "house_kwh"]].sort_values("hour_start").reset_index(drop=True)
    df["charger_total_kwh"] = df["charger_kwh"] + df["house_kwh"].fillna(0)
    return df


def _parse_naive_timestamps(s: pd.Series, dayfirst: bool | None) -> pd.Series:
    s = s.astype(str).str.strip().str.strip('"')
    if dayfirst is None:
        for fmt in ("%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
            try:
                return pd.to_datetime(s, format=fmt)
            except (ValueError, TypeError):
                continue
        return pd.to_datetime(s)
    return pd.to_datetime(s, dayfirst=dayfirst)


def combine(oct_df: pd.DataFrame, charger_df: pd.DataFrame | None) -> pd.DataFrame:
    """Half-hourly frame indexed on slot start, with the hourly charger series spread across it."""
    combined = oct_df.set_index("start")
    if charger_df is None or charger_df.empty:
        for col in ("charger_kwh", "house_kwh", "charger_total_kwh"):
            combined[col] = np.nan
        return combined
    ch = charger_df.set_index("hour_start")[["charger_kwh", "house_kwh", "charger_total_kwh"]]
    ch30 = ch.resample("30min").ffill() / 2
    return combined.join(ch30.reindex(combined.index))


# --------------------------------------------------------------------------------------
# billing anomalies
# --------------------------------------------------------------------------------------

def in_fixed_cheap_window(ts: pd.Series | pd.DatetimeIndex, cfg: Settings) -> pd.Series:
    """True inside the tariff's guaranteed off-peak block, which may wrap past midnight."""
    idx = pd.Series(ts) if not isinstance(ts, pd.Series) else ts
    minutes = idx.dt.hour * 60 + idx.dt.minute
    start = cfg.fixed_cheap_start.hour * 60 + cfg.fixed_cheap_start.minute
    end = cfg.fixed_cheap_end.hour * 60 + cfg.fixed_cheap_end.minute
    if start <= end:
        return (minutes >= start) & (minutes < end)
    return (minutes >= start) | (minutes < end)


def flag_misbilled(oct_df: pd.DataFrame, cfg: Settings) -> pd.DataFrame:
    """Slots whose 'home' load is too large to be genuine yet were charged the standard rate."""
    low, high = detect_rates(oct_df)
    df = oct_df.copy()
    df["in_fixed_window"] = in_fixed_cheap_window(df["start"], cfg).values
    df["implausible"] = df["home_kw"] > cfg.implausible_kw
    df["standard_rate"] = df["home_rate_p"] > (low + high) / 2
    df["misbilled"] = df["implausible"] & df["standard_rate"] & ~df["in_fixed_window"]
    df["overpayment_gbp"] = np.where(df["misbilled"], df["home_kwh"] * (high - low) / 100, 0.0)
    return df


def cost_summary(flagged: pd.DataFrame, cfg: Settings) -> dict:
    low, high = detect_rates(flagged)
    mis = flagged[flagged["misbilled"]]
    days = max(len(flagged) * 0.5 / 24, 1)  # slot coverage, not endpoint difference

    baseline_kwh = (cfg.baseline_kw / 2 if cfg.baseline_kw is not None
                    else float(flagged.loc[~flagged["implausible"], "home_kwh"].median()))
    excess = (mis["home_kwh"] - baseline_kwh).clip(lower=0)

    shifted = flagged.assign(home_kwh_shifted=flagged["home_kwh"].shift(cfg.assumed_shift_slots)).dropna(
        subset=["home_kwh_shifted"]
    )
    as_billed = float((shifted["home_kwh"] * shifted["home_rate_p"]).sum() / 100)
    realigned = float((shifted["home_kwh_shifted"] * shifted["home_rate_p"]).sum() / 100)

    gross = float(mis["overpayment_gbp"].sum())
    total_bill = float(flagged["total_cost_p"].sum() / 100)
    return {
        "low_rate_p": low,
        "high_rate_p": high,
        "days": days,
        "slots": int(len(mis)),
        "kwh": float(mis["home_kwh"].sum()),
        "charged_gbp": float(mis["home_cost_p"].sum() / 100),
        "at_offpeak_gbp": float(mis["home_kwh"].sum() * low / 100),
        "gross_overpayment_gbp": gross,
        "baseline_kw": baseline_kwh * 2,
        "conservative_overpayment_gbp": float(excess.sum() * (high - low) / 100),
        "total_bill_gbp": total_bill,
        "share_of_bill_pct": 100 * gross / total_bill if total_bill else np.nan,
        "annualised_gbp": gross * 365 / days,
        "rebill_as_billed_gbp": as_billed,
        "rebill_realigned_gbp": realigned,
        "rebill_difference_gbp": as_billed - realigned,
    }


# --------------------------------------------------------------------------------------
# timing offset
# --------------------------------------------------------------------------------------

def offset_scan(combined: pd.DataFrame, max_minutes: int = 240, step_minutes: int = 15) -> pd.DataFrame:
    """RMSE between the meter and the charger's own record as the charger series is slid in time."""
    if combined["charger_total_kwh"].isna().all():
        return pd.DataFrame(columns=["shift_min", "rmse", "r"])
    pair = combined[["total_kwh", "charger_total_kwh"]].dropna()
    meter, charger = pair["total_kwh"].to_numpy(), pair["charger_total_kwh"].to_numpy()
    positions = np.arange(len(charger))
    rows = []
    for minutes in range(-max_minutes, max_minutes + 1, step_minutes):
        shifted = np.interp(positions + minutes / 30, positions, charger)
        rows.append({
            "shift_min": minutes,
            "rmse": float(np.sqrt(((meter - shifted) ** 2).mean())),
            "r": float(np.corrcoef(meter, shifted)[0, 1]),
        })
    return pd.DataFrame(rows)


def octopus_only_offset(oct_df: pd.DataFrame, cfg: Settings, max_slots: int = 8) -> pd.DataFrame:
    """How much energy lands inside Octopus's own off-peak windows as the consumption series is slid.

    Uses nothing but the Octopus file, so it is unaffected by how the charger labels its hours.
    """
    df = oct_df.reset_index(drop=True)
    outside_fixed = ~in_fixed_cheap_window(df["start"], cfg).values
    triggered = (df["cheap"].values & outside_fixed)
    ev_flagged = (df["ev_kwh"] > 0.05).values

    quiet = df.loc[df["ev_kwh"].rolling(9, center=True, min_periods=1).max() == 0, "total_kwh"]
    if (df["ev_kwh"] > 0).sum() == 0:
        # No EV column to steer by, so treat anything below the implausible threshold as baseline.
        quiet = df.loc[df["total_kwh"] * 2 < cfg.implausible_kw, "total_kwh"]
    baseline = float(quiet.median()) if len(quiet) else 0.0
    excess = np.clip(df["total_kwh"].to_numpy() - baseline, 0, None)

    rows = []
    for slots in range(max_slots + 1):
        moved = np.concatenate([np.full(slots, np.nan), excess[: len(excess) - slots]]) if slots else excess
        rows.append({
            "shift_min": slots * 30,
            "kwh_in_triggered_windows": float(np.nansum(moved[triggered])),
            "kwh_in_ev_slots": float(np.nansum(moved[ev_flagged])),
        })
    return pd.DataFrame(rows)


def event_study(oct_df: pd.DataFrame, cfg: Settings, span_slots: int = 8) -> tuple[pd.DataFrame, int]:
    """Mean load profile around the start of each Octopus-triggered off-peak window."""
    df = oct_df.reset_index(drop=True)
    outside_fixed = ~in_fixed_cheap_window(df["start"], cfg)
    triggered = df["cheap"] & outside_fixed
    starts = df.index[triggered & ~triggered.shift(1, fill_value=False)]

    records = [
        {"offset_min": k * 30, "home_kw": df.at[i + k, "home_kw"], "ev_kw": df.at[i + k, "ev_kw"]}
        for i in starts
        for k in range(-span_slots, span_slots + 1)
        if 0 <= i + k < len(df)
    ]
    if not records:
        return pd.DataFrame(columns=["offset_min", "home_kw", "ev_kw"]), 0
    profile = pd.DataFrame(records).groupby("offset_min").mean().reset_index()
    return profile, len(starts)


# --------------------------------------------------------------------------------------
# case studies
# --------------------------------------------------------------------------------------

def rank_case_study_days(combined: pd.DataFrame, cfg: Settings) -> pd.DataFrame:
    """Days ranked by overpayment, keeping only those with charging evidence outside the fixed window."""
    slots = combined.reset_index()
    slots["day"] = slots["start"].dt.normalize()
    outside = ~in_fixed_cheap_window(slots["start"], cfg)

    by_meter = set(slots.loc[outside & (slots["total_kwh"] > cfg.charge_event_kwh), "day"])
    by_charger = set(slots.loc[outside & (slots["charger_kwh"] * 2 > cfg.charger_on_kw), "day"])

    mis = slots[slots["misbilled"]]
    if mis.empty:
        return pd.DataFrame(columns=["day", "overpayment_gbp", "slots", "kwh", "by_meter",
                                     "by_charger", "daytime_charge"])
    ranked = (mis.groupby("day")
              .agg(overpayment_gbp=("overpayment_gbp", "sum"), slots=("home_kwh", "size"),
                   kwh=("home_kwh", "sum"))
              .reset_index())
    ranked["by_meter"] = ranked["day"].isin(by_meter)
    ranked["by_charger"] = ranked["day"].isin(by_charger)
    ranked["daytime_charge"] = ranked["by_meter"] | ranked["by_charger"]
    return ranked.sort_values("overpayment_gbp", ascending=False).reset_index(drop=True)


def case_study_window(combined: pd.DataFrame, day: pd.Timestamp,
                      hours_before: int = 6, hours_after: int = 8) -> pd.DataFrame:
    """Slots around a day's mis-billed events, anchored on the events rather than on midnight."""
    block = combined[combined["misbilled"] & (combined.index.normalize() == day)]
    if block.empty:
        return combined.loc[day: day + pd.Timedelta(days=1)]
    return combined.loc[
        block.index.min() - pd.Timedelta(hours=hours_before):
        block.index.max() + pd.Timedelta(hours=hours_after)
    ]


def analyse(octopus_src, charger_src=None, cfg: Settings | None = None,
            charger_col: str | None = None, dayfirst: bool | None = None) -> dict:
    """One-shot entry point returning every frame the notebook or app needs."""
    cfg = cfg or Settings()
    oct_df = read_octopus(octopus_src, cfg.tz)
    has_ev_split = bool(oct_df.attrs.get("has_ev_split", True))
    charger_df = read_charger(charger_src, cfg.tz, charger_col, dayfirst) if charger_src is not None else None

    flagged = flag_misbilled(oct_df, cfg)
    combined = combine(flagged, charger_df)
    profile, n_windows = event_study(flagged, cfg)
    return {
        "settings": cfg,
        "octopus": flagged,
        "charger": charger_df,
        "combined": combined,
        "has_ev_split": has_ev_split,
        "summary": cost_summary(flagged, cfg),
        "offset_scan": offset_scan(combined),
        "octopus_only_offset": octopus_only_offset(flagged, cfg),
        "event_study": profile,
        "n_triggered_windows": n_windows,
        "case_study_days": rank_case_study_days(combined, cfg),
    }
