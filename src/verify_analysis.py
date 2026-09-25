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


if __name__ == "__main__":
    main()
