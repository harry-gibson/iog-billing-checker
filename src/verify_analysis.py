"""Checks that the shared analysis module reproduces the figures from the exploratory scripts."""

from pathlib import Path

from analysis import Settings, analyse

DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    result = analyse(
        DATA / "octopus.csv",
        DATA / "myenergi-graph-energy-usage-07_16_2026-09_19_2026.csv",
        Settings(),
    )
    s = result["summary"]
    print(f"rates detected        : {s['low_rate_p']:.5f}p / {s['high_rate_p']:.5f}p")
    print(f"mis-billed slots      : {s['slots']}  (expected 35)")
    print(f"energy                : {s['kwh']:.2f} kWh  (expected 122.43)")
    print(f"gross overpayment     : GBP {s['gross_overpayment_gbp']:.2f}  (expected 26.51)")
    print(f"conservative          : GBP {s['conservative_overpayment_gbp']:.2f}  (expected 24.87)")
    print(f"total bill            : GBP {s['total_bill_gbp']:.2f}  (expected 307.42)")
    print(f"re-bill difference    : GBP {s['rebill_difference_gbp']:.2f}  (expected 44.68)")

    scan = result["offset_scan"]
    print(f"meter vs charger best : {scan.loc[scan['rmse'].idxmin(), 'shift_min']:+.0f} min  (expected +90)")

    oo = result["octopus_only_offset"]
    print(f"octopus-only best     : {oo.loc[oo['kwh_in_triggered_windows'].idxmax(), 'shift_min']:+.0f} min"
          f"  (expected +90)")

    days = result["case_study_days"]
    print(f"case-study days       : {len(days)} ranked, {int(days['by_charger'].sum())} charger-corroborated"
          f"  (expected 19 / 16)")
    print(f"triggered windows     : {result['n_triggered_windows']}  (expected 67)")

    print("\n--- older total-only export ---")
    old = analyse(DATA / "octopus_old.csv", cfg=Settings())
    s = old["summary"]
    print(f"has EV split          : {old['has_ev_split']}  (expected False)")
    print(f"rates recovered       : {s['low_rate_p']:.5f}p / {s['high_rate_p']:.5f}p")
    print(f"period                : {s['days']:.0f} days, bill GBP {s['total_bill_gbp']:.2f}")
    print(f"suspect slots         : {s['slots']}, {s['kwh']:.2f} kWh, "
          f"GBP {s['gross_overpayment_gbp']:.2f} overpaid")
    print(f"triggered windows     : {old['n_triggered_windows']}")
    oo = old["octopus_only_offset"]
    print(f"octopus-only best     : {oo.loc[oo['kwh_in_triggered_windows'].idxmax(), 'shift_min']:+.0f} min")

    print("\n--- both exports merged ---")
    both = analyse([DATA / "octopus_old.csv", DATA / "octopus.csv"],
                   DATA / "myenergi-graph-energy-usage-07_16_2026-09_19_2026.csv", cfg=Settings())
    s = both["summary"]
    oct_df = both["octopus"]
    print(f"slots                 : {len(oct_df)} covering "
          f"{oct_df['start'].min():%Y-%m-%d} to {oct_df['start'].max():%Y-%m-%d}")
    print(f"duplicate timestamps  : {int(oct_df['start'].duplicated().sum())}  (expected 0)")
    print(f"period                : {s['days']:.0f} days, bill GBP {s['total_bill_gbp']:.2f}")
    print(f"suspect slots         : {s['slots']}, GBP {s['gross_overpayment_gbp']:.2f} overpaid")
    oo = both["octopus_only_offset"]
    print(f"octopus-only best     : {oo.loc[oo['kwh_in_triggered_windows'].idxmax(), 'shift_min']:+.0f} min")


if __name__ == "__main__":
    main()
