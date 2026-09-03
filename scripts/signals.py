"""
Signal tagging and cluster-activity detection.

"Cluster activity" = at least two distinct insiders reported qualifying
transactions in the same company within the report window.

Signal tiers roughly follow the original brief:
  - Role: CEO / CFO / President / Director carry more weight than generic officer.
  - Direct ownership > indirect (indirect is often trusts/entities).
  - Purchases carry more weight than sales (sales are routine; purchases are not).
  - Value tiers: $50K, $250K, $1M.
  - Multiple insiders at the same company (cluster).
  - 10b5-1 trades are flagged but treated as lower-signal (pre-scheduled, not
    discretionary) — the dashboard should never present a 10b5-1 sale as bearish
    conviction, and this module reflects that by capping its score contribution.
  - Amended filings (4/A) are flagged for visibility, not scored as bullish/bearish.
"""
from __future__ import annotations

from collections import defaultdict

HIGH_TITLES = {"chief executive officer", "ceo", "chief financial officer", "cfo", "president"}


def _role_weight(role: str) -> int:
    r = role.lower()
    if any(t in r for t in HIGH_TITLES):
        return 3
    if "director" in r:
        return 2
    if "officer" in r:
        return 2
    if "10%" in r:
        return 1
    return 0


def _value_weight(value: float) -> int:
    if value >= 1_000_000:
        return 3
    if value >= 250_000:
        return 2
    if value >= 50_000:
        return 1
    return 0


def compute_cluster_map(transactions: list[dict]) -> dict[str, int]:
    """ticker -> count of distinct insiders with qualifying activity in-window."""
    by_ticker: dict[str, set[str]] = defaultdict(set)
    for t in transactions:
        by_ticker[t["ticker"]].add(t["insider"])
    return {ticker: len(insiders) for ticker, insiders in by_ticker.items() if len(insiders) >= 2}


def tag_transaction(t: dict, cluster_map: dict[str, int]) -> tuple[list[str], int]:
    tags: list[str] = []
    score = 0

    role_w = _role_weight(t["role"])
    if role_w:
        tags.append(t["role"])
        score += role_w

    if t["ownership"] == "Direct":
        tags.append("Direct")
        score += 1

    if t["type"] == "P":
        tags.append("Purchase")
        score += 3
    else:
        score += 0  # sales are the base case, not penalized, just not boosted

    value_w = _value_weight(t["value"])
    if value_w == 3:
        tags.append("$1M+")
    elif value_w == 2:
        tags.append("$250K+")
    elif value_w == 1:
        tags.append("$50K+")
    score += value_w

    if t["ticker"] in cluster_map:
        tags.append(f"Cluster ({cluster_map[t['ticker']]} insiders)")
        score += 2

    if t.get("rule_10b5_1"):
        tags.append("10b5-1")
        score += 0  # scheduled trades: flagged, not scored as conviction

    if t.get("amended"):
        tags.append("Amended")
        score += 0

    return tags, score
