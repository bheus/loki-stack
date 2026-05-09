#!/usr/bin/env python3
"""Query Loki for errors and anomalies over the past week."""

import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

LOKI_URL = "http://apple-pi.lan:3100"
WEEK_SECONDS = 7 * 24 * 3600


def now_ns() -> int:
    return int(time.time() * 1e9)


def week_ago_ns() -> int:
    return int((time.time() - WEEK_SECONDS) * 1e9)


def loki_query(logql: str, limit: int = 1000) -> dict:
    params = urllib.parse.urlencode({
        "query": logql,
        "start": str(week_ago_ns()),
        "end": str(now_ns()),
        "limit": str(limit),
        "direction": "backward",
    })
    url = f"{LOKI_URL}/loki/api/v1/query_range?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def loki_query_instant(logql: str) -> dict:
    params = urllib.parse.urlencode({
        "query": logql,
        "start": str(week_ago_ns()),
        "end": str(now_ns()),
        "step": "3600",  # 1-hour resolution
    })
    url = f"{LOKI_URL}/loki/api/v1/query_range?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get_labels() -> list[str]:
    url = f"{LOKI_URL}/loki/api/v1/labels"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data.get("data", [])


def get_label_values(label: str) -> list[str]:
    url = f"{LOKI_URL}/loki/api/v1/label/{urllib.parse.quote(label)}/values"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data.get("data", [])


def count_from_matrix(result: dict) -> int:
    """Sum all values across a metric result."""
    total = 0
    for series in result.get("data", {}).get("result", []):
        for _ts, val in series.get("values", []):
            total += int(float(val))
    return total


def extract_log_lines(result: dict) -> list[tuple[int, str, str]]:
    """Return list of (timestamp_ns, stream_labels, line) tuples."""
    lines = []
    for stream in result.get("data", {}).get("result", []):
        labels = str(stream.get("stream", {}))
        for ts_str, line in stream.get("values", []):
            lines.append((int(ts_str), labels, line))
    lines.sort(key=lambda x: x[0])
    return lines


def format_ts(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def section(title: str):
    bar = "=" * 70
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)


def check_loki_ready():
    url = f"{LOKI_URL}/ready"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode()
            if "ready" not in body.lower():
                print(f"[WARN] Loki /ready returned unexpected body: {body!r}")
    except Exception as exc:
        print(f"[ERROR] Cannot reach Loki at {LOKI_URL}: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def run_error_count_by_source():
    """Count errors per log source (container / unit / syslog host) over a week."""
    section("Error counts by source (past 7 days)")

    queries = {
        "Docker containers — ERROR level":
            'sum by (container) (count_over_time({job="docker"} |~ "(?i)(error|exception|critical|fatal)" [7d]))',
        "Systemd journal — error/critical priority":
            'sum by (unit) (count_over_time({job="systemd-journal", level=~"err|crit|alert|emerg"} [7d]))',
        "Router syslog (TCP) — error severity":
            'sum by (host, application) (count_over_time({job="router-syslog-tcp", level=~"err|crit|alert|emerg"} [7d]))',
        "Router syslog (UDP) — error severity":
            'sum by (host, application) (count_over_time({job="router-syslog-udp", level=~"err|crit|alert|emerg"} [7d]))',
    }

    for label, query in queries.items():
        print(f"\n>> {label}")
        try:
            result = loki_query_instant(query)
            series_list = result.get("data", {}).get("result", [])
            if not series_list:
                print("   (no data)")
                continue
            # Aggregate totals per series
            totals = []
            for series in series_list:
                total = sum(int(float(v)) for _, v in series.get("values", []))
                metric = series.get("metric", {})
                label_str = ", ".join(f"{k}={v!r}" for k, v in metric.items())
                totals.append((total, label_str))
            totals.sort(reverse=True)
            for count, lbl in totals[:20]:
                print(f"   {count:>7,}  {lbl}")
        except Exception as exc:
            print(f"   [WARN] Query failed: {exc}")


def run_hourly_error_trend():
    """Show hourly error rate trend for the past week across all sources."""
    section("Hourly error rate trend — all sources (past 7 days)")

    query = (
        'sum(count_over_time({job=~"docker|systemd-journal|router-syslog-tcp|router-syslog-udp"}'
        ' |~ "(?i)(error|exception|critical|fatal|emerg|alert)" [1h]))'
    )
    try:
        result = loki_query_instant(query)
        series_list = result.get("data", {}).get("result", [])
        if not series_list:
            print("   (no data)")
            return

        # Flatten values from the single aggregated series
        all_values = []
        for series in series_list:
            for ts_str, val in series.get("values", []):
                all_values.append((int(ts_str), float(val)))
        all_values.sort()

        if not all_values:
            print("   (no data)")
            return

        counts = [v for _, v in all_values]
        max_count = max(counts) if counts else 1
        avg_count = sum(counts) / len(counts) if counts else 0
        spike_threshold = avg_count * 3

        print(f"   Avg errors/hr: {avg_count:,.1f}   Spike threshold (3×avg): {spike_threshold:,.1f}")
        print()

        for ts_ns, count in all_values:
            bar_len = int(40 * count / max_count) if max_count else 0
            bar = "#" * bar_len
            spike = " *** SPIKE ***" if count > spike_threshold and spike_threshold > 0 else ""
            print(f"   {format_ts(ts_ns * 1_000_000_000)}  {count:>7,.0f}  |{bar}{spike}")

    except Exception as exc:
        print(f"   [WARN] Query failed: {exc}")


def run_recent_error_samples():
    """Pull recent error log lines for manual inspection."""
    section("Recent error samples — last 50 across all sources (past 7 days)")

    query = (
        '{job=~"docker|systemd-journal|router-syslog-tcp|router-syslog-udp"}'
        ' |~ "(?i)(error|exception|critical|fatal|emerg|alert)"'
    )
    try:
        result = loki_query(query, limit=50)
        lines = extract_log_lines(result)
        if not lines:
            print("   (no error log lines found)")
            return
        for ts_ns, labels, line in lines[-50:]:
            ts = format_ts(ts_ns)
            truncated = line[:200] + ("…" if len(line) > 200 else "")
            print(f"\n   [{ts}]")
            print(f"   Labels : {labels}")
            print(f"   Message: {truncated}")
    except Exception as exc:
        print(f"   [WARN] Query failed: {exc}")


def run_oom_and_crash_check():
    """Look for OOM kills, panics, and segfaults."""
    section("OOM kills / panics / segfaults (past 7 days)")

    patterns = {
        "OOM kill":
            '{job=~"docker|systemd-journal"} |~ "(?i)(out of memory|oom.kill|killed process)"',
        "Kernel panic / panic":
            '{job=~"docker|systemd-journal"} |~ "(?i)(kernel panic|panic:|stacktrace|goroutine \\d)"',
        "Segfault":
            '{job=~"docker|systemd-journal"} |~ "(?i)(segfault|segmentation fault|signal 11)"',
        "Container exit / restart":
            '{job="docker"} |~ "(?i)(exited with code [^0]|restarting)"',
    }

    for name, query in patterns.items():
        try:
            result = loki_query(query, limit=10)
            lines = extract_log_lines(result)
            print(f"\n>> {name}: {len(lines)} occurrence(s)")
            for ts_ns, labels, line in lines[:5]:
                print(f"   [{format_ts(ts_ns)}] {line[:180]}")
        except Exception as exc:
            print(f"\n>> {name}: [WARN] {exc}")


def run_syslog_severity_breakdown():
    """Break down syslog messages by severity level."""
    section("Syslog severity breakdown (past 7 days)")

    query = (
        'sum by (level) (count_over_time('
        '{job=~"router-syslog-tcp|router-syslog-udp"} [7d]))'
    )
    try:
        result = loki_query_instant(query)
        series_list = result.get("data", {}).get("result", [])
        if not series_list:
            print("   (no syslog data)")
            return
        totals = []
        for series in series_list:
            total = sum(int(float(v)) for _, v in series.get("values", []))
            level = series.get("metric", {}).get("level", "unknown")
            totals.append((total, level))
        totals.sort(reverse=True)
        grand = sum(t for t, _ in totals) or 1
        for count, lvl in totals:
            pct = 100 * count / grand
            print(f"   {lvl:<12}  {count:>8,}  ({pct:5.1f}%)")
    except Exception as exc:
        print(f"   [WARN] Query failed: {exc}")


def run_anomaly_detection():
    """Flag hours where error rate is significantly above the weekly average."""
    section("Anomaly detection — hours with error spikes (past 7 days)")

    query = (
        'sum(count_over_time({job=~"docker|systemd-journal|router-syslog-tcp|router-syslog-udp"}'
        ' |~ "(?i)(error|exception|critical|fatal|emerg|alert)" [1h]))'
    )
    try:
        result = loki_query_instant(query)
        series_list = result.get("data", {}).get("result", [])
        if not series_list:
            print("   (no data)")
            return

        all_values: list[tuple[int, float]] = []
        for series in series_list:
            for ts_str, val in series.get("values", []):
                all_values.append((int(ts_str) * 1_000_000_000, float(val)))
        all_values.sort()

        if len(all_values) < 3:
            print("   Not enough data points for anomaly detection.")
            return

        counts = [v for _, v in all_values]
        avg = sum(counts) / len(counts)
        variance = sum((c - avg) ** 2 for c in counts) / len(counts)
        stddev = variance ** 0.5
        threshold = avg + 2 * stddev  # flag anything >2σ above mean

        print(f"   Mean: {avg:,.1f}  StdDev: {stddev:,.1f}  Spike threshold (μ+2σ): {threshold:,.1f}")

        anomalies = [(ts, c) for ts, c in all_values if c > threshold]
        if not anomalies:
            print("   No anomalous hours detected.")
        else:
            print(f"\n   {len(anomalies)} anomalous hour(s):")
            for ts_ns, count in anomalies:
                print(f"   {format_ts(ts_ns)}  errors={count:,.0f}  ({count / avg:.1f}× avg)")

    except Exception as exc:
        print(f"   [WARN] Query failed: {exc}")


def run_top_error_messages():
    """Surface the most-repeated error message patterns."""
    section("Top repeated error messages — Docker containers (past 7 days)")

    # Pull a larger sample and count by line content
    query = '{job="docker"} |~ "(?i)(error|exception|critical|fatal)"'
    try:
        result = loki_query(query, limit=1000)
        lines = extract_log_lines(result)
        if not lines:
            print("   (no data)")
            return

        from collections import Counter
        # Truncate lines to first 120 chars for grouping (strip timestamps)
        def normalise(line: str) -> str:
            # Strip leading timestamps like "2026-01-01T00:00:00Z " or "[123456789]"
            import re
            line = re.sub(r"^\S*\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*\s*", "", line)
            line = re.sub(r"^\[\d+\]\s*", "", line)
            return line[:120].strip()

        counter: Counter = Counter()
        for _, _, line in lines:
            counter[normalise(line)] += 1

        print(f"   Sampled {len(lines)} error log lines, {len(counter)} unique patterns:\n")
        for msg, cnt in counter.most_common(20):
            print(f"   {cnt:>5}×  {msg}")

    except Exception as exc:
        print(f"   [WARN] Query failed: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    start = datetime.now(tz=timezone.utc)
    week_start = start - timedelta(days=7)

    print("=" * 70)
    print("  LOKI ERROR & ANOMALY REPORT")
    print(f"  Period : {week_start.strftime('%Y-%m-%d %H:%M UTC')} → {start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Source : {LOKI_URL}")
    print("=" * 70)

    check_loki_ready()

    # Discover what's in Loki
    section("Available labels & streams")
    try:
        labels = get_labels()
        print(f"   Labels: {labels}")
        for lbl in ("job", "container", "unit", "host", "level"):
            if lbl in labels:
                values = get_label_values(lbl)
                print(f"   {lbl}: {values}")
    except Exception as exc:
        print(f"   [WARN] Could not list labels: {exc}")

    run_error_count_by_source()
    run_hourly_error_trend()
    run_anomaly_detection()
    run_oom_and_crash_check()
    run_syslog_severity_breakdown()
    run_top_error_messages()
    run_recent_error_samples()

    section("Summary")
    elapsed = (datetime.now(tz=timezone.utc) - start).total_seconds()
    print(f"   Report completed in {elapsed:.1f}s")
    print()


if __name__ == "__main__":
    main()
