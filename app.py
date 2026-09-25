"""Intelligent Octopus Go billing checker - upload your exports and look for mis-billed slots.

Run locally with:  streamlit run app.py
"""

from __future__ import annotations

import io
import sys
from datetime import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

import analysis  # noqa: E402

st.set_page_config(page_title="Octopus EV billing checker", page_icon="⚡", layout="wide")

RED, GREEN, BLUE, GOLD = "#d62728", "#2ca02c", "#1f77b4", "#ffd54f"


@st.cache_data(show_spinner=False)
def run_analysis(octopus_bytes, charger_bytes, charger_col, dayfirst, cfg_values):
    cfg = analysis.Settings(**cfg_values)
    octopus_src = [io.BytesIO(b) for b in octopus_bytes]
    charger_src = io.BytesIO(charger_bytes) if charger_bytes else None
    return analysis.analyse(octopus_src, charger_src, cfg, charger_col, dayfirst)


def gold_bands(fig: go.Figure, window: pd.DataFrame, top: float, threshold: float) -> None:
    """Shade the half hours that were actually billed at the off-peak rate."""
    cheap = window["home_rate_p"] <= threshold
    for _, grp in window[cheap].groupby((~cheap).cumsum()[cheap]):
        fig.add_vrect(x0=grp.index.min(), x1=grp.index.max() + pd.Timedelta(minutes=30),
                      fillcolor=GOLD, opacity=0.35, line_width=0, layer="below")


def case_study_figure(window: pd.DataFrame, title: str, rate_threshold: float,
                      low_rate: float, has_ev_split: bool = True) -> go.Figure:
    slot_ms = 30 * 60 * 1000  # bars span their whole half hour, starting at the slot's timestamp
    fig = go.Figure()
    fig.add_bar(x=window.index, y=window["home_kw"], width=slot_ms, offset=0, marker_color=RED,
                name='Octopus "home"' if has_ev_split else "Metered consumption")
    if has_ev_split:
        fig.add_bar(x=window.index, y=window["ev_kw"], name='Octopus "EV"', marker_color=GREEN,
                    width=slot_ms, offset=0)
    if window["charger_kwh"].notna().any():
        fig.add_scatter(x=window.index, y=window["charger_kwh"] * 2, name="Charger's own log",
                        mode="lines", line=dict(color=BLUE, width=2.5, shape="hv"))
    flagged = window[window["misbilled"]]
    if not flagged.empty:
        fig.add_scatter(x=flagged.index + pd.Timedelta(minutes=15), y=flagged["home_kw"],
                        name="Possibly mis-billed slot", mode="markers",
                        marker=dict(symbol="triangle-down", size=13, color="black"))

    top = float(max(window["home_kw"].max(), window["ev_kw"].max(),
                    (window["charger_kwh"] * 2).max() if window["charger_kwh"].notna().any() else 0)) * 1.2
    gold_bands(fig, window, top, rate_threshold)
    # add_vrect draws a layout shape, which cannot carry a legend entry of its own
    fig.add_scatter(x=[None], y=[None], mode="markers", hoverinfo="skip",
                    name=f"Billed at {low_rate:.2f}p (off-peak)",
                    marker=dict(symbol="square", size=15, color=GOLD, opacity=0.35,
                                line=dict(width=0)))
    fig.update_layout(barmode="stack", height=360, title=title, margin=dict(t=40, b=60, l=10, r=10),
                      yaxis_title="kW", legend=dict(orientation="h", y=-0.18, yanchor="top"),
                      yaxis_range=[0, top])
    return fig


st.title("⚡ Intelligent Octopus Go billing checker")
st.caption(
    "Looks for half hours where the 'home' consumption may be too high to be genuine household load (and therefore was probably caused by the EV charging) "
    "but was still identified as 'home' use and charged at the peak rate - the signature of EV charging being recorded "
    "out of step with the off-peak window that was created for it.  This analysis assumes you have not used the 'Bump Charge' feature!"
)

with st.sidebar:
    st.header("1. Your data")
    octopus_files = st.file_uploader("Octopus half-hourly export (CSV)", type="csv",
                                     accept_multiple_files=True,
                                     help="Add more than one to cover a longer period. Both the newer "
                                          "home/EV split export and the older consumption-only export "
                                          "are accepted, and they can be mixed.")
    charger_file = st.file_uploader("Charger export (CSV, optional)", type="csv",
                                    help="A myenergi-style hourly export. Optional, but it corroborates "
                                         "when the car was actually drawing power.")

    charger_col, dayfirst = None, None
    if charger_file is not None:
        options = analysis.charger_column_options(charger_file)
        if len(options) > 1:
            charger_col = st.selectbox("Charger column", options)
        elif options:
            charger_col = options[0]
        dayfirst_choice = st.radio("Charger date format", ["Detect", "MM/DD/YYYY", "DD/MM/YYYY"],
                                   horizontal=True)
        dayfirst = {"Detect": None, "MM/DD/YYYY": False, "DD/MM/YYYY": True}[dayfirst_choice]

    st.header("2. Thresholds")
    implausible_kw = st.slider("Implausible household load (kW)", 2.0, 10.0, 5.0, 0.5,
                               help="Peak load your home could plausibly draw without the car charging. 30 minute slots where the consumption exceeds half this number of kWh are flagged for investigation. Bear in mind this is averaged across the slot so running a 9kw electric shower for 10 minutes would consume 1.5 kWh.")
    fixed_start = st.time_input("Guaranteed household off-peak starts", time(23, 30), step=1800)
    fixed_end = st.time_input("Guaranteed household off-peak ends", time(5, 30), step=1800)
    charge_event_kwh = st.slider("Charge evidence, per half hour (kWh)", 1.0, 6.0, 3.0, 0.5)
    max_cases = st.slider("Case studies to show", 1, 24, 12)

    st.divider()
    st.caption("Your files are processed in memory for this session only.")

if not octopus_files:
    st.info("Upload your Octopus export in the sidebar to begin.")
    with st.expander("What do I need, and where do I get it?"):
        st.markdown(
            """
**Octopus export (required).** From your Octopus account, download the half-hourly usage CSV for
an Intelligent Octopus Go meter. There are two places this can be found:
1. The new "Home and EV Data" page, at the bottom under "Get your energy geek on". This data will contain Octopus's version of usage that was allocated as EV vs Home usage, with the following
 columns necessary for the analysis: `Home Consumption (kWh)`, `Home Unit Rate (p)`, `EV Consumption (kWh)`, `EV Unit Rate (p)`, `Start`, `End` plus two estimated cost columns. Data are only available 
 in this format if you have the "charge cap" active for your account, and only for periods since it was switched on.

2. The "my energy insights" page, at the bottom, under "Get your energy geek on". This allows you to export data for periods before the charge cap was active. It provides
a simpler file with `Consumption (kWh)`, `Estimated Cost Inc. Tax (p)`, `Standing Charge Inc. Tax (p)`, `Start`, `End`
and no home/EV split. These work too: the unit rate is recovered from cost divided by consumption, and the flagging is done on total consumption instead of the home share. 
If you use these files then you won't be able to separate Octopus's home vs EV consumption on the graph which might make the results harder to interpret conclusively. 

You can upload several files at once to build a continuous history - where they overlap, the split format wins.

**Charger export (optional).** A myenergi hourly graph export, with a `Timestamp` column, a device
column such as `Zappi 12345678 (kW)`, and a `Home (kW)` column. Any charger export with the same
shape will work; you can pick which column is the charger. I've only tried this with myenergi exports.

Unit rates are read from your own file, so this works whatever your tariff rates are.
            """
        )
    st.stop()

cfg_values = dict(
    implausible_kw=implausible_kw,
    charge_event_kwh=charge_event_kwh,
    fixed_cheap_start=fixed_start,
    fixed_cheap_end=fixed_end,
    max_case_studies=max_cases,
)

try:
    result = run_analysis(tuple(f.getvalue() for f in octopus_files),
                          charger_file.getvalue() if charger_file else None,
                          charger_col, dayfirst, cfg_values)
except Exception as exc:  # surface parsing problems rather than a stack trace
    st.error(f"Could not read that file: {exc}")
    st.stop()

summary = result["summary"]
combined = result["combined"]
has_ev_split = result["has_ev_split"]
rate_threshold = (summary["low_rate_p"] + summary["high_rate_p"]) / 2
has_charger = combined["charger_kwh"].notna().any()

st.subheader("Headline")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Suspect half hours", f"{summary['slots']}")
c2.metric("Energy involved", f"{summary['kwh']:.1f} kWh")
c3.metric("Likely overpayment", f"£{summary['gross_overpayment_gbp']:.2f}",
          delta=f"{summary['share_of_bill_pct']:.1f}% of the bill", delta_color="inverse")
c4.metric("Annualised", f"£{summary['annualised_gbp']:.0f}")

st.caption(
    f"Rates detected in your file: **{summary['low_rate_p']:.2f}p** off-peak and "
    f"**{summary['high_rate_p']:.2f}p** standard. Period analysed: {summary['days']:.0f} days, "
    f"total bill £{summary['total_bill_gbp']:.2f}. "
    f"Allowing {summary['baseline_kw']:.2f} kW of genuine household load to continue through each "
    f"spike, the conservative figure is £{summary['conservative_overpayment_gbp']:.2f}."
)

tab_cases, tab_timing, tab_slots = st.tabs(["Case studies", "Timing evidence", "Suspect slots"])

with tab_cases:
    days = result["case_study_days"]
    if days.empty:
        st.success("No slots crossed the implausible-load threshold. Nothing to see here.")
    else:
        qualifying = days[days["daytime_charge"]].head(max_cases)
        st.markdown(
            f"**{len(days)}** days have a suspect slot outside the guaranteed off-peak window. "
            f"**{int(days['by_charger'].sum())}** are corroborated by the charger's own log; "
            f"**{int((~days['daytime_charge']).sum())}** were excluded for having no charging evidence."
        )
        st.caption(
            ("Red is what Octopus billed as household load, green what it billed as EV, blue is the "
             if has_ev_split else
             "Red is the metered consumption (this export has no home/EV split), blue is the ")
            + "charger's own record. Black triangles mark the suspect slots. Yellow highlights the slots that were billed as off-peak by Octopus. "
            + "If you have not used Bump Charge, and have Charge Cap active, these slots should always align with the high consumption bars indicating consumption by the EV charger."
            "Zappi data are hourly so peaks may appear smoothed compared to the half-hourly Octopus data."
        )
        for _, row in qualifying.iterrows():
            window = analysis.case_study_window(combined, row["day"])
            evidence = "charger log" if row["by_charger"] else "meter only"
            title = (f"{row['day'].date()} - {int(row['slots'])} slot(s), {row['kwh']:.1f} kWh, "
                     f"£{row['overpayment_gbp']:.2f} overpaid ({evidence})")
            st.plotly_chart(
                case_study_figure(window, title, rate_threshold, summary["low_rate_p"], has_ev_split),
                width="stretch")

with tab_timing:
    st.markdown("#### Is the consumption data out of step with the tariff?")
    st.caption(
        "This uses only your Octopus file. It slides the consumption series forward and measures how "
        "much energy then lands inside the off-peak windows Octopus itself created. A peak away from "
        "zero means the consumption values are timestamped earlier than the tariff they were billed at."
    )
    oo = result["octopus_only_offset"]
    fig = go.Figure()
    fig.add_bar(x=oo["shift_min"], y=oo["kwh_in_triggered_windows"], name="Triggered off-peak windows",
                marker_color=GREEN)
    if has_ev_split:
        fig.add_bar(x=oo["shift_min"], y=oo["kwh_in_ev_slots"], name="EV-flagged slots",
                    marker_color=BLUE)
    fig.update_layout(height=380, barmode="group", xaxis_title="consumption series moved later (minutes)",
                      yaxis_title="kWh captured", legend=dict(orientation="h", y=1.02, yanchor="bottom"))
    st.plotly_chart(fig, width="stretch")
    best = int(oo.loc[oo["kwh_in_triggered_windows"].idxmax(), "shift_min"])
    st.info(f"Best fit: the consumption series lines up with your tariff when moved **{best} minutes later**.")

    profile, n_windows = result["event_study"], result["n_triggered_windows"]
    if n_windows:
        st.markdown("#### Load profile around the start of an off-peak window")
        fig = go.Figure()
        fig.add_scatter(x=profile["offset_min"], y=profile["home_kw"],
                        name='Billed as "home"' if has_ev_split else "Metered consumption",
                        mode="lines+markers", line=dict(color=RED))
        if has_ev_split:
            fig.add_scatter(x=profile["offset_min"], y=profile["ev_kw"], name='Billed as "EV"',
                            mode="lines+markers", line=dict(color=GREEN))
        fig.add_vline(x=0, line_dash="dash", annotation_text="off-peak starts")
        fig.update_layout(height=380, xaxis_title="minutes relative to the start of the window",
                          yaxis_title="mean kW", legend=dict(orientation="h", y=1.02, yanchor="bottom"))
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Averaged over {n_windows} off-peak windows triggered outside the guaranteed block. "
                   "A spike before zero that collapses at zero is the signature of the problem.")

    if has_charger:
        st.markdown("#### Meter versus the charger's own record")
        scan = result["offset_scan"]
        fig = go.Figure()
        fig.add_scatter(x=scan["shift_min"], y=scan["rmse"], mode="lines", line=dict(color=BLUE))
        best_row = scan.loc[scan["rmse"].idxmin()]
        fig.add_vline(x=best_row["shift_min"], line_dash="dash", line_color=RED,
                      annotation_text=f"{best_row['shift_min']:+.0f} min")
        fig.update_layout(height=340, xaxis_title="charger record shifted earlier (minutes)",
                          yaxis_title="RMSE vs meter (kWh per half hour)")
        st.plotly_chart(fig, width="stretch")
        st.caption("Note this cannot tell you which of the two sources carries the error, only the gap "
                   "between them. The Octopus-only test above is the one that isolates Octopus.")
    else:
        st.info("Upload a charger export to compare the meter against the charger's own record.")

with tab_slots:
    mis = combined[combined["misbilled"]].reset_index()
    if mis.empty:
        st.success("No suspect slots found.")
    else:
        table = mis[["start", "home_kwh", "home_kw", "ev_kwh", "home_rate_p",
                     "home_cost_p", "overpayment_gbp"]].copy()
        if not has_ev_split:
            table = table.drop(columns="ev_kwh")
        table["start"] = table["start"].dt.strftime("%Y-%m-%d %H:%M")
        table = table.rename(columns={
            "start": "Slot", "home_kwh": "Home kWh" if has_ev_split else "kWh",
            "home_kw": "Home kW" if has_ev_split else "kW", "ev_kwh": "EV kWh",
            "home_rate_p": "Rate (p)", "home_cost_p": "Charged (p)", "overpayment_gbp": "Overpaid (£)",
        })
        st.dataframe(table.round(3), width="stretch", hide_index=True)
        st.download_button("Download as CSV", table.round(3).to_csv(index=False),
                           "misbilled_slots.csv", "text/csv")
