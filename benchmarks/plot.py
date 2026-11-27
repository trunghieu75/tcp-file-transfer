"""TV5 - Ve bieu do va bang tom tat tu ket qua benchmark.

Chay tu thu muc goc repository sau ``python -m benchmarks.bench``:
    python -m benchmarks.plot
    python -m benchmarks.plot --summary results/bench_summary_lan.csv --tag lan

Dau ra:
    plots/tn1_throughput.png, plots/tn1_time.png  - theo kich thuoc file
    plots/tn2_throughput.png                      - theo chunk size Client
    plots/tn3_throughput.png, plots/tn3_time.png  - theo so Client dong thoi
    results/bench_summary[_tag].md                - bang Markdown dua vao bao cao
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from benchmarks.bench import KiB, MiB, RESULTS_DIR, ROOT, read_csv  # noqa: E402


PLOTS_DIR = ROOT / "plots"
OP_LABELS = {"upload": "Upload", "download": "Download",
             "aggregate": "Tổng (mọi Client, upload + download)"}
OP_COLORS = {"upload": "#2a78c8", "download": "#e07b28", "aggregate": "#3a9a5b"}


def _size_label(size):
    size = int(size)
    if size >= MiB:
        return f"{size / MiB:g} MiB"
    if size >= KiB:
        return f"{size / KiB:g} KiB"
    return f"{size} B"


def _select(summary, experiment, metric):
    """{operation: [(x_key_row, mean, std), ...]} cho mot thi nghiem."""
    series = {}
    for row in summary:
        if row["experiment"] != experiment or row["metric"] != metric:
            continue
        if row["mean"] == "":
            continue
        series.setdefault(row["operation"], []).append(row)
    return series


def _bar_chart(series, x_field, x_label, y_label, title, path, env_note,
               label_fn=str, ops=("upload", "download", "aggregate")):
    ops = [op for op in ops if op in series]
    if not ops:
        return None
    xs = sorted({int(r[x_field]) for op in ops for r in series[op]})
    width = 0.8 / len(ops)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for i, op in enumerate(ops):
        by_x = {int(r[x_field]): r for r in series[op]}
        pos = [j + (i - (len(ops) - 1) / 2) * width for j, x in enumerate(xs) if x in by_x]
        # Median + thanh min..max: phan bo co duoi dai (vai luot cham dot bien),
        # nen median dai dien tot hon trung binh.
        meds = [float(by_x[x]["median"]) for x in xs if x in by_x]
        lows = [m - float(by_x[x]["min"]) for m, x in zip(meds, [x for x in xs if x in by_x])]
        highs = [float(by_x[x]["max"]) - m for m, x in zip(meds, [x for x in xs if x in by_x])]
        bars = ax.bar(pos, meds, width, yerr=[lows, highs], capsize=4,
                      label=OP_LABELS.get(op, op), color=OP_COLORS.get(op))
        ax.bar_label(bars, fmt="%.1f", fontsize=8, padding=2)
    ax.set_xticks(range(len(xs)), [label_fn(x) for x in xs])
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_ylim(bottom=0)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend(fontsize=8)
    fig.text(0.01, 0.01, env_note, fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def make_plots(summary, out_dir, env=None, tag=""):
    suffix = f"_{tag}" if tag else ""
    env = env or {}
    note = (f"{env.get('network', '')} | {env.get('os', '')} | Python {env.get('python', '')}"
            f" | {env.get('repeat', '?')} lần đo; cột = median, thanh = min–max")
    out_dir = Path(out_dir)
    made = []
    specs = [
        ("TN1", "throughput_mib_s", "file_size", "Kích thước file", "Throughput (MiB/s)",
         "TN1 – Throughput theo kích thước file (chunk 4 KiB)", "tn1_throughput", _size_label,
         ("upload", "download")),
        ("TN1", "elapsed_s", "file_size", "Kích thước file", "Thời gian hoàn tất (s)",
         "TN1 – Thời gian hoàn tất theo kích thước file", "tn1_time", _size_label,
         ("upload", "download")),
        ("TN2", "throughput_mib_s", "chunk_size", "Chunk size phía Client",
         "Throughput (MiB/s)", "TN2 – Throughput theo chunk size (server cố định 4 KiB)",
         "tn2_throughput", _size_label, ("upload", "download")),
        ("TN3", "throughput_mib_s", "clients", "Số Client đồng thời", "Throughput (MiB/s)",
         "TN3 – Throughput mỗi Client và tổng", "tn3_throughput", str,
         ("upload", "download", "aggregate")),
        ("TN3", "elapsed_s", "clients", "Số Client đồng thời", "Thời gian (s)",
         "TN3 – Thời gian mỗi Client / cả lượt", "tn3_time", str,
         ("upload", "download", "aggregate")),
    ]
    for exp, metric, x_field, x_label, y_label, title, name, fn, ops in specs:
        series = _select(summary, exp, metric)
        path = _bar_chart(series, x_field, x_label, y_label, title,
                          out_dir / f"{name}{suffix}.png", note, fn, ops)
        if path:
            made.append(path)
    return made


def markdown_table(summary):
    """Bang Markdown: moi dong mot cau hinh, cot throughput/thoi gian trung binh ± std."""
    rows = {}
    for row in summary:
        key = (row["experiment"], row["operation"], int(row["file_size"]),
               int(row["chunk_size"]), int(row["clients"]))
        rows.setdefault(key, {"n": row["n"], "rate": row["success_rate"]})[row["metric"]] = row

    def cell(r):
        if r is None or r["mean"] == "":
            return "— | —"
        return f"{float(r['median']):.3f} | {float(r['mean']):.3f} ± {float(r['std']):.3f}"

    lines = [
        "| TN | Thao tác | File | Chunk Client | Client | Số lượt | Thành công | "
        "Thời gian median (s) | Thời gian TB ± std (s) | "
        "Throughput median (MiB/s) | Throughput TB ± std (MiB/s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key in sorted(rows):
        exp, op, size, chunk, clients = key
        if op == "aggregate" and exp != "TN3":
            continue  # 1 Client: tong = upload + download, khong can trong TN1/TN2
        info = rows[key]
        lines.append(
            f"| {exp} | {op} | {_size_label(size)} | {_size_label(chunk)} | {clients} | "
            f"{info['n']} | {float(info['rate']) * 100:.0f}% | "
            f"{cell(info.get('elapsed_s'))} | {cell(info.get('throughput_mib_s'))} |"
        )
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ve bieu do benchmark TV5")
    parser.add_argument("--tag", default="")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--env", type=Path)
    parser.add_argument("--plots-dir", type=Path, default=PLOTS_DIR)
    parser.add_argument("--md", type=Path)
    args = parser.parse_args(argv)
    suffix = f"_{args.tag}" if args.tag else ""
    summary_path = args.summary or RESULTS_DIR / f"bench_summary{suffix}.csv"
    env_path = args.env or summary_path.with_name(f"bench_env{suffix}.json")
    md_path = args.md or summary_path.with_suffix(".md")

    summary = read_csv(summary_path)
    env = json.loads(env_path.read_text(encoding="utf-8")) if env_path.is_file() else {}
    made = make_plots(summary, args.plots_dir, env, args.tag)
    md_path.write_text(markdown_table(summary), encoding="utf-8")
    for path in made + [md_path]:
        print(f"Da ghi {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
