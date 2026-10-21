#!/usr/bin/env python3
"""Build a self-contained HTML report of the profiling problems, decisions and architecture.

Reads the finished DuckDB database read-only (plus a few raw files for the "as found" views)
and draws every chart as inline SVG with the standard library only, so it needs no extra
dependency. Usage: python scripts/build_profiling_report.py
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
e = html.escape
FONT = "font-family:system-ui,'Segoe UI',Helvetica,Arial,sans-serif"
BAD, GOOD, WARN, BLUE, PURPLE, GREY = "#c0392b", "#1e8449", "#d68910", "#2874a6", "#7d3c98", "#95a5a6"

# ----------------------------------------------------------------------------- chart helpers


def _svg(w, h, body):
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px;{FONT}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img">{body}</svg>')


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 100 or v == int(v) else f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _nice_step(vmax, n=5):
    raw = max(vmax, 1e-9) / n
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _legend(names_colors, x, y):
    out, cx = [], x
    for name, color in names_colors:
        out.append(f'<rect x="{cx}" y="{y - 9}" width="11" height="11" fill="{color}"/>'
                   f'<text x="{cx + 15}" y="{y}" font-size="11" fill="#333">{e(name)}</text>')
        cx += 22 + 6.2 * len(name)
    return "".join(out)


def bar_chart(labels, series, *, w=780, h=300, title="", ylabel="", stacked=False, log=False,
              ymax=None, bar_colors=None, ref=None, rot=-45, show_values=None, label_every=1):
    """Grouped or stacked vertical bars. series = [(name, values, color)]."""
    L, R, T = 62, 16, 44
    B = 74 if rot else 40
    pw, ph, n = w - L - R, h - T - B, len(labels)
    if stacked:
        vmax = max([sum((s[1][i] or 0) for s in series) for i in range(n)] + [1])
    else:
        vmax = max([(v or 0) for s in series for v in s[1]] + [1])
    vmax = ymax or vmax
    if log:
        top = 10 ** math.ceil(math.log10(max(vmax, 1)))
        lo = 0.5
        ticks = [10 ** k for k in range(0, int(math.log10(top)) + 1)]

        def sy(v):
            if v <= 0:
                return T + ph
            f = (math.log10(max(v, lo)) - math.log10(lo)) / (math.log10(top) - math.log10(lo))
            return T + ph - f * ph
    else:
        step = _nice_step(vmax)
        top = math.ceil(vmax / step) * step
        ticks = [round(i * step, 6) for i in range(int(round(top / step)) + 1)]

        def sy(v):
            return T + ph - (v / top) * ph
    out = [f'<text x="{L}" y="18" font-size="13" font-weight="600" fill="#222">{e(title)}</text>']
    named = [(s[0], s[2]) for s in series if s[0]]
    if named:
        out.append(_legend(named, L, 34))
    for t in ticks:
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e5e7e9"/>'
                   f'<text x="{L - 6}" y="{y + 4:.1f}" font-size="10" text-anchor="end" fill="#555">{_fmt(t)}</text>')
    if ylabel:
        out.append(f'<text transform="rotate(-90 14 {T + ph / 2})" x="14" y="{T + ph / 2}" '
                   f'font-size="10" text-anchor="middle" fill="#555">{e(ylabel)}</text>')
    gw = pw / max(n, 1)
    sv = n <= 16 if show_values is None else show_values
    for i, lab in enumerate(labels):
        base = T + ph
        if stacked:
            bw = gw * 0.7
            x = L + i * gw + gw * 0.15
            acc = 0
            for name, vals, color in series:
                v = vals[i] or 0
                if v <= 0:
                    continue
                y1, y0 = sy(acc + v), sy(acc)
                out.append(f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(y0 - y1, 0):.1f}" '
                           f'fill="{color}"><title>{e(str(lab))} - {e(name)}: {_fmt(v)}</title></rect>')
                acc += v
            if sv and acc:
                out.append(f'<text x="{x + bw / 2:.1f}" y="{sy(acc) - 4:.1f}" font-size="9" '
                           f'text-anchor="middle" fill="#333">{_fmt(acc)}</text>')
        else:
            bw = gw * 0.8 / len(series)
            for j, (name, vals, color) in enumerate(series):
                v = vals[i] or 0
                x = L + i * gw + gw * 0.1 + j * bw
                col = bar_colors[i] if bar_colors and len(series) == 1 else color
                y = sy(v)
                if v > 0:
                    out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{base - y:.1f}" '
                               f'fill="{col}"><title>{e(str(lab))} - {e(name)}: {_fmt(v)}</title></rect>')
                if sv:
                    out.append(f'<text x="{x + bw / 2:.1f}" y="{y - 4:.1f}" font-size="9" '
                               f'text-anchor="middle" fill="#333">{_fmt(v)}</text>')
        lx, ly = L + i * gw + gw / 2, T + ph + 12
        if i % label_every:
            continue
        if rot:
            out.append(f'<text transform="rotate({rot} {lx:.1f} {ly})" x="{lx:.1f}" y="{ly}" font-size="9" '
                       f'text-anchor="end" fill="#444">{e(str(lab))}</text>')
        else:
            out.append(f'<text x="{lx:.1f}" y="{ly + 2}" font-size="10" text-anchor="middle" fill="#444">{e(str(lab))}</text>')
    if ref:
        y = sy(ref[0])
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{ref[2]}" stroke-dasharray="5 4"/>'
                   f'<text x="{w - R}" y="{y - 4:.1f}" font-size="10" text-anchor="end" fill="{ref[2]}">{e(ref[1])}</text>')
    out.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{T + ph}" stroke="#888"/>'
               f'<line x1="{L}" x2="{w - R}" y1="{T + ph}" y2="{T + ph}" stroke="#888"/>')
    return _svg(w, h, "".join(out))


def hbar_chart(labels, values, colors, *, w=780, title="", log=True, legend=None, rowh=22):
    L, R, T = 250, 60, 44
    n = len(labels)
    h = T + n * rowh + 16
    vmax = max(values + [1])
    top = 10 ** math.ceil(math.log10(vmax)) if log else vmax

    def sx(v):
        if v <= 0:
            return L
        if log:
            f = (math.log10(max(v, 0.5)) - math.log10(0.5)) / (math.log10(top) - math.log10(0.5))
        else:
            f = v / top
        return L + f * (w - L - R)

    out = [f'<text x="10" y="18" font-size="13" font-weight="600" fill="#222">{e(title)}</text>']
    if legend:
        out.append(_legend(legend, 10, 34))
    for i, (lab, v, c) in enumerate(zip(labels, values, colors)):
        y = T + i * rowh
        out.append(f'<text x="{L - 6}" y="{y + 14}" font-size="10.5" text-anchor="end" fill="#333">{e(lab)}</text>'
                   f'<rect x="{L}" y="{y + 2}" width="{max(sx(v) - L, 0):.1f}" height="{rowh - 6}" fill="{c}">'
                   f'<title>{e(lab)}: {_fmt(v)}</title></rect>'
                   f'<text x="{sx(v) + 5:.1f}" y="{y + 14}" font-size="10.5" fill="#333">{_fmt(v)}</text>')
    out.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{T + n * rowh}" stroke="#888"/>')
    return _svg(w, h, "".join(out))


def line_chart(series, *, w=780, h=270, title="", xlabel="", ylabel="", ymin=None, ymax=None,
               xmin=0, xmax=1, xticks=(), bands=()):
    """series = [(name, [(x, y|None)], color)]; a None y breaks the line."""
    L, R, T, B = 62, 16, 44, 44
    pw, ph = w - L - R, h - T - B
    ys = [y for s in series for _, y in s[1] if y is not None]
    lo = ymin if ymin is not None else min(ys)
    hi = ymax if ymax is not None else max(ys)
    step = _nice_step(hi - lo, 4)
    lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step

    def sx(x):
        return L + (x - xmin) / (xmax - xmin) * pw

    def sy(y):
        return T + ph - (y - lo) / (hi - lo) * ph

    out = [f'<text x="{L}" y="18" font-size="13" font-weight="600" fill="#222">{e(title)}</text>',
           _legend([(s[0], s[2]) for s in series], L, 34)]
    t = lo
    while t <= hi + 1e-9:
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="#e5e7e9"/>'
                   f'<text x="{L - 6}" y="{sy(t) + 4:.1f}" font-size="10" text-anchor="end" fill="#555">{_fmt(round(t, 4))}</text>')
        t += step
    for x0, x1, label, color in bands:
        out.append(f'<rect x="{sx(x0):.1f}" y="{T}" width="{sx(x1) - sx(x0):.1f}" height="{ph}" fill="{color}" opacity="0.13"/>'
                   f'<text x="{(sx(x0) + sx(x1)) / 2:.1f}" y="{T + 14}" font-size="10" text-anchor="middle" fill="{color}">{e(label)}</text>')
    for xv, lab in xticks:
        out.append(f'<line x1="{sx(xv):.1f}" x2="{sx(xv):.1f}" y1="{T + ph}" y2="{T + ph + 4}" stroke="#888"/>'
                   f'<text x="{sx(xv):.1f}" y="{T + ph + 16}" font-size="10" text-anchor="middle" fill="#444">{e(lab)}</text>')
    if xlabel:
        out.append(f'<text x="{L + pw / 2}" y="{h - 6}" font-size="10" text-anchor="middle" fill="#555">{e(xlabel)}</text>')
    if ylabel:
        out.append(f'<text transform="rotate(-90 14 {T + ph / 2})" x="14" y="{T + ph / 2}" font-size="10" '
                   f'text-anchor="middle" fill="#555">{e(ylabel)}</text>')
    for name, pts, color in series:
        d, pen = [], False
        for x, y in pts:
            if y is None:
                pen = False
                continue
            d.append(f'{"L" if pen else "M"}{sx(x):.1f},{sy(y):.1f}')
            pen = True
        out.append(f'<path d="{" ".join(d)}" fill="none" stroke="{color}" stroke-width="1.6"/>')
    out.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{T + ph}" stroke="#888"/>'
               f'<line x1="{L}" x2="{w - R}" y1="{T + ph}" y2="{T + ph}" stroke="#888"/>')
    return _svg(w, h, "".join(out))


def heatmap(rows, cols, color_fn, tip_fn, *, cw=20, ch=11, title="", legend=()):
    L, T = 48, 70
    w, h = L + len(cols) * cw + 12, T + len(rows) * ch + 10
    out = [f'<text x="{L}" y="18" font-size="13" font-weight="600" fill="#222">{e(title)}</text>',
           _legend(list(legend), L, 36)]
    for j, c in enumerate(cols):
        if j % 2 == 0:
            out.append(f'<text transform="rotate(-90 {L + j * cw + cw / 2 + 3} {T - 4})" x="{L + j * cw + cw / 2 + 3}" '
                       f'y="{T - 4}" font-size="8.5" fill="#555">{e(c)}</text>')
    for i, r in enumerate(rows):
        out.append(f'<text x="{L - 4}" y="{T + i * ch + ch - 2}" font-size="8" text-anchor="end" fill="#555">{e(r)}</text>')
        for j, c in enumerate(cols):
            out.append(f'<rect x="{L + j * cw}" y="{T + i * ch}" width="{cw - 1}" height="{ch - 1}" '
                       f'fill="{color_fn(r, c)}"><title>{e(tip_fn(r, c))}</title></rect>')
    return _svg(w, h, "".join(out))


STAGE_COLORS = {"light": "#85c1e9", "deep": "#1b4f72", "rem": "#af7ac5", "awake": "#f5b041"}


def stage_timeline(stages, onset, sleep_end, *, w=780, title=""):
    """stages = [(name, start_min, end_min)] measured from sleep onset; sleep_end in minutes."""
    L, R, T = 40, 20, 62
    total = max(s[2] for s in stages)
    pw = w - L - R

    def sx(m):
        return L + m / total * pw

    out = [f'<text x="{L}" y="18" font-size="13" font-weight="600" fill="#222">{e(title)}</text>',
           _legend([(k, v) for k, v in STAGE_COLORS.items()], L, 36)]
    out.append(f'<rect x="{sx(sleep_end):.1f}" y="{T - 6}" width="{sx(total) - sx(sleep_end):.1f}" height="64" '
               f'fill="{BAD}" opacity="0.12"/>')
    for name, a, b in stages:
        out.append(f'<rect x="{sx(a):.1f}" y="{T}" width="{max(sx(b) - sx(a), 0.5):.1f}" height="40" '
                   f'fill="{STAGE_COLORS.get(name, GREY)}" stroke="#fff"><title>{e(name)} {a:.0f}-{b:.0f} min</title></rect>')
    for xm, lab, col in ((0, "sleep_onset", "#333"), (sleep_end, "sleep_end (wake-up)", BAD)):
        out.append(f'<line x1="{sx(xm):.1f}" x2="{sx(xm):.1f}" y1="{T - 8}" y2="{T + 52}" stroke="{col}" stroke-width="2"/>'
                   f'<text x="{sx(xm) + 4:.1f}" y="{T + 66}" font-size="10" fill="{col}">{e(lab)}</text>')
    out.append(f'<text x="{(sx(sleep_end) + sx(total)) / 2:.1f}" y="{T + 88}" font-size="10" text-anchor="middle" '
               f'fill="{BAD}">awake tail after sleep_end</text>')
    for m in range(0, int(total) + 1, 60):
        out.append(f'<text x="{sx(m):.1f}" y="{T + 104}" font-size="9" text-anchor="middle" fill="#666">{m} min</text>')
    return _svg(w, T + 112, "".join(out))


def table(headers, rows, *, hl=(), caption=""):
    th = "".join(f"<th>{e(str(h))}</th>" for h in headers)
    body = []
    for r in rows:
        cells = []
        for h, v in zip(headers, r):
            if v is None:
                txt = '<span class="null">NULL</span>'
            elif isinstance(v, float):
                txt = e(f"{v:,.2f}")
            elif isinstance(v, int) and not isinstance(v, bool):
                txt = e(f"{v:,}")
            else:
                txt = e(str(v))
            cells.append(f'<td class="{"hl" if h in hl else ""}">{txt}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    cap = f"<caption>{e(caption)}</caption>" if caption else ""
    return f'<div class="tw"><table>{cap}<thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def lerp_color(a, b, f):
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(pa, pb))


# ----------------------------------------------------------------------------- page helpers


def panel(kind, title, inner):
    return f'<div class="panel {kind}"><h4>{e(title)}</h4>{inner}</div>'


def card(fid, title, severity, status, detected, decision, why, problem, resolution, note=""):
    chips = (f'<span class="chip sev-{severity.lower()}">{e(severity)}</span>'
             f'<span class="chip st">{e(status)}</span>')
    meta = (f'<dl><dt>How it was detected</dt><dd>{detected}</dd>'
            f'<dt>Human decision</dt><dd>{decision}</dd>'
            f'<dt>Why</dt><dd>{why}</dd></dl>')
    extra = f'<p class="note">{note}</p>' if note else ""
    return (f'<section class="card" id="{fid}"><h3>{e(title)} {chips}</h3>{meta}{extra}'
            f'{panel("problem", "Problem - as found", problem)}'
            f'{panel("resolution", "Resolution - as implemented", resolution)}</section>')


CSS = """
:root{--bad:#c0392b;--good:#1e8449;--ink:#1c2833;--mut:#5d6d7e}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:#f4f6f7;line-height:1.5;
font-family:system-ui,'Segoe UI',Helvetica,Arial,sans-serif}
header{background:#1b2631;color:#fff;padding:28px 40px}header h1{margin:0 0 6px;font-size:26px}
header p{margin:2px 0;color:#aeb6bf;font-size:14px}
nav{position:sticky;top:0;background:#fff;border-bottom:1px solid #d5d8dc;padding:8px 40px;z-index:5;
font-size:13px;display:flex;flex-wrap:wrap;gap:6px 16px}nav a{color:#2874a6;text-decoration:none}
main{max-width:1180px;margin:0 auto;padding:10px 24px 60px}
h2{margin-top:44px;padding-bottom:6px;border-bottom:2px solid #1b2631}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.kpi{background:#fff;border:1px solid #d5d8dc;
border-radius:6px;padding:10px 16px;min-width:150px}.kpi b{display:block;font-size:22px}
.kpi span{font-size:12px;color:var(--mut)}
.card{background:#fff;border:1px solid #d5d8dc;border-radius:8px;padding:18px 22px;margin:22px 0}
.card h3{margin:0 0 8px}.chip{font-size:11px;font-weight:600;border-radius:10px;padding:2px 9px;margin-left:8px;
vertical-align:middle}.sev-high{background:#fadbd8;color:#922b21}.sev-medium{background:#fdebd0;color:#935116}
.sev-low{background:#d6eaf8;color:#1b4f72}.st{background:#e8f6f3;color:#0e6251}
dl{display:grid;grid-template-columns:170px 1fr;gap:4px 14px;font-size:14px;margin:8px 0}
dt{font-weight:600;color:var(--mut)}dd{margin:0}.note{font-size:13px;background:#fef9e7;border-left:4px solid #f4d03f;
padding:6px 10px}
.panel{border-left:5px solid;border-radius:4px;padding:6px 16px 12px;margin:14px 0;background:#fcfcfc}
.panel.problem{border-color:var(--bad)}.panel.resolution{border-color:var(--good)}
.panel h4{margin:6px 0 8px;font-size:14px}.panel.problem h4{color:var(--bad)}.panel.resolution h4{color:var(--good)}
.tw{overflow-x:auto;margin:10px 0}table{border-collapse:collapse;font-size:12.5px;width:100%}
th,td{border:1px solid #d5d8dc;padding:4px 8px;text-align:left;white-space:nowrap}th{background:#eaeded}
caption{text-align:left;font-weight:600;font-size:12.5px;padding:4px 0;color:var(--mut)}
td.hl{background:#fcf3cf;font-weight:600}.null{color:#a93226;font-style:italic}
.small{font-size:13px;color:var(--mut)}.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.cols,dl{grid-template-columns:1fr}}
.arch{background:#fff;border:1px solid #d5d8dc;border-radius:8px;padding:14px;margin:14px 0}
.dec td{white-space:normal;vertical-align:top}.proposed{background:#fef5e7}
footer{text-align:center;color:var(--mut);font-size:12px;padding:20px}
"""

# ----------------------------------------------------------------------------- architecture diagrams


def _node(x, y, w, h, lines, fill, stroke, dashed=False):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    t = "".join(f'<tspan x="{x + w / 2}" dy="{14 if i else 0}" '
                f'font-weight="{600 if i == 0 else 400}">{e(l)}</tspan>' for i, l in enumerate(lines))
    ty = y + h / 2 - (len(lines) - 1) * 7 + 4
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1.6"{dash}/><text x="{x + w / 2}" y="{ty}" font-size="11" text-anchor="middle" '
            f'fill="#1c2833">{t}</text>')


def _edge(a, b, dashed=False, label=""):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if bx >= ax + aw - 1:
        x1, y1, x2, y2 = ax + aw, ay + ah / 2, bx, by + bh / 2
    elif by >= ay + ah:
        x1, y1, x2, y2 = ax + aw / 2, ay + ah, bx + bw / 2, by
    else:
        x1, y1, x2, y2 = ax + aw / 2, ay, bx + bw / 2, by + bh
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    lab = (f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 - 4}" font-size="9.5" text-anchor="middle" '
           f'fill="#555">{e(label)}</text>') if label else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#566573" stroke-width="1.5"{dash} '
            f'marker-end="url(#arr)"/>{lab}')


ARROW = ('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#566573"/></marker></defs>')


def local_architecture():
    N = {
        "raw": (20, 150, 140, 90), "chk": (190, 150, 150, 90), "bronze": (370, 150, 150, 90),
        "silver": (550, 150, 170, 90), "daily": (770, 160, 170, 70), "weekly": (770, 40, 170, 70),
        "cohort": (770, 280, 170, 70), "out": (990, 150, 170, 90), "qual": (550, 320, 170, 80),
        "state": (370, 320, 150, 80), "qr": (950, 410, 110, 70), "cmp": (1070, 410, 100, 70),
    }
    spec = {
        "raw": (["Raw exports", "/data (read-only mount)", "participants, survey,", "device, sleep, steps, HR"], "#fdebd0", "#d68910"),
        "chk": (["Input checks", "run-failing only for", "missing / malformed", "inputs"], "#fadbd8", BAD),
        "bronze": (["Bronze", "raw_manifest", "path, SHA-256,", "record count"], "#d6eaf8", BLUE),
        "silver": (["Silver (shared)", "typed participants, survey,", "sleep sessions + stages,", "steps, heart rate"], "#d5f5e3", GOOD),
        "daily": (["gold_clinical_daily", "key: participant_id, local_date"], "#fcf3cf", "#b7950b"),
        "weekly": (["gold_biostat_weekly", "key: participant_id, week_id"], "#fcf3cf", "#b7950b"),
        "cohort": (["gold_chronotype_cohort", "key: participant_id"], "#fcf3cf", "#b7950b"),
        "out": (["/output (writable mount)", "wearable.duckdb", "(one DuckDB file)"], "#e8daef", PURPLE),
        "qual": (["Quality layer", "flags, withheld fields,", "quality_rejects,", "quality_results"], "#fdebd0", "#d68910"),
        "state": (["Run state", "manifest + run_id,", "full / incremental mode"], "#eaeded", "#566573"),
        "qr": (["Query runner", "read-only SQL", "queries/*.sql"], "#e8daef", PURPLE),
        "cmp": (["compare", "diff two", "output dirs"], "#e8daef", PURPLE),
    }
    body = [ARROW,
            '<rect x="8" y="8" width="1164" height="500" rx="12" fill="none" stroke="#2874a6" stroke-dasharray="3 5"/>'
            '<text x="22" y="30" font-size="12" fill="#2874a6" font-weight="600">Docker container (non-root, pinned image; docker compose service "pipeline")</text>']
    for k, (lines, fill, stroke) in spec.items():
        body.append(_node(*N[k], lines, fill, stroke))
    for a, b, d, lab in [("raw", "chk", 0, ""), ("chk", "bronze", 0, ""), ("bronze", "silver", 0, ""),
                         ("silver", "daily", 0, ""), ("daily", "weekly", 0, ""), ("daily", "cohort", 0, ""),
                         ("silver", "qual", 0, "flags"), ("bronze", "state", 1, "hash diff"),
                         ("daily", "out", 0, ""), ("weekly", "out", 0, ""), ("cohort", "out", 0, ""),
                         ("out", "qr", 0, ""), ("out", "cmp", 0, "")]:
        body.append(_edge(N[a], N[b], bool(d), lab))
    body.append('<polyline points="720,360 965,360 965,215 990,215" fill="none" stroke="#566573" stroke-width="1.5" '
                'marker-end="url(#arr)"/><text x="845" y="376" font-size="9.5" text-anchor="middle" fill="#555">'
                'quality/report.md</text>')
    return _svg(1180, 520, "".join(body))


def cloud_architecture():
    y = 230
    N = {"raw": (40, y, 150, 80), "batch": (230, y, 150, 80), "bronze": (420, y, 150, 80),
         "silver": (610, y, 150, 80), "gold": (800, y, 150, 80), "cat": (990, y, 160, 80),
         "sf": (230, 60, 150, 70), "eb": (40, 60, 150, 70)}
    spec = {
        "raw": (["S3 raw prefix", "(source exports)"], "#fdebd0", "#d68910"),
        "batch": (["AWS Batch task", "same container image", "(full / incremental)"], "#d6eaf8", BLUE),
        "bronze": (["S3 bronze prefix", "raw manifest"], "#d6eaf8", BLUE),
        "silver": (["S3 silver prefix", "typed, flagged data"], "#d5f5e3", GOOD),
        "gold": (["Iceberg gold tables", "on S3 (daily, weekly,", "cohort)"], "#fcf3cf", "#b7950b"),
        "cat": (["Glue Catalog + Athena", "catalog and SQL access"], "#e8daef", PURPLE),
        "sf": (["Step Functions", "orchestration"], "#eaeded", "#566573"),
        "eb": (["EventBridge", "schedule"], "#eaeded", "#566573"),
    }
    body = [ARROW]
    for k, (lines, fill, stroke) in spec.items():
        body.append(_node(*N[k], lines, fill, stroke, dashed=True))
    for a, b, d in [("raw", "batch", 0), ("batch", "bronze", 0), ("bronze", "silver", 0), ("silver", "gold", 0),
                    ("gold", "cat", 0), ("eb", "sf", 1), ("sf", "batch", 1)]:
        body.append(_edge(N[a], N[b], bool(d)))
    body.append('<text x="40" y="380" font-size="12" fill="#a04000" font-weight="600">Dashed outline = proposed, '
                'not implemented. No cloud component has been built, deployed or tested.</text>')
    return _svg(1180, 400, "".join(body))


# ----------------------------------------------------------------------------- report


def build(db_path: Path, data_dir: Path, out_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)

    def Q(sql, *params):
        return con.execute(sql, list(params)).fetchall()

    run_id = Q("select max(run_id) from quality_results")[0][0]
    manifest = Q("select source, count(*), sum(record_count) from raw_manifest group by 1 order by 1")
    n_gold = Q("select count(*) from gold_clinical_daily")[0][0]
    n_week = Q("select count(*) from gold_biostat_weekly")[0][0]
    n_coh = Q("select count(*) from gold_chronotype_cohort")[0][0]
    n_hr = Q("select count(*) from silver_heart_rate_minute")[0][0]
    n_st = Q("select count(*) from silver_steps_minute")[0][0]
    parts: list[str] = []

    # ---- overview: rules that fired
    rules = Q("select rule_id, source, handling, failed_count from quality_results where failed_count > 0 "
              "order by failed_count desc, rule_id")
    hcol = {"withhold_field": BAD, "flag": WARN, "convert": BLUE, "exclude_from_output": PURPLE}
    chart_rules = hbar_chart([f"{r[1]}: {r[0]}" for r in rules], [r[3] for r in rules],
                             [hcol.get(r[2], GREY) for r in rules],
                             title="Quality rules that fired in the real run (records, log scale)",
                             legend=[("withhold_field", BAD), ("flag", WARN), ("convert", BLUE),
                                     ("exclude_from_output", PURPLE)])
    rules_tbl = table(["rule_id", "source", "handling", "failed_count"], rules[:25])

    # ---- A: Z timestamps (M-05)
    raw_hr = json.loads((data_dir / "wearable_events" / "P018" / "heart_rate.json").read_text())
    naive = Counter()
    for r in raw_hr:
        ts = r["timestamp"]
        d = datetime.fromisoformat(ts[:-1] if ts.endswith("Z") else ts)
        if date(2026, 1, 24) <= d.date() <= date(2026, 1, 26):
            naive[(d.date().day, d.hour)] += 1
    keys = [(day, h) for day in (24, 25, 26) for h in range(24)]
    labels_h = [f"01-{day} {h:02d}h" for day, h in keys]
    after = {(r[0].day, r[1]): r[2] for r in Q(
        "select local_date, hour(minute_ts), count(*) from silver_heart_rate_minute where participant_id='P018' "
        "and local_date between '2026-01-24' and '2026-01-26' group by 1,2")}
    cols_a = [BAD if naive.get(k, 0) != 60 else GREY for k in keys]
    chart_a_before = bar_chart(labels_h, [("records per hour", [naive.get(k, 0) for k in keys], GREY)],
                               title="P018 heart rate, Z labels read as local clock: records per hour (expected 60)",
                               bar_colors=cols_a, ref=(60, "expected 60", GOOD), ymax=130, show_values=False,
                               label_every=6, rot=0)
    chart_a_after = bar_chart(labels_h, [("records per hour", [after.get(k, 0) for k in keys], GOOD)],
                              title="P018 heart rate after conversion to UTC-5: records per hour",
                              ref=(60, "expected 60", GOOD), ymax=130, show_values=False, label_every=6, rot=0)
    zrows = Q("(select raw_timestamp, minute_ts, local_date, heart_rate_bpm, quality_flags from silver_heart_rate_minute "
              "where participant_id='P018' and is_timestamp_converted order by minute_ts limit 2) union all "
              "(select raw_timestamp, minute_ts, local_date, heart_rate_bpm, quality_flags from silver_heart_rate_minute "
              "where participant_id='P018' and is_timestamp_converted order by minute_ts desc limit 2)")
    z_tbl = table(["raw_timestamp (as in file)", "minute_ts (local UTC-5)", "local_date", "date changed?", "heart_rate_bpm", "quality_flags"],
                  [(r[0], r[1], r[2], "yes" if r[0][:10] != str(r[2]) else "no", r[3], r[4]) for r in zrows], hl={"minute_ts (local UTC-5)"})
    z_cnt = table(["participant_id", "converted records", "records whose local date changes"], Q(
        "select participant_id, count(*), count(*) filter (where cast(substr(raw_timestamp,1,10) as date) <> local_date) "
        "from silver_heart_rate_minute where is_timestamp_converted group by 1 order by 1"))
    z_gold = table(["local_date", "hr_mean_bpm", "hr_valid_minute_count", "hr_quality_flags"], Q(
        "select local_date, hr_mean_bpm, hr_valid_minute_count, hr_quality_flags from gold_clinical_daily "
        "where participant_id='P018' and local_date between '2026-01-24' and '2026-01-26' order by 1"))
    card_a = card(
        "m05", "M-05 - UTC 'Z' timestamps in heart rate (P018, P044)", "High", "Implemented",
        "Timestamp-format scan per file: 2,880 heart-rate records in 2 participants end in <code>Z</code>; every other timestamp "
        "is naive. Each Z block is exactly one day. Read as plain labels, 600 records collide with existing minutes; "
        "shifted by -5 h all 2,880 land in empty slots with 0 collisions.",
        "Interpret naive timestamps as local UTC-5 and convert any Z/offset timestamp to that timeline before the local "
        "date is taken. Keep the raw text, mark it with the informational rule <code>HR_TIMESTAMP_CONVERTED</code>, no reject rows.",
        "A wrong clock shifts values 5 h (distorting time-of-day and sleep overlap) and creates a hole plus duplicates. The "
        "empty-slot fit is the evidence for the offset; the offset itself comes from the data dictionary and is an assumption (A-01).",
        chart_a_before +
        '<p class="small">Hours 00-04 of 01-25 are empty and hours 00-04 of 01-26 hold twice the records: 300 collisions per participant.</p>',
        chart_a_after + z_tbl + z_cnt + '<p class="small">Gold daily row for the converted day (no level shift against its neighbours):</p>' + z_gold)

    # ---- B: duplicates (M-07)
    hrs = list(range(24))
    pb = {r[0]: r[1:] for r in Q(
        "select hour(minute_ts), count(*) filter (where quality_flags like '%STEPS_DUPLICATE_MINUTE_EXACT%'), "
        "count(*) filter (where quality_flags like '%STEPS_DUPLICATE_MINUTE_CONFLICT%'), "
        "count(*) filter (where not is_duplicate_minute) from silver_steps_minute "
        "where participant_id='P019' and local_date='2026-01-10' group by 1")}
    chart_b_before = bar_chart([f"{h:02d}h" for h in hrs], [
        ("exact-duplicate rows", [pb.get(h, (0, 0, 0))[0] for h in hrs], WARN),
        ("conflicting rows", [pb.get(h, (0, 0, 0))[1] for h in hrs], BAD),
        ("single rows", [pb.get(h, (0, 0, 0))[2] for h in hrs], GREY)], stacked=True, rot=0,
        title="P019 steps on 2026-01-10 in silver: rows per hour (a clean hour has 60)", show_values=False,
        ref=(60, "60 expected", GOOD))
    ra = {r[0]: r[1:] for r in Q(
        "select hour(minute_ts), count(distinct date_trunc('minute', minute_ts)) filter (where steps_per_minute is not null), "
        "count(distinct date_trunc('minute', minute_ts)) from silver_steps_minute "
        "where participant_id='P019' and local_date='2026-01-10' group by 1")}
    chart_b_after = bar_chart([f"{h:02d}h" for h in hrs], [
        ("minutes counted in gold (exact pairs once)", [ra.get(h, (0, 0))[0] for h in hrs], GOOD),
        ("minutes withheld (conflict)", [ra.get(h, (0, 0))[1] - ra.get(h, (0, 0))[0] for h in hrs], BAD)],
        stacked=True, rot=0, title="Same day, minutes that reach gold_clinical_daily per hour", show_values=False,
        ref=(60, "60 minutes", GREY))
    days = Q("with s as (select participant_id, local_date, sum(steps_per_minute) naive from silver_steps_minute "
             "where not is_orphan and not is_out_of_window group by 1,2) select g.participant_id, g.local_date, "
             "s.naive, g.total_steps from gold_clinical_daily g join s using (participant_id, local_date) "
             "where g.total_steps is distinct from s.naive order by 1,2")
    chart_b_days = bar_chart([f"{d[0]} {str(d[1])[5:]}" for d in days], [
        ("naive sum over silver rows", [int(d[2] or 0) for d in days], BAD),
        ("gold total_steps", [int(d[3] or 0) for d in days], GOOD)],
        title=f"{len(days)} participant-days where a naive silver sum differs from gold", show_values=False)
    dup_rows = Q("(select 'conflict' k, minute_ts, steps, unit, steps_per_minute, is_duplicate_minute, quality_flags "
                 "from silver_steps_minute where participant_id='P019' and date_trunc('minute', minute_ts) = "
                 "(select min(date_trunc('minute', minute_ts)) from silver_steps_minute where participant_id='P019' "
                 "and quality_flags like '%CONFLICT%') order by minute_ts) union all "
                 "(select 'exact' k, minute_ts, steps, unit, steps_per_minute, is_duplicate_minute, quality_flags "
                 "from silver_steps_minute where participant_id='P019' and date_trunc('minute', minute_ts) = "
                 "(select min(date_trunc('minute', minute_ts)) from silver_steps_minute where participant_id='P019' "
                 "and quality_flags like '%EXACT%') order by minute_ts)")
    dup_tbl = table(["case", "minute_ts", "steps (raw)", "unit", "steps_per_minute", "is_duplicate_minute", "quality_flags"],
                    dup_rows, hl={"steps_per_minute"}, caption="Silver keeps every raw row; normalized value is NULL for conflicts")
    dup_gold = table(["local_date", "total_steps", "steps_valid_minute_count", "steps_present", "steps_quality_flags",
                      "hr_valid_minute_count", "hr_mean_bpm"], Q(
        "select local_date, total_steps, steps_valid_minute_count, steps_present, steps_quality_flags, hr_valid_minute_count, "
        "hr_mean_bpm from gold_clinical_daily where participant_id='P019' and local_date between '2026-01-08' and '2026-01-11' order by 1"),
        hl={"steps_valid_minute_count", "hr_valid_minute_count"}, caption="Gold: P019 around the mis-stamped day")
    dup_sum = table(["source", "conflict records", "exact-duplicate records"], [
        ("steps", *Q("select failed_count from quality_results where rule_id='STEPS_DUPLICATE_MINUTE_CONFLICT'")[0],
         *Q("select failed_count from quality_results where rule_id='STEPS_DUPLICATE_MINUTE_EXACT'")[0]),
        ("heart rate", *Q("select failed_count from quality_results where rule_id='HR_DUPLICATE_MINUTE_CONFLICT'")[0],
         *Q("select failed_count from quality_results where rule_id='HR_DUPLICATE_MINUTE_EXACT'")[0])])
    card_b = card(
        "m07", "M-07 - Duplicate minute slots (P010, P019, P022, P025 and others)", "High", "Implemented",
        "Group records by (participant, minute); compare values. P010, P019, P022 and P025 duplicate the whole of 2026-01-10 "
        "(and miss 2026-01-08) with conflicting values; P005, P034, P040 and P050 have a few duplicates each.",
        "Preserve all raw rows. Exact duplicates count once. Conflicting values at one minute: withhold that measure for the "
        "minute (no first/last pick, no average). Unrelated slots and measures stay usable.",
        "Choosing a value would invent data; averaging unrelated readings hides the conflict. A-04 (a mis-stamped day) was "
        "suspected but the values do not match, so equivalence is not proven.",
        chart_b_before + dup_sum,
        chart_b_after + chart_b_days + dup_tbl + dup_gold,
        note="Caveat: silver is not de-duplicated. Aggregate gold, or de-duplicate by <code>date_trunc('minute', minute_ts)</code>. "
             "Heavy conflict days keep few valid minutes, so always read <code>*_valid_minute_count</code> next to a total.")

    # ---- C: _unit (M-01)
    med = Q("select participant_id, median(steps), median(steps_per_minute), count(*) filter (where unit is not null) "
            "from silver_steps_minute where not is_orphan and not is_out_of_window and steps is not null group by 1 order by 1")
    lab_p = [m[0][1:] for m in med]
    colc = [BAD if m[3] > 0 else GREY for m in med]
    colg = [GOOD if m[3] > 0 else GREY for m in med]
    c_before = bar_chart(lab_p, [("median raw steps per record", [float(m[1]) for m in med], GREY)], bar_colors=colc, rot=-90,
                         title="Median raw 'steps' per record by participant (red = carries _unit)", show_values=False, w=860)
    c_after = bar_chart(lab_p, [("median steps per minute", [float(m[2]) for m in med], GREY)], bar_colors=colg, rot=-90,
                        title="Median normalized steps per minute (green = _unit participants, converted)", show_values=False, w=860)
    unit_rows = Q("select minute_ts, steps, unit, steps_per_minute, activity_level, quality_flags from silver_steps_minute "
                  "where participant_id='P033' and unit is not null and steps >= 60 order by minute_ts limit 4")
    c_tbl = table(["minute_ts", "steps (raw)", "unit", "steps_per_minute", "activity_level", "quality_flags"], unit_rows,
                  hl={"steps_per_minute"}, caption="P033 silver rows: raw value and unit kept, value normalized per minute")
    c_sum = table(["participant", "records with _unit", "median raw", "median per minute"],
                  [(m[0], m[3], float(m[1]), float(m[2])) for m in med if m[3] > 0])
    card_c = card(
        "m01", "M-01 - Undocumented _unit = steps_per_hour (P005, P033, P038)", "High", "Implemented",
        "Schema scan: an extra <code>_unit</code> field in 120,963 step records of 3 participants. Their medians are about 60x "
        "the others (P033 300, P038 420 vs 5). Every such value is a non-negative multiple of 60.",
        "Convert per record: normalized = steps / 60 when the unit is steps_per_hour and the value divisible by 60; otherwise withhold. "
        "Raw value and unit text stay in silver. Decided per record, never per participant. (First withheld, then reversed on 2026-10-13.)",
        "Converted medians (1, 5, 7) are comparable to the 47 other participants (5), so converting beats discarding 3 participants. "
        "The unit meaning is supported by the data but not confirmed by the data provider (A-02).",
        c_before, c_after + c_sum + c_tbl)

    # ---- D: value ranges (M-02 / M-03)
    edges = [(None, 40, "<40"), (40, 60, "40-59"), (60, 80, "60-79"), (80, 100, "80-99"), (100, 120, "100-119"),
             (120, 140, "120-139"), (140, 160, "140-159"), (160, 201, "160-200"), (201, None, ">200")]

    def hist(col):
        conds = []
        for lo, hi, _ in edges:
            c = []
            if lo is not None:
                c.append(f"{col} >= {lo}")
            if hi is not None:
                c.append(f"{col} < {hi}")
            conds.append(f"count(*) filter (where {' and '.join(c)})")
        return Q(f"select {', '.join(conds)} from silver_heart_rate_minute where {col} is not null")[0]

    h_raw, h_norm = hist("heart_rate_bpm"), hist("heart_rate_bpm_normalized")
    chart_d = bar_chart([x[2] for x in edges], [("raw heart_rate_bpm", list(h_raw), GREY),
                                                 ("normalized (used in aggregates)", list(h_norm), GOOD)],
                        log=True, rot=0, title="Heart-rate values by band: raw vs normalized (log scale)", w=780)
    d_before = bar_chart([x[2] for x in edges], [("raw heart_rate_bpm", list(h_raw), BAD)], log=True, rot=0,
                         bar_colors=[BAD if i in (0, 8) else GREY for i in range(9)],
                         title="Raw heart-rate values by band (log scale): impossible values at both ends")
    d_after = bar_chart([x[2] for x in edges], [("normalized", list(h_norm), GOOD)], log=True, rot=0,
                        title="Normalized heart-rate values by band (log scale): <40 and >200 are empty", ymax=max(h_raw))
    hr_bad = table(["participant_id", "minute_ts", "heart_rate_bpm (raw)", "heart_rate_bpm_normalized", "is_out_of_range", "quality_flags"],
                   Q("select participant_id, minute_ts, heart_rate_bpm, heart_rate_bpm_normalized, is_out_of_range, quality_flags "
                     "from silver_heart_rate_minute where is_out_of_range order by 1,2"), hl={"heart_rate_bpm_normalized"})
    hr_inv = table(["min of gold hr_min_bpm", "max of gold hr_max_bpm", "expected bounds"], [
        (*Q("select min(hr_min_bpm), max(hr_max_bpm) from gold_clinical_daily")[0], "40 - 200")])
    lv = Q("select coalesce(activity_level, 'withheld (NULL)'), count(*) from silver_steps_minute group by 1 order by 2 desc")
    d_steps = bar_chart([x[0] for x in lv], [("records", [x[1] for x in lv], BLUE)], log=True, rot=0,
                        title="Steps records by activity level (log scale): bursts are kept, only invalid values withheld",
                        bar_colors=[GOOD if x[0] == "burst" else (BAD if x[0].startswith("withheld") else BLUE) for x in lv])
    st_neg = table(["participant_id", "minute_ts", "steps (raw)", "steps_per_minute", "quality_flags"], Q(
        "select participant_id, minute_ts, steps, steps_per_minute, quality_flags from silver_steps_minute where steps < 0 order by 1,2"),
        hl={"steps_per_minute"})
    card_d = card(
        "m02", "M-02 / M-03 - Impossible heart rates and extreme steps (P029, P037)", "High", "Implemented",
        "Range checks against the dictionary: 4 heart-rate values outside 40-200 (-10 and 999), 2 negative step counts, and "
        "95,902 step records above 30 per minute (up to 1,000,000).",
        "Heart rate outside 40-200: keep raw, withhold the normalized value, exclude from aggregates. Negative steps: withhold. "
        "Steps above 30/min: not rejected, classified as the <code>burst</code> level and kept in totals; no positive upper cutoff is approved.",
        "A 999 bpm reading would corrupt a day's min/max/mean, while high step bursts can be genuine, so the rule removes only what is physically impossible. "
        "Only the affected field is lost; the same row's other fields survive.",
        d_before + hr_bad + st_neg,
        d_after + hr_inv + d_steps)

    # ---- E: stuck sensor (M-04)
    ser = Q("select minute_ts, heart_rate_bpm, heart_rate_bpm_normalized from silver_heart_rate_minute where participant_id='P003' "
            "and minute_ts between '2026-01-09 03:30:00' and '2026-01-09 08:30:00' order by 1")
    t0 = ser[0][0]
    pts_raw = [((r[0] - t0).total_seconds() / 60, r[1]) for r in ser]
    pts_norm = [((r[0] - t0).total_seconds() / 60, r[2]) for r in ser]
    xt = [(m, (t0 + timedelta(minutes=m)).strftime("%H:%M")) for m in range(0, 301, 60)]
    yvals = [p[1] for p in pts_raw if p[1] is not None]
    band = (74, 254, "stuck run: 60 bpm x 180 min", BAD)
    e_before = line_chart([("raw heart_rate_bpm", pts_raw, GREY)], title="P003 on 2026-01-09: raw heart rate (03:30-08:30)",
                          xmin=0, xmax=300, xticks=xt, bands=[band], ymin=min(yvals) - 5, ymax=max(yvals) + 5, ylabel="bpm")
    e_after = line_chart([("heart_rate_bpm_normalized", pts_norm, GOOD)], title="Same window after the stuck-sensor rule (gap = withheld)",
                         xmin=0, xmax=300, xticks=xt, bands=[band], ymin=min(yvals) - 5, ymax=max(yvals) + 5, ylabel="bpm")
    st_sum = table(["participant_id", "flagged minutes", "first minute", "last minute", "raw bpm kept", "normalized non-null"], Q(
        "select participant_id, count(*), min(minute_ts), max(minute_ts), min(heart_rate_bpm), count(heart_rate_bpm_normalized) "
        "from silver_heart_rate_minute where is_stuck_sensor group by 1 order by 1"), hl={"normalized non-null"})
    st_gold = table(["participant_id", "local_date", "mean if stuck minutes kept", "gold hr_mean_bpm", "raw minutes", "gold valid minutes", "gold flags"], Q(
        "select s.participant_id, s.local_date, avg(s.heart_rate_bpm), g.hr_mean_bpm, count(s.heart_rate_bpm), g.hr_valid_minute_count, g.hr_quality_flags "
        "from silver_heart_rate_minute s join gold_clinical_daily g using (participant_id, local_date) "
        "where (s.participant_id='P003' and s.local_date='2026-01-09') or (s.participant_id='P042' and s.local_date='2026-01-24') "
        "group by s.participant_id, s.local_date, g.hr_mean_bpm, g.hr_valid_minute_count, g.hr_quality_flags order by 1,2"),
        hl={"gold valid minutes", "gold hr_mean_bpm"})
    card_e = card(
        "m04", "M-04 - Stuck heart-rate sensor (P003, P042)", "Medium", "Implemented",
        "Run-length scan of identical consecutive in-range values: exactly two runs of 180 minutes (P003 from 2026-01-09 04:44, P042 from 2026-01-24 09:16); "
        "no other run reaches even 10 minutes, so any threshold between 10 and 180 gives the same result.",
        "Flag every minute of a run of at least 60 identical consecutive minute slots, from its first minute; keep the raw reading, "
        "withhold it from aggregates. A missing minute, out-of-range value or conflict breaks a run.",
        "In-range values pass the range check, so only a temporal rule can catch a flat line. The 60-minute threshold is provisional "
        "(no validated device behaviour) and is not sensitive on this dataset.",
        e_before, e_after + st_sum + st_gold)

    # ---- F: gaps (M-08 / X-01)
    graw = {(r[0], r[1]): r[2] for r in Q(
        "select participant_id, local_date, count(*) from silver_steps_minute where not is_orphan and not is_out_of_window "
        "and local_date is not null group by 1,2")}
    ggold = {(r[0], r[1]): (r[2] or 0, r[3]) for r in Q(
        "select participant_id, local_date, steps_valid_minute_count, steps_quality_flags from gold_clinical_daily")}
    pids = sorted({k[0] for k in ggold})
    dts = sorted({k[1] for k in ggold})
    pid_l, dt_l = pids, [d.strftime("%m-%d") for d in dts]
    dmap = dict(zip(dt_l, dts))

    def c_raw(p, d):
        n = graw.get((p, dmap[d]), 0)
        return "#c0392b" if n == 0 else "#f5b041" if n < 1440 else "#d5f5e3" if n == 1440 else "#8e44ad"

    def c_val(p, d):
        v = ggold.get((p, dmap[d]), (0, None))[0]
        if v >= 1440:
            return "#d5f5e3"
        return "#c0392b" if v == 0 else lerp_color("#d35400", "#fad7a0", v / 1440)

    f_before = heatmap(pid_l, dt_l, c_raw, lambda p, d: f"{p} {dmap[d]}: {graw.get((p, dmap[d]), 0)} raw step rows (1,440 expected)",
                       title="Raw step records per participant-day in silver (as found)",
                       legend=[("1,440 expected", "#d5f5e3"), ("partial", "#f5b041"), ("none", "#c0392b"), ("more than 1,440 (duplicates)", "#8e44ad")])
    f_after = heatmap(pid_l, dt_l, c_val, lambda p, d: f"{p} {dmap[d]}: {ggold.get((p, dmap[d]), (0, None))[0]} valid minutes in gold",
                      title="Valid step minutes per participant-day in gold (gaps stay visible, nothing imputed)",
                      legend=[("1,440 valid", "#d5f5e3"), ("partial (darker orange = fewer valid minutes)", "#e67e22"), ("0 valid", "#c0392b")])
    odd = sorted(k for k in set(graw) | set(ggold) if graw.get(k, 0) != 1440 or ggold.get(k, (0, None))[0] != 1440)
    f_tbl = table(["participant_id", "local_date", "raw silver rows", "gold steps_valid_minute_count", "gold steps_quality_flags"],
                  [(k[0], k[1], graw.get(k, 0), ggold.get(k, (0, None))[0], ggold.get(k, (0, None))[1]) for k in odd[:40]],
                  hl={"gold steps_valid_minute_count"}, caption=f"Participant-days that are not a clean 1,440 ({len(odd)} in total, first 40 shown)")
    card_f = card(
        "m08", "M-08 / X-01 - Gaps on the minute grid", "High", "Implemented",
        "Expected 1,440 slots per participant-day minus observed slots: steps miss 6,840 slots in 7 participants (whole day 2026-01-08 for P010, P019, "
        "P022, P025; 360 minutes for P020, P028, P041); heart rate misses 9,720 before and 6,840 after Z conversion.",
        "No imputation. Aggregate observed eligible readings and expose <code>*_valid_minute_count</code> and <code>*_present</code>; "
        "missing steps are not zero and missing heart rate is not a measured value.",
        "Filling gaps would fabricate clinical data, and the data cannot distinguish 'not worn' from 'zero'. Coverage columns let each consumer choose a threshold. "
        "A daily coverage threshold is still open; the weekly (4 days) and cohort (14 days) thresholds never trigger on real data.",
        f_before, f_after + f_tbl)

    # ---- G: sleep (S-01..S-07)
    sess = Q("with st as (select participant_id, local_date, sum(duration_min) filter (where stage <> 'awake') asleep, "
             "sum(duration_min) total from silver_sleep_stages group by 1,2) select s.supplied_efficiency_pct, "
             "st.asleep*100.0/(date_diff('second', s.sleep_onset, s.sleep_end)/60.0), st.asleep*100.0/st.total "
             "from silver_sleep_sessions s join st using (participant_id, local_date) "
             "where not s.is_duplicate_key and not s.is_orphan and not s.is_out_of_window")
    bins = [(-1e9, 70, "<70"), (70, 80, "70-80"), (80, 90, "80-90"), (90, 95, "90-95"), (95, 99.5, "95-99.5"),
            (99.5, 100.5, "~100"), (100.5, 1e9, ">100.5")]

    def binned(i):
        return [sum(1 for r in sess if lo <= r[i] < hi) for lo, hi, _ in bins]

    bl = [b[2] for b in bins]
    g_before = bar_chart(bl, [("supplied efficiency_pct", binned(0), GREY),
                              ("span-based: asleep / (sleep_end - sleep_onset)", binned(1), BAD)], rot=0,
                         title="Sleep efficiency per session (1,400): supplied field and the span-based formula both pile up at ~100")
    g_after = bar_chart(bl, [("stage-based (decided): non-awake / all stage minutes", binned(2), GOOD)], rot=0,
                        title="Sleep efficiency per session after the decided stage-based formula")
    ex = Q("select s.participant_id, s.local_date, s.sleep_onset, s.sleep_end, s.supplied_efficiency_pct from silver_sleep_sessions s "
           "where s.participant_id='P001' and s.local_date='2026-01-08'")[0]
    stg = Q("select stage_index, stage, start_time, end_time, duration_min, derived_duration_min from silver_sleep_stages "
            "where participant_id='P001' and local_date='2026-01-08' order by stage_index")
    onset = ex[2]
    tl_stages = [(s[1], (s[2] - onset).total_seconds() / 60, (s[3] - onset).total_seconds() / 60) for s in stg]
    tl = stage_timeline(tl_stages, onset, (ex[3] - onset).total_seconds() / 60, title="P001 on 2026-01-08: stages, sleep_end and the awake tail")
    span = (ex[3] - onset).total_seconds() / 60
    allm = sum(s[4] for s in stg)
    asleep = sum(s[4] for s in stg if s[1] != "awake")
    g_metrics = table(["measure", "value"], [
        ("sleep_onset to sleep_end (span, min)", round(span, 1)), ("sum of all stage minutes", allm),
        ("sum of non-awake stage minutes", asleep), ("supplied efficiency_pct", ex[4]),
        ("span-based efficiency", round(asleep / span * 100, 2)), ("stage-based efficiency (decided)", round(asleep / allm * 100, 2))],
        hl={"value"})
    g_stg = table(["stage_index", "stage", "start_time", "end_time", "duration_min", "derived_duration_min"], stg)
    s01 = table(["participant_id", "local_date", "stage_index", "stage", "start_time", "end_time", "duration_min (used)", "derived_duration_min (kept, flagged)",
                 "gold total_sleep_min", "total if derived were used"], Q(
        "select t.participant_id, t.local_date, t.stage_index, t.stage, t.start_time, t.end_time, t.duration_min, t.derived_duration_min, "
        "g.total_sleep_min, (select sum(derived_duration_min) from silver_sleep_stages x where x.participant_id=t.participant_id "
        "and x.local_date=t.local_date and x.stage <> 'awake') from silver_sleep_stages t join gold_clinical_daily g using (participant_id, local_date) "
        "where t.quality_flags like '%SLEEP_STAGE_DURATION_MISMATCH%' order by 1,2,3"), hl={"duration_min (used)", "gold total_sleep_min"})
    card_g = card(
        "s-sleep", "S-01..S-07 - sleep durations, efficiency and the meaning of sleep_end", "Medium", "Implemented",
        "Compare stage sums, stage timestamps and the session span: the last stage is <code>awake</code> and starts at sleep_end in 1,397 of 1,400 sessions, "
        "so sleep_end is the wake-up, not the end of the last stage. The sum of all stages differs from the span in all 1,400 sessions; the non-awake sum in 7. "
        "The supplied efficiency is exactly 100 in 1,132 sessions and matches the stage-based value within 1 point in only 7. 4 stages disagree with their timestamps.",
        "total_sleep_min = non-awake duration_min; efficiency = non-awake / all stage minutes (denominator is the stage timeline, not sleep_end/onset); "
        "duration_min is used and mismatches flagged with both values kept; the awake tail is not sleep; day = wake <code>date</code>.",
        "A span denominator gives ~100% for almost every night (meaningless), while the stage-based value spans 74.6-94.4 (median 87.4). "
        "Wake-date assignment is 1:1 with the survey; onset-date assignment would put 43 sessions outside the window. 'sleep_end is the start of the awake tail' is an inference (A-03).",
        g_before + tl + g_stg + s01,
        g_after + g_metrics)

    # ---- H: tabular (T-01, T-02, T-03)
    sv = Q("select participant_id, count(*) from silver_wellness_survey group by 1 order by 1")
    gv = dict(Q("select participant_id, count(*) filter (where survey_present) from gold_clinical_daily group by 1"))
    h_before = bar_chart([s[0][1:] for s in sv], [("survey rows in silver", [s[1] for s in sv], GREY)], rot=-90, w=860,
                         bar_colors=[BAD if s[0] == "P999" else GREY for s in sv], show_values=False,
                         title="Survey rows per participant id in the source (P999 is not enrolled)")
    h_after = bar_chart([s[0][1:] for s in sv], [("survey days in gold", [gv.get(s[0], 0) for s in sv], GOOD)], rot=-90, w=860,
                        bar_colors=[GOOD] * len(sv), show_values=False, ymax=28,
                        title="Survey days in gold_clinical_daily per participant id (P999 absent)")
    p999 = table(["participant_id", "local_date", "fatigue_score", "is_orphan", "quality_flags"], Q(
        "select participant_id, local_date, fatigue_score, is_orphan, quality_flags from silver_wellness_survey "
        "where participant_id='P999' order by local_date limit 3"), hl={"is_orphan"}, caption="Silver keeps the orphan rows (first 3 of 14)")
    p999g = table(["gold rows for P999", "gold rows total"], [(Q("select count(*) from gold_clinical_daily where participant_id='P999'")[0][0], n_gold)])
    card_h1 = card(
        "t01", "T-01 - Orphan survey participant P999", "Medium", "Implemented",
        "Key check against participants.csv: 14 daily survey rows belong to P999, who is not enrolled (1,414 survey rows vs 1,400 expected).",
        "Keep the rows in silver with an orphan flag; exclude them only from outputs that need an enrolled-participant join.",
        "Dropping them would lose evidence; joining them would put an unenrolled person in clinical output.",
        h_before, h_after + p999 + p999g)
    dev = list(csv.DictReader((data_dir / "health_summaries" / "device_metadata.csv").open(encoding="utf-8-sig")))
    pat = re.compile(r"fw([A-Za-z0-9.]+)", re.I)
    blank = [(d["participant_id"], d["device_id"], d["firmware_version"], d["device_label"]) for d in dev if not d["firmware_version"].strip()]
    cnt = Counter(d["firmware_version"].strip() or "(blank)" for d in dev)
    cnt_after = Counter()
    for d in dev:
        fv = d["firmware_version"].strip()
        if fv:
            cnt_after[fv] += 1
        else:
            m = pat.search(d["device_label"])
            cnt_after[f"{m.group(1)} (from label, flagged)" if m else "(unresolved)"] += 1
    fl = sorted(cnt)
    fl2 = sorted(cnt_after)
    t2_before = bar_chart(fl, [("devices", [cnt[k] for k in fl], GREY)], rot=0, bar_colors=[BAD if k == "(blank)" else GREY for k in fl],
                          title="firmware_version in device_metadata.csv (50 devices)")
    t2_after = bar_chart(fl2, [("devices", [cnt_after[k] for k in fl2], GREY)], rot=0,
                         bar_colors=[WARN if "label" in k else GOOD for k in fl2], title="Decided outcome: label fallback with provenance")
    t2_tbl_b = table(["participant_id", "device_id", "firmware_version", "device_label"], blank[:6], hl={"firmware_version"},
                     caption=f"Blank firmware_version ({len(blank)} of {len(dev)} devices; first 6 shown)")
    t2_tbl_a = table(["participant_id", "firmware_raw", "firmware_normalized", "source", "review flag"], [
        (b[0], "(blank)", (pat.search(b[3]).group(1) if pat.search(b[3]) else None), "device_label", "unexpected 2.1.8: review") for b in blank[:6]],
        caption="What the decision produces (preview computed by this report)")
    card_h2 = card(
        "t02", "T-02 / T-03 - Blank firmware_version and the suspicious 2.1.8", "Medium", "Decided - NOT yet implemented",
        "Blank check on device_metadata.csv: 15 of 50 rows have no firmware_version, but device_label carries a parseable <code>fw&lt;version&gt;</code> in all 15 "
        "with 0 conflicts where both exist. All 15 parse to 2.1.8, a value in no populated cell (populated: 2.2.0 x23, 2.2.1 x12).",
        "Parse device_label when the structured field is blank, keep the raw blank, record fallback provenance, flag 2.1.8 for review, never discard the device row.",
        "The label is reliable where both exist, but the blanks all being one older version suggests missing-not-at-random, so the value is trusted only with provenance and a flag.",
        t2_before + t2_tbl_b,
        t2_after + t2_tbl_a,
        note="Honest status: device_metadata only reaches raw_manifest; no firmware column or parsing exists in the pipeline, and gold_participant_devices is not produced. "
             "The right-hand view is a preview, not pipeline output.")

    # ---- decision register
    reg = [
        ("Quality policy", "Fail / quarantine / flag / exclude whole days were all possible; 108 of 1,400 days carry any flag.", "Preserve raw rows in bronze; normalize, flag or withhold only the affected field; persist outcomes.", "One bad minute must not discard a day's other valid measures.", "Implemented"),
        ("Timezone (G1)", "Naive timestamps vs 2,880 Z records.", "Naive = local UTC-5; convert Z/offsets to it before joins and local date.", "Z records fit empty slots only after a -5 h shift.", "Implemented"),
        ("Day assignment (G1)", "Onset date, wake date, majority date for sleep; naive vs converted for minutes.", "Sleep: wake `date`; minutes: normalized local date.", "1:1 with the survey; onset would put 43 sessions outside the window.", "Implemented"),
        ("Daily grain (G1)", "Participant-day vs with completeness columns.", "gold_clinical_daily keyed (participant_id, local_date) with quality/valid-count fields.", "Survey, sleep and minute data all aggregate to this key.", "Implemented"),
        ("Walking skeleton (G1)", "Survey CSV vs nested sleep JSON.", "Start with wellness_survey.csv; keep P999 flagged in silver.", "Smallest source with the target grain.", "Implemented"),
        ("_unit steps_per_hour (M-01)", "Withhold vs convert.", "Convert per record (steps / 60); keep raw + unit.", "Medians match other participants after conversion.", "Implemented"),
        ("Step values (M-02)", "Cutoff vs keep bursts.", "Negative withheld; >30/min is the burst level, kept.", "No evidence for a positive cutoff.", "Implemented"),
        ("Heart-rate range (M-03)", "Reject row vs withhold field.", "Raw kept; normalized NULL outside 40-200.", "Protect aggregates, keep evidence.", "Implemented"),
        ("Stuck sensor (M-04)", "Threshold choices; flag vs drop.", ">=60 identical consecutive minutes: flag whole run, withhold from aggregates.", "Range check cannot see a flat line; result insensitive between 10 and 180 min.", "Implemented (threshold provisional)"),
        ("Z timestamps (M-05)", "Treat as naive, UTC everywhere, or reject.", "Convert to UTC-5; keep raw text; informational rule.", "0 collisions after conversion.", "Implemented"),
        ("Duplicates (M-07)", "First/last/average vs withhold.", "Exact once; conflicts withheld for that minute.", "No invented values; no inflation.", "Implemented"),
        ("Missing minutes (M-08)", "Impute vs expose coverage.", "No imputation; valid counts and present flags.", "Missing is not zero.", "Implemented (daily threshold open)"),
        ("Non-zero seconds (M-09)", "Floor silently vs preserve.", "Preserve and flag; apply the duplicate rule on collision.", "Flooring can merge distinct readings.", "Implemented"),
        ("Firmware fallback (T-02/T-03)", "Leave blank vs parse label.", "Parse label, keep provenance, flag 2.1.8.", "Recover metadata without hiding its origin.", "Decided, not implemented"),
        ("Sleep durations (S-01)", "Recompute from timestamps vs use field.", "Use duration_min; flag mismatches; keep both.", "Documented metric; no silent rewrite.", "Implemented"),
        ("Sleep formulas", "Span vs stage denominator.", "total = non-awake minutes; efficiency = non-awake / all stage minutes.", "Span-based is ~100% for 1,393 of 1,400 sessions.", "Implemented"),
        ("sleep_end (S-03/S-07)", "End of sleep vs end of last stage.", "Wake-up and start of the awake tail; tail not counted as sleep.", "True for 1,397 of 1,400 sessions (A-03).", "Implemented"),
        ("Week boundary (G2)", "Study-relative 7-day blocks vs ISO / Sunday weeks.", "Four blocks anchored on 2026-01-08 with week_id and period dates.", "Four equal weeks, no partial weeks.", "Implemented (INC-007)"),
        ("Weekly schema (G2)", "Wide vs long vs reuse daily.", "Distinct wide table keyed (participant_id, week_id); NULL below 4 valid days.", "Reconciles with daily; coverage always explicit.", "Implemented (threshold provisional)"),
        ("Chronotype cohort (G3)", "Drop incomplete participants vs keep all.", "All 50 kept; per-metric valid days; NULL below 14 valid days.", "Dropping participants biases cohorts.", "Implemented (threshold provisional)"),
        ("Freshness (G4)", "Full refresh, upsert, watermark.", "Incremental by affected participant-day; SHA-256 manifest to detect change.", "No arrival timestamps in the data.", "Implemented (INC-006)"),
        ("Local storage (G4)", "DuckDB vs Parquet/Iceberg.", "Native DuckDB tables; Iceberg only proposed for cloud.", "Transactions and MERGE in one file.", "Implemented"),
    ]
    reg_tbl = table(["Decision", "Options / evidence", "Decision taken", "Why", "Status"], reg).replace('<table>', '<table class="dec">')
    open_items = ("<ul><li>Positive upper cutoff for steps.</li><li>Confirmation of <code>_unit=steps_per_hour</code> by the data provider (conversion applied meanwhile).</li>"
                  "<li>Whether firmware 2.1.8 is valid; and implementing the firmware fallback.</li>"
                  "<li>Minimum daily coverage threshold; weekly (4 days) and cohort (14 days) thresholds never trigger on real data.</li>"
                  "<li>60-minute stuck-sensor threshold needs validated device behaviour.</li>"
                  "<li>Late arrival vs correction: no arrival timestamps exist, so incremental mode detects changed files only.</li>"
                  "<li><code>gold_participant_devices</code> is a client-brief item that is not produced.</li></ul>")

    # ---- architecture
    mapping = table(["Local component (implemented)", "Cloud equivalent (proposed, not implemented)"], [
        ("raw exports under /data", "S3 raw prefix"), ("bronze (raw_manifest)", "S3 bronze prefix"), ("silver tables", "S3 silver prefix"),
        ("gold tables in one DuckDB file", "Iceberg tables on S3"), ("container run through compose", "AWS Batch task"),
        ("catalog + SQL through the query runner", "Glue Catalog and Athena"), ("scheduling by CLI and compose", "EventBridge and Step Functions")])
    arch_dec = [
        ("DuckDB, not Spark", "Implemented", "Runs in-process from one file; fits the measured size (19.4 MB database, ~71 s full run, 4.9 GiB peak memory).",
         "Spark: a cluster and its operations add cost without a need at this size.", "Memory-bound and single-node; a full run holds the transformed tables in memory."),
        ("Local DuckDB storage; Iceberg only as a proposal", "Implemented / Proposed", "Native DuckDB tables with transactions and MERGE. Iceberg proposed for the cloud gold layer: snapshots, atomic commits and schema evolution mirror what the idempotent merge gives locally.",
         "Writing Iceberg/Parquet now: adds a catalog and cloud services outside the verified run. Parquet on S3 is simpler but has no atomic table commits.", "Iceberg was not built, benchmarked or tested."),
        ("CLI + compose now; Step Functions + EventBridge proposed", "Implemented / Proposed", "A single container command orchestrates runs locally.",
         "Building cloud orchestration now: it cannot be tested locally.", "ECS scheduled tasks are an equally workable alternative to AWS Batch for a short batch job."),
        ("Medallion layers with one shared silver", "Implemented", "bronze raw_manifest -> typed, flagged silver -> three gold tables from the same silver.",
         "Per-output parsing: daily, weekly and cohort numbers would diverge on the same raw data.", "Silver keeps raw rows, so consumers must use gold or de-duplicate."),
        ("Quality failure behaviour", "Implemented", "Field-level defects are flagged, withheld and counted in quality_results; only unreadable or contract-breaking inputs fail the run.",
         "Failing on any bad record: real exports always contain a few.", "Thresholds (stuck run, coverage) are provisional."),
        ("Reproducibility and idempotency", "Implemented", "Content-derived run_id, a compare command, pinned base image and uv version, locked dependencies, non-root container with read-only data mount.",
         "Unpinned images and timestamp-based run ids: the same input would not give the same output.", "Verified locally; not run on any cloud."),
        ("Incremental mode by file hash", "Implemented", "SHA-256 manifest compared with the stored one; only changed files are re-parsed; gold recomputed for affected participant-days; falls back to a full rebuild when unsure.",
         "Watermarks: files carry no arrival timestamps.", "Detects changed files, not late arrival. A one-record correction takes ~3 s versus ~75 s for a full rebuild."),
        ("Weekly and cohort as separate gold tables", "Implemented", "Distinct participant-week and participant tables derived with the daily rules so overlapping quantities reconcile (0 mismatches).",
         "Reusing the daily table: wrong grain for biostatistics.", "Coverage thresholds are provisional."),
        ("Read-only query runner", "Implemented", "Opens the database read-only, runs queries/*.sql, never imports the pipeline or writes.",
         "Querying through the pipeline: risk of mutating outputs.", "Only gold tables are read by the shipped queries."),
    ]
    dec_rows = [(a, b, c, d, f) for a, b, c, d, f in arch_dec]
    arch_dec_tbl = table(["Decision", "Status", "What and why", "Alternative rejected", "Limits / trade-off"], dec_rows).replace('<table>', '<table class="dec">')
    scaling = ("<p class='small'>Measured full run on the real data: 70-71 s wall clock including container start, 4.9 GiB peak container memory (may include page cache), "
               "19.4 MB database; no-op incremental about 1.1 s; one-record correction about 3.1 s. No thresholds or growth estimates are claimed. "
               "Memory is the first limit; the shared silver layer and per-participant gold outputs are the natural places to partition.</p>")

    kp = [(f"{len(pids)}", "participants"), (f"{n_gold:,}", "gold_clinical_daily rows"), (f"{n_week}", "gold_biostat_weekly rows"),
          (f"{n_coh}", "gold_chronotype_cohort rows"), (f"{n_hr:,}", "silver heart-rate records"), (f"{n_st:,}", "silver step records"),
          (run_id, "run_id")]
    kpis = "".join(f'<div class="kpi"><b>{e(a)}</b><span>{e(b)}</span></div>' for a, b in kp)
    nav = [("overview", "Overview"), ("m05", "Z timestamps"), ("m07", "Duplicates"), ("m01", "_unit"), ("m02", "Ranges"), ("m04", "Stuck sensor"),
           ("m08", "Gaps"), ("s-sleep", "Sleep"), ("t01", "Orphan"), ("t02", "Firmware"), ("register", "Decision register"), ("arch", "Architecture")]
    nav_html = "".join(f'<a href="#{a}">{e(b)}</a>' for a, b in nav)

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Wearable pipeline - profiling problems, decisions and architecture</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head><body>
<header><h1>Wearable health pipeline - profiling problems, decisions and architecture</h1>
<p>Meridian Health Analytics proposal. Every chart and table is computed from <code>output/wearable.duckdb</code> (run {e(run_id)}), opened read-only, plus the raw files for the "as found" views.</p>
<p>Source of the decisions: <code>reports/INC-001/profiling-findings.md</code> (Section 7) and the README. Generated by <code>scripts/build_profiling_report.py</code>. Contains participant-level excerpts: do not commit.</p></header>
<nav>{nav_html}</nav><main>
<h2 id="overview">Overview</h2><div class="kpis">{kpis}</div>
<p>Each finding below shows the problem as it exists in the source (red) and the same view after the decided handling (green), with the table that backs it.
Handling vocabulary: <b>withhold_field</b> (raw kept, normalized value NULL), <b>flag</b>, <b>convert</b>, <b>exclude_from_output</b>.</p>
{chart_rules}<details><summary>Rules that fired (table)</summary>{rules_tbl}</details>
<h2>Findings that received human decisions</h2>
{card_a}{card_b}{card_c}{card_d}{card_e}{card_f}{card_g}{card_h1}{card_h2}
<h2 id="register">Decision register</h2>{reg_tbl}<h3>Still open</h3>{open_items}
<h2 id="arch">Architecture</h2>
<h3>Local architecture (implemented)</h3><div class="arch">{local_architecture()}</div>
<h3>Cloud architecture (proposed, not implemented)</h3><div class="arch">{cloud_architecture()}</div>
<h3>Local to cloud mapping</h3>{mapping}
<h3>Architecture decisions explained</h3>{arch_dec_tbl}
<h3>Scaling</h3>{scaling}
</main><footer>Generated from the pipeline output of run {e(run_id)}. Cloud components are a design proposal only.</footer></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", type=Path, default=ROOT / "output" / "wearable.duckdb")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "data-profiling-decisions-report.html")
    a = ap.parse_args()
    build(a.database, a.data_dir, a.out)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
