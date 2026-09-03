"""
Turns a flat list of parsed transactions into the report.json payload the
static dashboard reads. Also maintains a rolling history/ folder of daily
snapshots so trailing 4-week averages can be computed without re-hitting EDGAR.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from signals import compute_cluster_map, tag_transaction

HISTORY_RETENTION_DAYS = 40  # a little over 4 weeks of *weekday* history


def _fmt_money(n: float) -> str:
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f}B"
    if a >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"${n / 1_000:.1f}K"
    return f"${n:,.0f}"


def build_report(
    *,
    transactions: list[dict],
    data_cutoff_iso: str,
    window_label: str,
    companies_checked: int,
    filings_found: int,
    filings_parsed: int,
    errors: list[str],
    history_dir: Path,
    sector_lookup: dict[str, str],
) -> dict:
    cluster_map = compute_cluster_map(transactions)

    tagged = []
    for t in transactions:
        tags, score = tag_transaction(t, cluster_map)
        t = {**t, "signals": tags, "signal_score": score}
        tagged.append(t)

    purchases = [t for t in tagged if t["type"] == "P"]
    sales = [t for t in tagged if t["type"] == "S"]

    purchase_value = sum(t["value"] for t in purchases)
    sale_value = sum(t["value"] for t in sales)

    companies = {t["ticker"] for t in tagged}
    insiders = {(t["ticker"], t["insider"]) for t in tagged}
    sectors = {sector_lookup.get(t["ticker"], "Unknown") for t in tagged}

    highest_signal = max(tagged, key=lambda t: t["signal_score"], default=None)

    today_snapshot = {
        "date": data_cutoff_iso[:10],
        "purchase_count": len(purchases),
        "purchase_value": purchase_value,
        "sale_count": len(sales),
        "sale_value": sale_value,
    }
    _write_history_snapshot(history_dir, today_snapshot)
    _prune_history(history_dir)
    trailing = _trailing_four_week_average(history_dir)

    dominant = "sale" if sale_value > purchase_value else "purchase" if purchase_value > sale_value else "even"

    morning_read = (
        f"{len(purchases)} purchase{'s' if len(purchases) != 1 else ''} and "
        f"{len(sales)} sale{'s' if len(sales) != 1 else ''} qualified across "
        f"{len(companies)} compan{'ies' if len(companies) != 1 else 'y'} and "
        f"{len(sectors)} sector group{'s' if len(sectors) != 1 else ''}. "
        + (
            "Sale value exceeded purchase value"
            if dominant == "sale"
            else "Purchase value exceeded sale value"
            if dominant == "purchase"
            else "Purchase and sale value were roughly even"
        )
        + (f", with cluster activity at {len(cluster_map)} compan{'ies' if len(cluster_map) != 1 else 'y'}." if cluster_map else ".")
        + f" The trailing four-week weekly average is {trailing['purchase_count']:.1f} purchases "
        f"and {trailing['sale_count']:.1f} sales."
    )

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "data_cutoff": data_cutoff_iso,
        "window_label": window_label,
        "universe": {"constituents_monitored": companies_checked},
        "stats": {
            "purchases": {"count": len(purchases), "value": purchase_value, "value_display": _fmt_money(purchase_value)},
            "sales": {"count": len(sales), "value": sale_value, "value_display": _fmt_money(sale_value)},
            "companies": len(companies),
            "insiders": len({i for _, i in insiders}),
            "sectors": len(sectors),
            "cluster_companies": len(cluster_map),
            "dominant": dominant,
        },
        "highest_signal": (
            {
                "ticker": highest_signal["ticker"],
                "tags": highest_signal["signals"],
            }
            if highest_signal
            else None
        ),
        "trailing_4wk_avg": {
            "purchase_count": round(trailing["purchase_count"], 1),
            "purchase_value": trailing["purchase_value"],
            "purchase_value_display": _fmt_money(trailing["purchase_value"]),
            "sale_count": round(trailing["sale_count"], 1),
            "sale_value": trailing["sale_value"],
            "sale_value_display": _fmt_money(trailing["sale_value"]),
        },
        "morning_read": morning_read,
        "data_health": {
            "companies_checked": companies_checked,
            "filings_found": filings_found,
            "filings_parsed": filings_parsed,
            "errors": len(errors),
            "error_detail": errors[:25],  # cap payload size
        },
        "transactions": [
            {
                "ticker": t["ticker"],
                "company": t["company"],
                "sector": sector_lookup.get(t["ticker"], "Unknown"),
                "insider": t["insider"],
                "role": t["role"],
                "type": t["type"],
                "tx_date": t["tx_date"],
                "filed_date": t["filed_date"],
                "shares": t["shares"],
                "price": t["price"],
                "value": t["value"],
                "value_display": _fmt_money(t["value"]),
                "ownership": t["ownership"],
                "shares_after": t["shares_after"],
                "rule_10b5_1": t["rule_10b5_1"],
                "amended": t["amended"],
                "signals": t["signals"],
                "filing_url": t["filing_url"],
            }
            for t in tagged
        ],
    }
    return report


def _write_history_snapshot(history_dir: Path, snapshot: dict) -> None:
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{snapshot['date']}.json"
    path.write_text(json.dumps(snapshot, indent=2))


def _prune_history(history_dir: Path) -> None:
    cutoff = dt.date.today() - dt.timedelta(days=HISTORY_RETENTION_DAYS)
    for f in history_dir.glob("*.json"):
        try:
            file_date = dt.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            f.unlink()


def _trailing_four_week_average(history_dir: Path) -> dict:
    snapshots = []
    for f in sorted(history_dir.glob("*.json")):
        try:
            snapshots.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue

    # Keep the last 20 weekday snapshots (~4 weeks).
    snapshots = snapshots[-20:]
    if not snapshots:
        return {"purchase_count": 0.0, "purchase_value": 0.0, "sale_count": 0.0, "sale_value": 0.0}

    weeks: dict[str, dict] = {}
    for s in snapshots:
        d = dt.date.fromisoformat(s["date"])
        iso_year, iso_week, _ = d.isocalendar()
        key = f"{iso_year}-W{iso_week}"
        wk = weeks.setdefault(key, {"purchase_count": 0, "purchase_value": 0.0, "sale_count": 0, "sale_value": 0.0})
        wk["purchase_count"] += s["purchase_count"]
        wk["purchase_value"] += s["purchase_value"]
        wk["sale_count"] += s["sale_count"]
        wk["sale_value"] += s["sale_value"]

    n = len(weeks) or 1
    return {
        "purchase_count": sum(w["purchase_count"] for w in weeks.values()) / n,
        "purchase_value": sum(w["purchase_value"] for w in weeks.values()) / n,
        "sale_count": sum(w["sale_count"] for w in weeks.values()) / n,
        "sale_value": sum(w["sale_value"] for w in weeks.values()) / n,
    }
