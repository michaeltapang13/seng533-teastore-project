import os
import re
import glob
import json
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

# ============================================================
# CONFIG
# ============================================================
DEFAULT_DATA_DIR = os.path.expanduser("~")
STATS_DIR = os.path.expanduser("~/data")
K6_CSV_DIR = os.path.expanduser("~/data/k6_csv")
OUTPUT_DIR = os.path.expanduser("~/graphs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

WARMUP_SKIP_SECONDS = 60
RAMPDOWN_TRIM_SECONDS = 30
CONFIDENCE_LEVEL = 0.95
TIME_BIN_SECONDS = 30

plt.rcParams.update({
    "figure.figsize": (10, 6),
    "font.family": "serif",
    "font.size": 12,
    "axes.titlesize": 14,
    "axes.labelsize": 12,
    "legend.fontsize": 10,
    "figure.dpi": 300,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.3,
})


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="baseline")
    p.add_argument("--label", default=None)
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    p.add_argument("--stats-dir", default=STATS_DIR)
    p.add_argument("--k6-csv-dir", default=K6_CSV_DIR)
    return p.parse_args()


def t_ci(values, confidence=CONFIDENCE_LEVEL):
    values = np.asarray([
        v for v in values
        if v is not None and not (isinstance(v, float) and np.isnan(v))
    ])
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return float(values[0]), 0.0

    mean = float(np.mean(values))
    sem = stats.sem(values)
    t_crit = stats.t.ppf((1 + confidence) / 2, df=n - 1)
    return mean, float(t_crit * sem)


# ============================================================
# K6 JSON parsing
# ============================================================
def find_run_files(data_dir, scenario):
    pattern = os.path.join(data_dir, f"{scenario}_results_run*.json")
    files = glob.glob(pattern)

    def run_num(path):
        m = re.search(r"run(\d+)", os.path.basename(path))
        return int(m.group(1)) if m else 0

    return sorted(files, key=run_num)


def get_metric_value(metrics, name, key):
    m = metrics.get(name, {})
    if "values" in m:
        return m["values"].get(key)
    return m.get(key)


def extract_run_metrics(json_path):
    with open(json_path, "r") as f:
        data = json.load(f)

    metrics = data.get("metrics", {})

    duration_s = data.get("state", {}).get("testRunDurationMs", 0) / 1000.0
    if not duration_s:
        count = get_metric_value(metrics, "http_reqs", "count") or 0
        rate = get_metric_value(metrics, "http_reqs", "rate") or 0
        duration_s = count / rate if rate > 0 else 1.0

    http_reqs = get_metric_value(metrics, "http_reqs", "count") or 0
    total_orders = get_metric_value(metrics, "total_orders", "count") or 0

    error_rate = (
        get_metric_value(metrics, "error_rate", "rate") or
        get_metric_value(metrics, "error_rate", "value") or 0
    )

    return {
        "run": os.path.basename(json_path),
        "duration_s": duration_s,
        "http_req_avg": get_metric_value(metrics, "http_req_duration", "avg"),
        "http_req_med": get_metric_value(metrics, "http_req_duration", "med"),
        "http_req_p90": get_metric_value(metrics, "http_req_duration", "p(90)"),
        "http_req_p95": get_metric_value(metrics, "http_req_duration", "p(95)"),
        "http_req_max": get_metric_value(metrics, "http_req_duration", "max"),
        "browse_avg": get_metric_value(metrics, "browse_response_time", "avg"),
        "browse_med": get_metric_value(metrics, "browse_response_time", "med"),
        "browse_p95": get_metric_value(metrics, "browse_response_time", "p(95)"),
        "cart_avg": get_metric_value(metrics, "cart_response_time", "avg"),
        "cart_med": get_metric_value(metrics, "cart_response_time", "med"),
        "cart_p95": get_metric_value(metrics, "cart_response_time", "p(95)"),
        "order_avg": get_metric_value(metrics, "order_response_time", "avg"),
        "order_med": get_metric_value(metrics, "order_response_time", "med"),
        "order_p95": get_metric_value(metrics, "order_response_time", "p(95)"),
        "throughput_rps": http_reqs / duration_s if duration_s else 0,
        "orders_per_sec": total_orders / duration_s if duration_s else 0,
        "error_rate": error_rate,
    }


# ============================================================
# Docker stats parsing
# ============================================================

def is_teastore_container(container_name):
    name = container_name.lower()

    exclude = ["influxdb", "grafana", "prometheus", "cadvisor", "telegraf"]
    if any(ex in name for ex in exclude):
        return False

    keywords = ["webui", "persistence", "auth", "image", "recommender", "registry"]
    if any(k in name for k in keywords):
        return True

    if ("_db" in name or "teastore-db" in name or "teastore_db" in name):
        return True

    return False


def match_service(container_name):
    name = container_name.lower()

    exclude = ["influxdb", "grafana", "prometheus", "cadvisor", "telegraf"]
    if any(ex in name for ex in exclude):
        return None

    if "webui" in name:
        return "WebUI"
    elif "persistence" in name:
        return "Persistence"
    elif "recommender" in name:
        return "Recommender"
    elif "image" in name:
        return "Image"
    elif "auth" in name:
        return "Auth"
    elif "registry" in name:
        return "Registry"
    elif "_db" in name or "teastore-db" in name or "teastore_db" in name:
        return "Database"

    return None


def load_stats_csv(csv_path):
    rows = []

    with open(csv_path, "r") as f:
        first_line = True
        for line in f:
            line = line.strip()
            if not line:
                continue

            if first_line:
                first_line = False
                if "timestamp" in line.lower() and "container" in line.lower():
                    continue

            parts = line.split(",")
            if len(parts) < 4:
                continue

            try:
                timestamp = parts[0].strip()
                container = parts[1].strip()
                cpu = float(parts[2].strip().replace("%", ""))

                if not is_teastore_container(container):
                    continue

                rows.append({
                    "timestamp": timestamp,
                    "container": container,
                    "cpu": cpu
                })
            except (ValueError, IndexError):
                continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Docker stats timestamps are human-readable strings like "2026-04-10 01:08:17"
    # Do NOT use unit="s" here — that's only for Unix epoch numbers
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])

    if df.empty:
        return df

    df = df.sort_values("timestamp").reset_index(drop=True)

    run_start = df["timestamp"].min()
    warmup_cutoff = run_start + pd.Timedelta(seconds=WARMUP_SKIP_SECONDS)
    df = df[df["timestamp"] >= warmup_cutoff].copy()

    if df.empty:
        return df

    df["seconds"] = (df["timestamp"] - run_start).dt.total_seconds()
    df["service"] = df["container"].apply(match_service)
    df = df.dropna(subset=["service"])

    return df


def cpu_means_per_service(csv_path):
    df = load_stats_csv(csv_path)
    if df.empty:
        return {}
    return df.groupby("service")["cpu"].mean().to_dict()


# ============================================================
# K6 CSV parsing (replaces InfluxDB)
# ============================================================

def load_k6_csv(csv_path):
    """
    Load a k6 CSV output file. k6 CSV format has columns:
    metric_name, timestamp, metric_value, plus tag columns.
    Returns the raw DataFrame.
    """
    try:
        df = pd.read_csv(csv_path, low_memory=False)
    except Exception as e:
        print(f"  Warning: could not read {csv_path}: {e}")
        return pd.DataFrame()

    if df.empty:
        return df

    required = ["metric_name", "timestamp", "metric_value"]
    for col in required:
        if col not in df.columns:
            print(f"  Warning: missing column '{col}' in {csv_path}")
            return pd.DataFrame()

    # k6 CSV timestamps are Unix epoch seconds (e.g. 1775808514)
    # Must use unit="s" to parse correctly
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["metric_value"] = pd.to_numeric(df["metric_value"], errors="coerce")

    return df


def extract_timeseries_from_k6_csv(csv_path, metric_name, bin_seconds=TIME_BIN_SECONDS):
    """
    Extract a time-series for a specific metric from a k6 CSV file.
    Bins data into time buckets and returns mean per bucket.
    Skips warmup and ramp-down periods.
    Returns DataFrame with columns ['seconds', 'val'].
    """
    df = load_k6_csv(csv_path)
    if df.empty:
        return pd.DataFrame()

    metric_df = df[df["metric_name"] == metric_name].copy()
    if metric_df.empty:
        return pd.DataFrame()

    metric_df = metric_df.sort_values("timestamp").reset_index(drop=True)

    run_start = metric_df["timestamp"].min()
    run_end = metric_df["timestamp"].max()
    total_duration = (run_end - run_start).total_seconds()

    metric_df["seconds"] = (metric_df["timestamp"] - run_start).dt.total_seconds()

    trim_end = total_duration - RAMPDOWN_TRIM_SECONDS
    metric_df = metric_df[
        (metric_df["seconds"] >= WARMUP_SKIP_SECONDS) &
        (metric_df["seconds"] <= trim_end)
    ].copy()

    if metric_df.empty:
        return pd.DataFrame()

    metric_df["seconds"] = metric_df["seconds"] - WARMUP_SKIP_SECONDS

    max_sec = metric_df["seconds"].max()
    bins = np.arange(0, max_sec + bin_seconds, bin_seconds)
    if len(bins) < 2:
        return pd.DataFrame()

    metric_df["bin"] = pd.cut(
        metric_df["seconds"], bins=bins,
        labels=bins[:-1], include_lowest=True, right=False
    )
    binned = metric_df.groupby("bin", observed=True)["metric_value"].mean().reset_index()
    binned.columns = ["seconds", "val"]
    binned["seconds"] = binned["seconds"].astype(float)

    return binned


def extract_percentiles_from_k6_csv(csv_path, metric_name, bin_seconds=TIME_BIN_SECONDS):
    """
    Extract P95 and P99 time-series from a k6 CSV file.
    Computes the 95th and 99th percentile per time bucket.
    Returns DataFrame with columns ['seconds', 'p95', 'p99'].
    """
    df = load_k6_csv(csv_path)
    if df.empty:
        return pd.DataFrame()

    metric_df = df[df["metric_name"] == metric_name].copy()
    if metric_df.empty:
        return pd.DataFrame()

    metric_df = metric_df.sort_values("timestamp").reset_index(drop=True)

    run_start = metric_df["timestamp"].min()
    run_end = metric_df["timestamp"].max()
    total_duration = (run_end - run_start).total_seconds()

    metric_df["seconds"] = (metric_df["timestamp"] - run_start).dt.total_seconds()

    trim_end = total_duration - RAMPDOWN_TRIM_SECONDS
    metric_df = metric_df[
        (metric_df["seconds"] >= WARMUP_SKIP_SECONDS) &
        (metric_df["seconds"] <= trim_end)
    ].copy()

    if metric_df.empty:
        return pd.DataFrame()

    metric_df["seconds"] = metric_df["seconds"] - WARMUP_SKIP_SECONDS

    max_sec = metric_df["seconds"].max()
    bins = np.arange(0, max_sec + bin_seconds, bin_seconds)
    if len(bins) < 2:
        return pd.DataFrame()

    metric_df["bin"] = pd.cut(
        metric_df["seconds"], bins=bins,
        labels=bins[:-1], include_lowest=True, right=False
    )

    # Compute P95 and P99 per bin
    binned = metric_df.groupby("bin", observed=True)["metric_value"].agg(
        p95=lambda x: np.percentile(x, 95),
        p99=lambda x: np.percentile(x, 99)
    ).reset_index()
    binned.columns = ["seconds", "p95", "p99"]
    binned["seconds"] = binned["seconds"].astype(float)

    return binned


def extract_throughput_from_k6_csv(csv_path, bin_seconds=TIME_BIN_SECONDS):
    """
    Extract throughput (requests/second) from a k6 CSV file.
    Counts http_reqs per time bucket and divides by bucket size.
    Returns DataFrame with columns ['seconds', 'val'].
    """
    df = load_k6_csv(csv_path)
    if df.empty:
        return pd.DataFrame()

    reqs_df = df[df["metric_name"] == "http_reqs"].copy()
    if reqs_df.empty:
        return pd.DataFrame()

    reqs_df = reqs_df.sort_values("timestamp").reset_index(drop=True)

    run_start = reqs_df["timestamp"].min()
    run_end = reqs_df["timestamp"].max()
    total_duration = (run_end - run_start).total_seconds()

    reqs_df["seconds"] = (reqs_df["timestamp"] - run_start).dt.total_seconds()

    trim_end = total_duration - RAMPDOWN_TRIM_SECONDS
    reqs_df = reqs_df[
        (reqs_df["seconds"] >= WARMUP_SKIP_SECONDS) &
        (reqs_df["seconds"] <= trim_end)
    ].copy()

    if reqs_df.empty:
        return pd.DataFrame()

    reqs_df["seconds"] = reqs_df["seconds"] - WARMUP_SKIP_SECONDS

    max_sec = reqs_df["seconds"].max()
    bins = np.arange(0, max_sec + bin_seconds, bin_seconds)
    if len(bins) < 2:
        return pd.DataFrame()

    reqs_df["bin"] = pd.cut(
        reqs_df["seconds"], bins=bins,
        labels=bins[:-1], include_lowest=True, right=False
    )
    binned = reqs_df.groupby("bin", observed=True)["metric_value"].count().reset_index()
    binned.columns = ["seconds", "val"]
    binned["seconds"] = binned["seconds"].astype(float)
    binned["val"] = binned["val"] / bin_seconds

    return binned


def find_k6_csv_files(k6_csv_dir, scenario):
    pattern = os.path.join(k6_csv_dir, f"{scenario}_k6_run*.csv")
    files = glob.glob(pattern)

    def run_num(path):
        m = re.search(r"run(\d+)", os.path.basename(path))
        return int(m.group(1)) if m else 0

    return sorted(files, key=run_num)


# ============================================================
# Plotting: bar charts
# ============================================================
def plot_response_time_breakdown(runs_df, label):
    metrics_to_plot = [
        ("browse_med", "Browse (median)"),
        ("browse_p95", "Browse (P95)"),
        ("cart_med", "Cart (median)"),
        ("cart_p95", "Cart (P95)"),
        ("order_med", "Order (median)"),
        ("order_p95", "Order (P95)"),
    ]

    names, means, cis = [], [], []
    for col, display in metrics_to_plot:
        if col in runs_df.columns:
            mean, hw = t_ci(runs_df[col].values)
            names.append(display)
            means.append(mean)
            cis.append(hw)

    fig, ax = plt.subplots()
    colors = ["#2196F3", "#1976D2", "#FF9800", "#F57C00", "#4CAF50", "#388E3C"]
    bars = ax.bar(names, means, yerr=cis, capsize=5,
                  color=colors[:len(names)], edgecolor="white", linewidth=0.5)
    ax.set_ylabel("Response Time (ms)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Response Time by Request Type - {label}\n(95% CI, n={len(runs_df)} runs)")
    plt.xticks(rotation=20, ha="right")

    offset = max(means) * 0.02 if means else 1
    for bar, val in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + offset,
                f"{val:.0f}",
                ha="center", va="bottom", fontsize=10)

    path = os.path.join(OUTPUT_DIR, "response_time_breakdown.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_throughput_bar(runs_df, label):
    metrics = [
        ("throughput_rps", "HTTP Requests/sec"),
        ("orders_per_sec", "Orders/sec"),
    ]

    names, means, cis = [], [], []
    for col, display in metrics:
        if col in runs_df.columns:
            mean, hw = t_ci(runs_df[col].values)
            names.append(display)
            means.append(mean)
            cis.append(hw)

    fig, ax = plt.subplots(figsize=(8, 6))
    bars = ax.bar(names, means, yerr=cis, capsize=5,
                  color=["#4CAF50", "#2E7D32"], edgecolor="white", linewidth=0.5)
    ax.set_ylabel("Throughput")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Throughput - {label}\n(95% CI, n={len(runs_df)} runs)")

    offset = max(means) * 0.02 if means else 1
    for bar, val in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + offset,
                f"{val:.2f}",
                ha="center", va="bottom", fontsize=10)

    path = os.path.join(OUTPUT_DIR, "throughput.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_cpu_per_service_bar(cpu_runs, label):
    if not cpu_runs:
        print("WARNING: No CPU data, skipping CPU bar chart.")
        return

    all_services = set()
    for r in cpu_runs:
        all_services.update(r.keys())

    per_service = {svc: [] for svc in all_services}
    for r in cpu_runs:
        for svc in all_services:
            if svc in r:
                per_service[svc].append(r[svc])

    services, means, cis = [], [], []
    for svc, vals in per_service.items():
        mean, hw = t_ci(vals)
        services.append(svc)
        means.append(mean)
        cis.append(hw)

    order = np.argsort(means)[::-1]
    services = [services[i] for i in order]
    means = [means[i] for i in order]
    cis = [cis[i] for i in order]

    colors = ["#F44336", "#FF9800", "#FFC107", "#4CAF50", "#2196F3", "#9C27B0", "#607D8B"]
    fig, ax = plt.subplots()
    bars = ax.bar(services, means, yerr=cis, capsize=5,
                  color=colors[:len(services)], edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Microservice")
    ax.set_ylabel("CPU Utilization (%)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"CPU Utilization per Microservice - {label}\n(95% CI, n={len(cpu_runs)} runs)")

    offset = max(means) * 0.02 if means else 1
    for bar, val in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + offset,
                f"{val:.1f}%",
                ha="center", va="bottom", fontsize=10)

    path = os.path.join(OUTPUT_DIR, "cpu_per_service.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"Saved: {path}")


# ============================================================
# Time-series helpers
# ============================================================
def align_runs_to_time_bins(per_run_dfs, value_col, bin_seconds=TIME_BIN_SECONDS):
    if not per_run_dfs:
        return pd.DataFrame()

    valid_dfs = [df for df in per_run_dfs if not df.empty]
    if not valid_dfs:
        return pd.DataFrame()

    max_seconds = min(df["seconds"].max() for df in valid_dfs)
    if pd.isna(max_seconds) or max_seconds <= 0:
        return pd.DataFrame()

    bins = np.arange(0, max_seconds + bin_seconds, bin_seconds)
    if len(bins) < 2:
        return pd.DataFrame()

    aligned = pd.DataFrame({"bin_start": bins[:-1]})

    for i, df in enumerate(valid_dfs):
        col_name = f"run{i+1}"

        if df.empty:
            aligned[col_name] = np.nan
            continue

        temp = df.copy()
        temp["bin"] = pd.cut(
            temp["seconds"],
            bins=bins,
            labels=bins[:-1],
            include_lowest=True,
            right=False
        )

        binned = temp.groupby("bin", observed=True)[value_col].mean().reset_index()
        binned["bin"] = binned["bin"].astype(float)

        aligned = aligned.merge(binned, left_on="bin_start", right_on="bin", how="left")
        aligned = aligned.drop(columns=["bin"])
        aligned = aligned.rename(columns={value_col: col_name})

    return aligned


def compute_mean_ci_band(aligned_df, min_value=None, max_value=None):
    run_cols = [c for c in aligned_df.columns if c.startswith("run")]
    means, lowers, uppers = [], [], []

    for _, row in aligned_df.iterrows():
        vals = row[run_cols].dropna().values
        m, hw = t_ci(vals)

        lower = m - hw
        upper = m + hw

        if min_value is not None:
            lower = max(lower, min_value)
            m = max(m, min_value)

        if max_value is not None:
            upper = min(upper, max_value)
            m = min(m, max_value)

        means.append(m)
        lowers.append(lower)
        uppers.append(upper)

    return np.array(means), np.array(lowers), np.array(uppers)


# ============================================================
# Time-series plots from k6 CSV
# ============================================================

def plot_response_time_over_time(k6_csv_files, label):
    if not k6_csv_files:
        print("WARNING: No k6 CSV files found, skipping response time time-series.")
        return

    print("  Loading response time data from k6 CSV files...")
    per_run_dfs = []
    for i, csv_path in enumerate(k6_csv_files):
        df = extract_timeseries_from_k6_csv(csv_path, "http_req_duration")
        if not df.empty:
            per_run_dfs.append(df)
            print(f"    Run {i+1}: {len(df)} bins, duration {df['seconds'].max():.0f}s")
        else:
            print(f"    Run {i+1}: no data")

    if not per_run_dfs:
        print("WARNING: No response time data found in CSV files.")
        return

    print(f"  {len(per_run_dfs)} valid runs")

    aligned = align_runs_to_time_bins(per_run_dfs, "val")
    if aligned.empty:
        print("WARNING: Could not align response-time data into common bins.")
        return

    means, lowers, uppers = compute_mean_ci_band(aligned, min_value=0)
    times = aligned["bin_start"].values

    fig, ax = plt.subplots()
    ax.fill_between(times, lowers, uppers, alpha=0.25, color="#2196F3", label="95% CI")
    ax.plot(times, means, linewidth=2, color="#1976D2", label="Mean")
    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("Response Time (ms)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Response Time Over Time - {label}\n(95% CI band, n={len(per_run_dfs)} runs)")
    ax.legend()

    path = os.path.join(OUTPUT_DIR, "response_time_over_time_ci.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_p95_p99_over_time(k6_csv_files, label):
    """Plot P95 and P99 response time over time with CI bands, matching midterm style."""
    if not k6_csv_files:
        print("WARNING: No k6 CSV files found, skipping P95/P99 time-series.")
        return

    print("  Loading P95/P99 response time data from k6 CSV files...")

    # Collect P95 and P99 per run
    p95_per_run = []
    p99_per_run = []

    for i, csv_path in enumerate(k6_csv_files):
        df = extract_percentiles_from_k6_csv(csv_path, "http_req_duration")
        if not df.empty:
            p95_df = df[["seconds", "p95"]].rename(columns={"p95": "val"})
            p99_df = df[["seconds", "p99"]].rename(columns={"p99": "val"})
            p95_per_run.append(p95_df)
            p99_per_run.append(p99_df)
            print(f"    Run {i+1}: {len(df)} bins, duration {df['seconds'].max():.0f}s")
        else:
            print(f"    Run {i+1}: no data")

    if not p95_per_run:
        print("WARNING: No P95/P99 data found in CSV files.")
        return

    print(f"  {len(p95_per_run)} valid runs")

    # Align and compute CI for P95
    p95_aligned = align_runs_to_time_bins(p95_per_run, "val")
    p99_aligned = align_runs_to_time_bins(p99_per_run, "val")

    if p95_aligned.empty or p99_aligned.empty:
        print("WARNING: Could not align P95/P99 data into common bins.")
        return

    p95_means, p95_lowers, p95_uppers = compute_mean_ci_band(p95_aligned, min_value=0)
    p99_means, p99_lowers, p99_uppers = compute_mean_ci_band(p99_aligned, min_value=0)
    times = p95_aligned["bin_start"].values

    fig, ax = plt.subplots()

    # P95 line and CI band
    ax.fill_between(times, p95_lowers, p95_uppers, alpha=0.2, color="#4CAF50", label="P95 95% CI")
    ax.plot(times, p95_means, linewidth=2, color="#2E7D32", label="P95 (mean)")

    # P99 line and CI band
    ax.fill_between(times, p99_lowers, p99_uppers, alpha=0.2, color="#FF9800", label="P99 95% CI")
    ax.plot(times, p99_means, linewidth=2, color="#E65100", label="P99 (mean)")

    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("Response Time (ms)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"P95 & P99 Response Time Over Time - {label}\n(95% CI band, n={len(p95_per_run)} runs)")
    ax.legend()

    path = os.path.join(OUTPUT_DIR, "p95_p99_response_time_ci.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_throughput_over_time(k6_csv_files, label):
    if not k6_csv_files:
        print("WARNING: No k6 CSV files found, skipping throughput time-series.")
        return

    print("  Loading throughput data from k6 CSV files...")
    per_run_dfs = []
    for i, csv_path in enumerate(k6_csv_files):
        df = extract_throughput_from_k6_csv(csv_path)
        if not df.empty:
            per_run_dfs.append(df)
            print(f"    Run {i+1}: {len(df)} bins, duration {df['seconds'].max():.0f}s")
        else:
            print(f"    Run {i+1}: no data")

    if not per_run_dfs:
        print("WARNING: No throughput data found in CSV files.")
        return

    print(f"  {len(per_run_dfs)} valid runs")

    aligned = align_runs_to_time_bins(per_run_dfs, "val")
    if aligned.empty:
        print("WARNING: Could not align throughput data into common bins.")
        return

    means, lowers, uppers = compute_mean_ci_band(aligned, min_value=0)
    times = aligned["bin_start"].values

    fig, ax = plt.subplots()
    ax.fill_between(times, lowers, uppers, alpha=0.25, color="#4CAF50", label="95% CI")
    ax.plot(times, means, linewidth=2, color="#2E7D32", label="Mean")
    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("Throughput (requests/second)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Throughput Over Time - {label}\n(95% CI band, n={len(per_run_dfs)} runs)")
    ax.legend()

    path = os.path.join(OUTPUT_DIR, "throughput_over_time_ci.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_cpu_over_time_with_ci(stats_files, label):
    if not stats_files:
        return

    per_run_data = []
    for csv in stats_files:
        df = load_stats_csv(csv)
        if not df.empty:
            per_run_data.append(df)

    if not per_run_data:
        return

    services = sorted(set().union(*[set(df["service"].unique()) for df in per_run_data]))
    svc_colors = {
        "WebUI": "#F44336",
        "Auth": "#FF9800",
        "Image": "#FFC107",
        "Persistence": "#4CAF50",
        "Recommender": "#2196F3",
        "Registry": "#9C27B0",
        "Database": "#607D8B",
    }

    fig, ax = plt.subplots(figsize=(12, 7))

    for service in services:
        per_run_svc = []

        for df in per_run_data:
            svc_df = df[df["service"] == service][["seconds", "cpu"]].copy()
            if not svc_df.empty:
                svc_df = svc_df.groupby("seconds", as_index=False)["cpu"].sum()
                per_run_svc.append(svc_df)

        if not per_run_svc:
            continue

        aligned = align_runs_to_time_bins(per_run_svc, "cpu")
        if aligned.empty:
            continue

        means, lowers, uppers = compute_mean_ci_band(aligned, min_value=0)
        times = aligned["bin_start"].values

        color = svc_colors.get(service, "#333333")
        ax.fill_between(times, lowers, uppers, alpha=0.18, color=color)
        ax.plot(times, means, linewidth=1.8, label=service, color=color)

    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("CPU Utilization (%)")
    ax.set_ylim(bottom=0)
    ax.set_title(f"CPU Utilization per Microservice Over Time - {label}\n(95% CI band, n={len(per_run_data)} runs)")
    ax.legend(loc="upper left", ncol=2)

    path = os.path.join(OUTPUT_DIR, "cpu_over_time_ci.png")
    fig.savefig(path)
    plt.close(fig)
    print(f"Saved: {path}")


# ============================================================
# Main
# ============================================================
def main():
    args = parse_args()
    label = args.label or args.scenario.title()

    print("=" * 60)
    print(f"TeaStore Multi-Run Graph Generator - {label}")
    print("=" * 60)

    # ---- k6 JSON summary files ----
    json_files = find_run_files(args.data_dir, args.scenario)
    if not json_files:
        print(f"ERROR: No files matching {args.scenario}_results_run*.json in {args.data_dir}")
        return

    print(f"\nFound {len(json_files)} k6 run(s):")
    for f in json_files:
        print(f"  - {os.path.basename(f)}")

    runs_data = [extract_run_metrics(f) for f in json_files]
    runs_df = pd.DataFrame(runs_data)

    # ---- Docker stats CSV files ----
    stats_pattern = os.path.join(args.stats_dir, f"stats_{args.scenario}_run*.csv")
    stats_files = sorted(
        glob.glob(stats_pattern),
        key=lambda p: int(re.search(r"run(\d+)", p).group(1))
    )

    if stats_files:
        print(f"\nFound {len(stats_files)} stats CSV(s):")
        for f in stats_files:
            print(f"  - {os.path.basename(f)}")
        cpu_runs = [cpu_means_per_service(f) for f in stats_files]
        cpu_runs = [r for r in cpu_runs if r]
    else:
        print(f"\nWARNING: No stats CSVs found in {args.stats_dir}")
        cpu_runs = []

    # ---- k6 CSV files (for time-series graphs) ----
    k6_csv_files = find_k6_csv_files(args.k6_csv_dir, args.scenario)
    if k6_csv_files:
        print(f"\nFound {len(k6_csv_files)} k6 CSV file(s):")
        for f in k6_csv_files:
            print(f"  - {os.path.basename(f)}")
    else:
        print(f"\nWARNING: No k6 CSV files found in {args.k6_csv_dir}")

    # ---- Bar charts ----
    print("\nGenerating bar charts...")
    plot_response_time_breakdown(runs_df, label)
    plot_throughput_bar(runs_df, label)
    plot_cpu_per_service_bar(cpu_runs, label)

    # ---- Time-series CI band charts ----
    print("\nGenerating time-series CI band charts...")
    plot_cpu_over_time_with_ci(stats_files, label)
    plot_response_time_over_time(k6_csv_files, label)
    plot_p95_p99_over_time(k6_csv_files, label)
    plot_throughput_over_time(k6_csv_files, label)

    print("\n" + "=" * 60)
    print(f"All graphs saved to: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()