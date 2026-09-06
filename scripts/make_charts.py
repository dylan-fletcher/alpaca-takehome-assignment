"""Render the final ranking to PNGs for README.md.

    uv run -- python scripts/make_charts.py

The assignment lists a visualisation of the result as a nice-to-have. Committing
the images means a reviewer sees the answers without running the pipeline, and
`./run.sh` regenerates them so they cannot drift from the data.

Two panels sharing one hour axis, rather than two sorted rankings side by side.
Sorting each panel by its own metric would make each answer pop, but it would
also hide the finding that matters: 22:00 wins on return and *loses* on
drawdown. Aligned on the hour, that disagreement is the thing you see first.

Light and dark variants are written for the `<picture>` element in README.md,
so the chart follows the reader's GitHub theme.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

# Agg: no display needed, so this runs the same in CI or over SSH. Must be set
# before pyplot is imported.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, fetch  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"

QUERY = """
SELECT trade_hour, total_return_pct, max_drawdown_pct
FROM analytics_facts.fct_btcusdt_strategy_by_hour
ORDER BY trade_hour
"""

# Palettes are per-mode rather than one set of colours on a transparent
# background: an image with no ground of its own inherits GitHub's, and the
# text then disappears in whichever theme it was not drawn for.
THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "text": "#0b0b0b",
        "muted": "#52514e",
        "grid": "#e6e5e1",
        "gain": "#2a78d6",   # diverging pair: blue for gain,
        "loss": "#e34948",   # red for loss, around a zero midpoint
    },
    "dark": {
        "surface": "#1a1a19",
        "text": "#ffffff",
        "muted": "#c3c2b7",
        "grid": "#383835",
        "gain": "#3987e5",
        "loss": "#e66767",
    },
}


def panel(ax, hours, values, theme, *, title, subtitle, best_is_max=True):
    """One horizontal bar panel, coloured by sign and annotated at its winner.

    Colour encodes the sign of the value, never the rank -- the winning bar is
    called out with a label rather than a colour of its own, so the palette
    keeps meaning the same thing in every row.
    """
    colours = [theme["gain"] if v >= 0 else theme["loss"] for v in values]
    ax.barh(hours, values, color=colours, height=0.72, zorder=3)

    ax.set_title(title, color=theme["text"], fontsize=11, weight="bold",
                 loc="left", pad=26)
    ax.text(0, 1.012, subtitle, transform=ax.transAxes, color=theme["muted"],
            fontsize=8.5, va="bottom")

    # The answer this panel gives: the largest value either way, since a
    # drawdown closest to zero is the shallowest one.
    pick = max(range(len(values)), key=lambda i: values[i] if best_is_max else -values[i])
    # A bbox in the surface colour, because the label sits at the end of one
    # bar and would otherwise be read through its neighbours.
    ax.annotate(
        f"{hours[pick]:02d}:00   {values[pick]:+.2f}%",
        xy=(values[pick], hours[pick]),
        xytext=(7 if values[pick] >= 0 else -7, 0),
        textcoords="offset points",
        ha="left" if values[pick] >= 0 else "right",
        va="center", fontsize=9.5, weight="bold", color=theme["text"], zorder=5,
        bbox=dict(boxstyle="round,pad=0.32", fc=theme["surface"], ec="none"),
    )

    ax.axvline(0, color=theme["muted"], lw=1, zorder=2)

    # Every hour labelled, not every other one: with 24 bars and 12 ticks there
    # is no way to tell which bar a label belongs to.
    ax.set_yticks(range(24))
    ax.set_yticklabels([f"{h:02d}:00" for h in range(24)])
    # An explicit limit rather than invert_yaxis(): the axes are shared, so
    # inverting once per panel would flip it back and silently undo itself.
    ax.set_ylim(23.6, -0.6)  # 00:00 at the top, so the axis reads like a clock

    # A little breathing room on both sides, and more on whichever side the
    # callout sits, so the label never runs off the panel.
    low, high = min(min(values), 0), max(max(values), 0)
    span = high - low
    callout_left = values[pick] < 0
    ax.set_xlim(
        low - span * (0.22 if callout_left else 0.06),
        high + span * (0.06 if callout_left else 0.22),
    )
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")

    ax.set_facecolor(theme["surface"])
    ax.grid(axis="x", color=theme["grid"], lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=theme["muted"], labelsize=8.5, length=0)


def render(rows, mode: str) -> Path:
    theme = THEMES[mode]
    hours = [int(r[0]) for r in rows]
    returns = [float(r[1]) for r in rows]
    drawdowns = [float(r[2]) for r in rows]

    fig, (left, right) = plt.subplots(
        1, 2, figsize=(11, 6.8), sharey=True, facecolor=theme["surface"]
    )
    panel(left, hours, returns, theme,
          title="Q1  Total return by hour",
          subtitle="Compounded across every trade. Higher is better.")
    panel(right, hours, drawdowns, theme,
          title="Q2  Maximum drawdown by hour",
          subtitle="Deepest peak-to-trough fall. Closer to zero is better.",
          best_is_max=True)

    fig.suptitle(
        "BTCUSDT hourly backtest — 2021-02-24 to 2024-08-27 (UTC)",
        color=theme["text"], fontsize=13, weight="bold", x=0.008, ha="left", y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))

    out = OUT_DIR / f"ranking-{mode}.png"
    # metadata={} suppresses matplotlib's version stamp, so re-running on
    # unchanged data produces a byte-identical file and leaves git clean.
    fig.savefig(out, dpi=150, facecolor=theme["surface"], metadata={})
    plt.close(fig)
    return out


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    rows = fetch(connect(retries=3), QUERY)
    if len(rows) < 2:
        # A single row means the build was restricted to one hour; there is no
        # ranking to draw, and overwriting the committed charts with one bar
        # would be worse than leaving them alone.
        print("fewer than 2 hours in the summary; leaving the charts as they are")
        return
    for mode in THEMES:
        print(f"wrote {render(rows, mode).relative_to(OUT_DIR.parent)}")


if __name__ == "__main__":
    main()
