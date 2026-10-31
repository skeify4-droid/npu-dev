"""Minimal dependency-free SVG line charts for the Phase 1 reports (static; light and dark via prefers-color-scheme).

Rules followed (dataviz method): categorical hues in fixed order (max 6 series per chart), one y-axis, 2 px lines,
8 px markers, recessive grid, legend always present for >= 2 series, direct end labels when <= 4 series,
text in text colors (never the series color). Every chart is paired with a table in the report.
"""
import math

LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]
W, H = 940, 440
ML, MR, MT, MB = 70, 300, 48, 56


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ticks(lo, hi, log):
    if log:
        a, b = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
        out = []
        for e in range(a, b + 1):
            for m in (1, 2, 5):
                v = m * 10 ** e
                if lo * 0.999 <= v <= hi * 1.001:
                    out.append(v)
        return out
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / 5)) if span > 0 else 1
    for m in (1, 2, 5, 10):
        if span / (step * m) <= 6:
            step *= m
            break
    v = math.ceil(lo / step) * step
    out = []
    while v <= hi + 1e-12:
        out.append(round(v, 10))
        v += step
    return out


def _fmt(v):
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:g}M"
    if a >= 1e3:
        return f"{v / 1e3:g}k"
    return f"{v:g}"


def line_chart(path, series, title, xlabel, ylabel, xlog=False, ylog=False, vlines=(), hlines=(), xlim=None, ylim=None):
    """series: [(name, [(x, y), ...]), ...] (max 6). vlines/hlines: [(value, label)]."""
    assert 1 <= len(series) <= 6, "fold or facet past six series"
    pts = [(x, y) for _, s in series for x, y in s if y is not None and (not ylog or y > 0) and (not xlog or x > 0)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    xs += [v for v, _ in vlines]
    ys += [v for v, _ in hlines]
    if xlim:
        x0, x1 = xlim
    elif xlog:
        x0, x1 = min(xs) / 1.15, max(xs) * 1.15
    else:
        pad = (max(xs) - min(xs)) * 0.03 or 1
        x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = ylim or ((min(ys) / 1.3, max(ys) * 1.3) if ylog else (0, max(ys) * 1.08))
    tx = lambda v: (math.log10(v) - math.log10(x0)) / (math.log10(x1) - math.log10(x0)) if xlog else (v - x0) / (x1 - x0)
    ty = lambda v: (math.log10(v) - math.log10(y0)) / (math.log10(y1) - math.log10(y0)) if ylog else (v - y0) / (y1 - y0)
    pw, ph = W - ML - MR, H - MT - MB
    X = lambda v: ML + tx(v) * pw
    Y = lambda v: MT + (1 - ty(v)) * ph
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="12">',
         "<style>", ":root{--bg:#fcfcfb;--t1:#0b0b0b;--t2:#52514e;--grid:#e4e3df;--axis:#a9a8a3;"
         + "".join(f"--s{i}:{c};" for i, c in enumerate(LIGHT)) + "}",
         "@media (prefers-color-scheme: dark){:root{--bg:#1a1a19;--t1:#f0efec;--t2:#c3c2b7;--grid:#33332f;--axis:#6b6a64;"
         + "".join(f"--s{i}:{c};" for i, c in enumerate(DARK)) + "}}",
         ".t1{fill:var(--t1)}.t2{fill:var(--t2)}.g{stroke:var(--grid);stroke-width:1}.ax{stroke:var(--axis);stroke-width:1}",
         "</style>", f'<rect width="{W}" height="{H}" fill="var(--bg)"/>',
         f'<text x="{ML}" y="24" class="t1" font-size="15" font-weight="600">{_esc(title)}</text>']
    for v in _ticks(x0, x1, xlog):
        o.append(f'<line class="g" x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{MT}" y2="{MT + ph}"/>')
        o.append(f'<text class="t2" x="{X(v):.1f}" y="{MT + ph + 18}" text-anchor="middle">{_fmt(v)}</text>')
    for v in _ticks(y0, y1, ylog):
        o.append(f'<line class="g" x1="{ML}" x2="{ML + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        o.append(f'<text class="t2" x="{ML - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{_fmt(v)}</text>')
    o.append(f'<line class="ax" x1="{ML}" x2="{ML + pw}" y1="{MT + ph}" y2="{MT + ph}"/>')
    o.append(f'<text class="t2" x="{ML + pw / 2}" y="{H - 14}" text-anchor="middle">{_esc(xlabel)}</text>')
    o.append(f'<text class="t2" transform="translate(18 {MT + ph / 2}) rotate(-90)" text-anchor="middle">{_esc(ylabel)}</text>')
    for v, lab in vlines:
        o.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{MT}" y2="{MT + ph}" stroke="var(--axis)" stroke-dasharray="4 4"/>')
        o.append(f'<text class="t2" x="{X(v) + 4:.1f}" y="{MT + ph - 6}">{_esc(lab)}</text>')
    for v, lab in hlines:
        o.append(f'<line x1="{ML}" x2="{ML + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--axis)" stroke-dasharray="4 4"/>')
        o.append(f'<text class="t2" x="{ML + pw - 4}" y="{Y(v) + 16:.1f}" text-anchor="end">{_esc(lab)}</text>')
    ends = []
    for i, (name, s) in enumerate(series):
        s = [(x, y) for x, y in s if y is not None and (not ylog or y > 0) and x0 <= x <= x1]
        if not s:
            continue
        d = " ".join(f"{'M' if j == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for j, (x, y) in enumerate(s))
        o.append(f'<path d="{d}" fill="none" stroke="var(--s{i})" stroke-width="2" stroke-linejoin="round"/>')
        for x, y in s:
            o.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4" fill="var(--s{i})" stroke="var(--bg)" stroke-width="2"><title>{_esc(name)}: {x:g}, {y:.4g}</title></circle>')
        ends.append((Y(s[-1][1]), X(s[-1][0]), name))
    # legend (always for >= 2 series)
    if len(series) >= 2:
        lx, ly = ML + pw + 16, MT + 6
        for i, (name, _) in enumerate(series):
            o.append(f'<line x1="{lx}" x2="{lx + 18}" y1="{ly + i * 20}" y2="{ly + i * 20}" stroke="var(--s{i})" stroke-width="2"/>')
            o.append(f'<circle cx="{lx + 9}" cy="{ly + i * 20}" r="4" fill="var(--s{i})" stroke="var(--bg)" stroke-width="2"/>')
            o.append(f'<text class="t1" x="{lx + 24}" y="{ly + i * 20 + 4}">{_esc(name)}</text>')
    o.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(o))
