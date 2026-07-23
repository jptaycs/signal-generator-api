import sys

import pandas as pd

from main import evaluate_bar, fetch_time_series, pairs as all_pairs

WARMUP_BARS = 60


def replay_pair(symbol):
    raw = fetch_time_series(symbol)
    if raw is None:
        print(f"{symbol:8s} | SKIPPED (all API keys failed)")
        return None

    df = pd.DataFrame(raw["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    if len(df) <= WARMUP_BARS:
        print(f"{symbol:8s} | SKIPPED (only {len(df)} bars, need > {WARMUP_BARS})")
        return None

    bars_evaluated = 0
    candidates = 0
    blocked_trend = 0
    blocked_htf = 0
    fired_buy = 0
    fired_sell = 0

    for i in range(WARMUP_BARS, len(df)):
        window = df.iloc[: i + 1]
        result = evaluate_bar(window)
        if result["block_reason"] == "insufficient_history":
            continue

        bars_evaluated += 1
        if result["candidate_signal"] is not None:
            candidates += 1
            if result["block_reason"] == "trend_guard":
                blocked_trend += 1
            elif result["block_reason"] == "htf_guard":
                blocked_htf += 1
            elif result["block_reason"] is None:
                if result["candidate_signal"] == "BUY":
                    fired_buy += 1
                else:
                    fired_sell += 1

    fired = fired_buy + fired_sell
    candidate_rate = candidates / bars_evaluated * 100 if bars_evaluated else 0.0
    fired_rate = fired / bars_evaluated * 100 if bars_evaluated else 0.0

    print(
        f"{symbol:8s} | bars={bars_evaluated:4d} | "
        f"candidates={candidates:3d} ({candidate_rate:4.1f}%) | "
        f"blocked_trend={blocked_trend:3d} | blocked_htf={blocked_htf:3d} | "
        f"fired={fired:3d} (BUY={fired_buy} SELL={fired_sell}, {fired_rate:.2f}%)"
    )

    return {
        "bars_evaluated": bars_evaluated,
        "candidates": candidates,
        "blocked_trend": blocked_trend,
        "blocked_htf": blocked_htf,
        "fired_buy": fired_buy,
        "fired_sell": fired_sell,
    }


def main():
    symbols = [sys.argv[1]] if len(sys.argv) > 1 else all_pairs

    totals = {
        "bars_evaluated": 0, "candidates": 0,
        "blocked_trend": 0, "blocked_htf": 0,
        "fired_buy": 0, "fired_sell": 0,
    }

    for symbol in symbols:
        stats = replay_pair(symbol)
        if stats is None:
            continue
        for key in totals:
            totals[key] += stats[key]

    bars = totals["bars_evaluated"]
    if bars == 0:
        print("No bars evaluated across any pair.")
        return

    candidates = totals["candidates"]
    fired = totals["fired_buy"] + totals["fired_sell"]
    candidate_rate = candidates / bars * 100
    fired_rate = fired / bars * 100
    trend_share = totals["blocked_trend"] / candidates * 100 if candidates else 0.0
    htf_share = totals["blocked_htf"] / candidates * 100 if candidates else 0.0

    print(
        f"\nAGGREGATE | bars={bars} | candidates={candidate_rate:.1f}% | "
        f"blocked_trend={trend_share:.1f}% of candidates | "
        f"blocked_htf={htf_share:.1f}% of candidates | "
        f"fired={fired_rate:.2f}% of bars (BUY={totals['fired_buy']} SELL={totals['fired_sell']})"
    )


if __name__ == "__main__":
    main()
