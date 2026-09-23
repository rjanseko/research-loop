"""Build the animated SVG diagrams in docs/assets/ that the README embeds.

The diagrams are generated rather than drawn so their timing and geometry stay exact and a
change to the research loop can be reflected by editing one table here. Animations are CSS
only (keyframes and offset-path), with no script, so GitHub and browsers play them inside an
<img>; they honor prefers-reduced-motion and prefers-color-scheme.

    .venv/bin/python scripts/readme_diagrams.py            # write docs/assets/*.svg
    .venv/bin/python scripts/readme_diagrams.py --freeze 6 # also write *-t6.svg stills for review
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "docs" / "assets"

# Accent per role: (light stroke, light tint, dark stroke, dark tint).
ACCENTS = {
    "planner": ("#4f46e5", "#e0e7ff", "#818cf8", "#312e81"),
    "scout": ("#0d9488", "#ccfbf1", "#2dd4bf", "#134e4a"),
    "join": ("#475569", "#e2e8f0", "#94a3b8", "#334155"),
    "gap": ("#7c3aed", "#ede9fe", "#a78bfa", "#4c1d95"),
    "deep": ("#0284c7", "#e0f2fe", "#38bdf8", "#0c4a6e"),
    "synth": ("#db2777", "#fce7f3", "#f472b6", "#831843"),
    "verify": ("#059669", "#d1fae5", "#34d399", "#064e3b"),
    "done": ("#ca8a04", "#fef3c7", "#facc15", "#713f12"),
    "block": ("#dc2626", "#fee2e2", "#f87171", "#7f1d1d"),
    "data": ("#9333ea", "#f3e8ff", "#c084fc", "#581c87"),
}


def _theme() -> str:
    light = ";".join(f"--c-{k}:{v[0]};--t-{k}:{v[1]}" for k, v in ACCENTS.items())
    dark = ";".join(f"--c-{k}:{v[2]};--t-{k}:{v[3]}" for k, v in ACCENTS.items())
    return (
        f":root{{--bg:#ffffff;--fg:#0f172a;--muted:#64748b;--node:#f8fafc;--edge:#cbd5e1;--frame:#e2e8f0;{light}}}"
        f"@media (prefers-color-scheme: dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;"
        f"--node:#161b22;--edge:#3d444d;--frame:#30363d;{dark}}}}}"
        "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"
        "text{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;fill:var(--fg)}"
        ".mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}"
        ".muted{fill:var(--muted)}"
        ".edge{fill:none;stroke:var(--edge);stroke-width:1.6}"
        ".dash{stroke-dasharray:5 4}"
        ".arrow{fill:var(--edge)}"
        ".node{fill:var(--node);stroke:var(--edge);stroke-width:1.5}"
        ".frame{fill:var(--bg);stroke:var(--frame);stroke-width:1}"
        ".panel{fill:none;stroke:var(--frame);stroke-width:1.2;stroke-dasharray:4 4}"
    )


@dataclass
class Svg:
    """One animated diagram: elements, a keyframe collector, and a loop length in seconds."""

    width: int
    height: int
    period: float
    title: str
    desc: str
    body: list[str] = field(default_factory=list)
    css: list[str] = field(default_factory=list)
    _n: int = 0

    def add(self, *parts: str) -> None:
        self.body.extend(parts)

    def _name(self) -> str:
        self._n += 1
        return f"k{self._n}"

    def _pct(self, t: float) -> str:
        return f"{max(0.0, min(100.0, 100 * t / self.period)):.3f}%"

    def keyframes(self, frames: list[tuple[float, str]]) -> str:
        """Keyframes at absolute times (seconds); returns the class that plays them each period."""
        name = self._name()
        steps = "".join(f"{self._pct(t)}{{{props}}}" for t, props in sorted(frames, key=lambda f: f[0]))
        self.css.append(f"@keyframes {name}{{{steps}}}.{name}{{animation:{name} {self.period}s linear infinite}}")
        return name

    def windows(self, on: str, off: str, spans: list[tuple[float, float]], ramp: float = 0.2) -> str:
        """Class that shows `on` during each (start, end) span and `off` otherwise."""
        frames = [(0.0, off), (self.period, off)]
        for start, end in spans:
            frames += [(max(start - ramp, 0.0), off), (start, on), (end, on), (min(end + ramp, self.period), off)]
        return self.keyframes(frames)

    def render(self, freeze: float | None = None) -> str:
        css = _theme() + "".join(self.css)
        if freeze is not None:
            css += f"*{{animation-delay:-{freeze}s!important;animation-play-state:paused!important}}"
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img" aria-labelledby="t d">'
            f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc)}</desc>'
            f"<style>{css}</style>"
            '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path class="arrow" d="M0 0L10 5L0 10z"/></marker></defs>'
            f'<rect class="frame" x="0.5" y="0.5" width="{self.width - 1}" height="{self.height - 1}" rx="14"/>'
            + "".join(self.body)
            + "</svg>"
        )


def text(x: float, y: float, value: str, *, size: float = 13, weight: int = 400, anchor: str = "middle",
         cls: str = "") -> str:
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" '
            f'class="{cls}">{escape(value)}</text>')


@dataclass
class Box:
    x: float  # center
    y: float
    w: float
    h: float

    @property
    def left(self) -> tuple[float, float]:
        return self.x - self.w / 2, self.y

    @property
    def right(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y

    @property
    def top(self) -> tuple[float, float]:
        return self.x, self.y - self.h / 2

    @property
    def bottom(self) -> tuple[float, float]:
        return self.x, self.y + self.h / 2


def node(svg: Svg, box: Box, title: str, sub: str, accent: str, spans: list[tuple[float, float]]) -> None:
    """A labelled box that lights up in its role's accent during each span."""
    lit = svg.windows(
        f"fill:var(--t-{accent});stroke:var(--c-{accent});stroke-width:2.6",
        "fill:var(--node);stroke:var(--edge);stroke-width:1.5",
        spans,
    ) if spans else ""
    svg.add(
        f'<rect class="node {lit}" x="{box.x - box.w / 2}" y="{box.y - box.h / 2}" width="{box.w}" '
        f'height="{box.h}" rx="9"/>',
        text(box.x, box.y - (3 if sub else -4), title, size=12.5, weight=600),
    )
    if sub:
        svg.add(text(box.x, box.y + 12, sub, size=9.5, cls="mono muted"))


def curve(a: tuple[float, float], b: tuple[float, float], *, vertical: bool = False) -> str:
    (x1, y1), (x2, y2) = a, b
    if vertical:
        dy = (y2 - y1) * 0.5
        return f"M {x1} {y1} C {x1} {y1 + dy}, {x2} {y2 - dy}, {x2} {y2}"
    dx = max(abs(x2 - x1) * 0.5, 12)
    return f"M {x1} {y1} C {x1 + dx} {y1}, {x2 - dx} {y2}, {x2} {y2}"


def edge(svg: Svg, d: str, *, dashed: bool = False) -> None:
    svg.add(f'<path class="edge{" dash" if dashed else ""}" d="{d}" marker-end="url(#a)"/>')


def token(svg: Svg, d: str, accent: str, spans: list[tuple[float, float]], r: float = 5.5) -> None:
    """A dot that travels along path `d` during each span, hidden otherwise."""
    frames: list[tuple[float, str]] = [(0.0, "offset-distance:0%;opacity:0"), (svg.period, "offset-distance:100%;opacity:0")]
    for start, end in spans:
        frames += [
            (max(start - 0.01, 0.0), "offset-distance:0%;opacity:0"),
            (start, "offset-distance:0%;opacity:1"),
            (end, "offset-distance:100%;opacity:1"),
            (min(end + 0.01, svg.period), "offset-distance:100%;opacity:0"),
        ]
    cls = svg.keyframes(frames)
    path = d.replace("'", "")
    svg.add(
        f'<g class="{cls}" style="offset-path:path(\'{path}\');offset-rotate:0deg">'
        f'<circle r="{r * 2}" fill="var(--c-{accent})" opacity="0.18"/>'
        f'<circle r="{r}" fill="var(--c-{accent})"/></g>'
    )


def caption(svg: Svg, x: float, y: float, value: str, spans: list[tuple[float, float]], *,
            size: float = 13, anchor: str = "middle", cls: str = "") -> None:
    """Text visible only during its spans."""
    fade = svg.windows("opacity:1", "opacity:0", spans, ramp=0.25)
    svg.add(f'<g class="{fade}">{text(x, y, value, size=size, anchor=anchor, cls=cls)}</g>')


def chip(svg: Svg, x: float, y: float, label: str, accent: str, appear: float, *, w: float = 34) -> None:
    """A small labelled block that appears at `appear` and stays until the period restarts."""
    show = svg.keyframes([(0.0, "opacity:0"), (max(appear - 0.01, 0.0), "opacity:0"), (appear, "opacity:1"),
                          (svg.period - 0.3, "opacity:1"), (svg.period, "opacity:0")])
    svg.add(
        f'<g class="{show}"><rect x="{x}" y="{y}" width="{w}" height="18" rx="4" '
        f'fill="var(--t-{accent})" stroke="var(--c-{accent})" stroke-width="1.2"/>'
        f'{text(x + w / 2, y + 13, label, size=10, cls="mono")}</g>'
    )


# ---------------------------------------------------------------------------------------------
# 1. One run of research-graph-v1: plan, fan out, join, gap analysis, deep dives, synthesize,
#    verify, and one verification round.
# ---------------------------------------------------------------------------------------------

def research_graph() -> Svg:
    svg = Svg(1100, 470, 23.0, "One run of research-graph-v1",
              "Animated walk through the research graph: the planner fans out to parallel scouts, a join "
              "records their results in plan order, gap analysis fans out to deep dives, the synthesizer "
              "writes a report, the verifier checks it, and one verification round loops back before the run ends.")
    svg.add(text(28, 36, "research-graph-v1 — one run", size=16, weight=700, anchor="start"),
            text(28, 56, "nodes light up as they run; dots are typed values moving between steps",
                 size=11.5, anchor="start", cls="muted"))

    y = 200
    start = Box(40, y, 18, 18)
    plan = Box(118, y, 104, 48)
    scouts = [Box(250, 110, 104, 42), Box(250, y, 104, 42), Box(250, 290, 104, 42)]
    join1 = Box(378, y, 98, 48)
    gap = Box(500, y, 104, 48)
    deeps = [Box(626, 150, 104, 42), Box(626, 250, 104, 42)]
    join2 = Box(750, y, 98, 48)
    synth = Box(868, y, 104, 48)
    verify = Box(990, y, 104, 48)
    end = Box(1072, 118, 18, 18)
    vdeeps = [Box(990, 318, 108, 40), Box(990, 382, 108, 40)]
    vjoin = Box(868, 350, 98, 44)

    # Edges (drawn under nodes).
    e = {}
    e["start"] = curve((start.x + 9, y), plan.left)
    for i, box in enumerate(scouts):
        e[f"p{i}"] = curve(plan.right, box.left)
        e[f"s{i}"] = curve(box.right, join1.left)
    e["j1"] = curve(join1.right, gap.left)
    for i, box in enumerate(deeps):
        e[f"g{i}"] = curve(gap.right, box.left)
        e[f"d{i}"] = curve(box.right, join2.left)
    e["j2"] = curve(join2.right, synth.left)
    e["sv"] = curve(synth.right, verify.left)
    e["end"] = f"M {verify.x} {verify.y - 24} C {verify.x} 150, {end.x} 160, {end.x} {end.y + 9}"
    e["v0"] = curve(verify.bottom, vdeeps[0].top, vertical=True)
    e["v1"] = f"M {verify.x + 52} {verify.y + 10} C 1080 250, 1080 382, {vdeeps[1].x + 54} {vdeeps[1].y}"
    e["vd0"] = curve(vdeeps[0].left, vjoin.right)
    e["vd1"] = curve(vdeeps[1].left, vjoin.right)
    e["vj"] = curve(vjoin.top, synth.bottom, vertical=True)
    for key, d in e.items():
        edge(svg, d, dashed=key in {"v0", "v1", "vd0", "vd1", "vj"})

    svg.add(text(982, 256, "follow-ups,", size=10, anchor="end", cls="muted"),
            text(982, 269, "if rounds remain", size=10, anchor="end", cls="muted"),
            text(1020, 146, "done", size=10, anchor="end", cls="muted"))

    # Evidence ledger strip: results land here only through the serial record steps.
    svg.add('<rect class="panel" x="60" y="352" width="690" height="92" rx="10"/>',
            text(76, 374, "EvidenceLedger", size=12, weight=600, anchor="start"),
            text(76, 390, "append-only, in plan order; claim IDs like q2/c1", size=10, anchor="start", cls="mono muted"))
    for x in (378, 750):
        svg.add(f'<path class="edge dash" d="M {x} {y + 24} L {x} 352"/>')

    t = {  # schedule, seconds
        "start": (0.3, 1.0), "plan": (1.0, 2.0),
        "fan": (2.0, 2.6), "scout": [(2.6, 4.6), (2.6, 3.8), (2.6, 5.2)],
        "join1": (5.8, 6.8), "to_gap": (6.8, 7.3), "gap": (7.3, 8.3),
        "fan2": (8.3, 8.9), "deep": [(8.9, 10.4), (8.9, 9.8)],
        "join2": (11.0, 11.8), "to_synth": (11.8, 12.3), "synth": (12.3, 13.3), "sv": (13.3, 13.8),
        "verify": (13.8, 14.8), "vfan": (14.8, 15.3), "vdeep": [(15.3, 16.0), (15.3, 15.8)],
        "vjoin": (16.5, 17.0), "vj": (17.0, 17.4), "synth2": (17.4, 18.0), "sv2": (18.0, 18.4),
        "verify2": (18.4, 19.0), "to_end": (19.0, 19.5), "end": (19.5, 22.0),
    }

    svg.add(f'<circle cx="{start.x}" cy="{y}" r="9" class="node"/>',
            f'<circle cx="{end.x}" cy="{end.y}" r="9" class="node {svg.windows("fill:var(--t-done);stroke:var(--c-done);stroke-width:2.6", "fill:var(--node);stroke:var(--edge);stroke-width:1.5", [t["end"]])}"/>')
    node(svg, plan, "Plan", "planner", "planner", [t["plan"]])
    for i, box in enumerate(scouts):
        node(svg, box, f"Scout q{i + 1}", "scout", "scout", [t["scout"][i]])
    node(svg, join1, "Join + record", "serial", "join", [t["join1"]])
    node(svg, gap, "Gap analysis", "gap_analyst", "gap", [t["gap"]])
    for i, box in enumerate(deeps):
        node(svg, box, f"Deep dive q{(1, 3)[i]}", "deep_dive", "deep", [t["deep"][i]])
    node(svg, join2, "Join + record", "serial", "join", [t["join2"]])
    node(svg, synth, "Synthesize", "synthesizer", "synth", [t["synth"], t["synth2"]])
    node(svg, verify, "Verify", "verifier", "verify", [t["verify"], t["verify2"]])
    for i, box in enumerate(vdeeps):
        node(svg, box, f"Deep dive q{(2, 3)[i]}", "attempt 1", "deep", [t["vdeep"][i]])
    node(svg, vjoin, "Join + record", "serial", "join", [t["vjoin"]])

    # Tokens: scouts finish out of order (q2, q1, q3); the join still records q1, q2, q3.
    token(svg, e["start"], "planner", [t["start"]])
    for i in range(3):
        token(svg, e[f"p{i}"], "scout", [t["fan"]])
        done = t["scout"][i][1]
        token(svg, e[f"s{i}"], "scout", [(done, done + 0.6)])
    token(svg, e["j1"], "gap", [t["to_gap"]])
    for i in range(2):
        token(svg, e[f"g{i}"], "deep", [t["fan2"]])
        done = t["deep"][i][1]
        token(svg, e[f"d{i}"], "deep", [(done, done + 0.6)])
    token(svg, e["j2"], "synth", [t["to_synth"]])
    token(svg, e["sv"], "verify", [t["sv"], t["sv2"]])
    token(svg, e["v0"], "deep", [t["vfan"]])
    token(svg, e["v1"], "deep", [t["vfan"]])
    for i in range(2):
        done = t["vdeep"][i][1]
        token(svg, e[f"vd{i}"], "deep", [(done, done + 0.5)])
    token(svg, e["vj"], "synth", [t["vj"]])
    token(svg, e["end"], "done", [t["to_end"]])

    # Ledger chips: appended in plan order by each record step.
    for i, label in enumerate(("q1", "q2", "q3")):
        chip(svg, 80 + i * 42, 402, label, "scout", 6.1 + i * 0.2)
    for i, label in enumerate(("q1", "q3")):
        chip(svg, 220 + i * 42, 402, label, "deep", 11.2 + i * 0.2)
    for i, label in enumerate(("q2", "q3")):
        chip(svg, 318 + i * 42, 402, label, "deep", 16.6 + i * 0.2)
    svg.add(text(430, 415, "← scouts, then deep dives, in plan order", size=10, anchor="start", cls="muted"))

    captions = [
        ((0.0, 2.0), "The planner splits the objective into research questions."),
        ((2.0, 5.8), "Scouts research every question in parallel: map() fans out, a semaphore bounds it."),
        ((5.8, 7.2), "They finish in any order (q2 first). The join waits; a serial step records q1, q2, q3."),
        ((7.2, 8.8), "Gap analysis names weak spots; the most severe gap per question gets a deep dive."),
        ((8.8, 12.2), "Deep dives fan out and join the same way. Workers return values; they never write state."),
        ((12.2, 13.8), "The synthesizer writes a report that may cite only ledger claim IDs."),
        ((13.8, 14.9), "The verifier checks the report claim by claim and may ask for follow-ups."),
        ((14.9, 18.4), "Follow-ups buy another round while rounds remain: deep dives, then synthesize again."),
        ((18.4, 23.0), "Verified again, the run ends with its report, verification, and review_reasons."),
    ]
    for span, words in captions:
        caption(svg, 600, 80, words, [span], size=13.5)
    return svg


# ---------------------------------------------------------------------------------------------
# 2. Fan-out and join in detail: a bounded semaphore, out-of-order completion, ordered record.
# ---------------------------------------------------------------------------------------------

def fan_out_join() -> Svg:
    svg = Svg(1060, 420, 16.0, "Fan-out, join, and ordered record",
              "Five scout work items enter a semaphore with three slots, finish out of order, gather in the "
              "join in completion order, and are then sorted by plan position and appended to the evidence ledger.")
    svg.add(text(28, 36, "Fan-out → join → record", size=16, weight=700, anchor="start"),
            text(28, 56, "max_parallel_scouts = 3 here (default 8); five questions", size=11.5, anchor="start", cls="muted"))

    columns = [(110, "map(scouts)", "plan order"), (430, "Semaphore: 3 slots", "each slot runs one scout"),
               (770, "Join", "collects typed results"), (950, "EvidenceLedger", "record step")]
    for x, head, sub in columns:
        svg.add(text(x, 96, head, size=13, weight=600), text(x, 112, sub, size=10, cls="muted"))
    svg.add('<rect class="panel" x="258" y="126" width="344" height="236" rx="12"/>',
            '<rect class="panel" x="712" y="126" width="116" height="236" rx="12"/>',
            '<rect class="panel" x="892" y="126" width="116" height="236" rx="12"/>')
    for x1, x2 in ((150, 256), (604, 710), (830, 890)):
        edge(svg, f"M {x1} 244 L {x2} 244")
    lanes = (170, 244, 318)
    for ly in lanes:
        svg.add(f'<rect x="276" y="{ly - 20}" width="308" height="40" rx="8" class="node"/>')

    queue_y = [150 + i * 46 for i in range(5)]
    join_y = [150 + i * 46 for i in range(5)]
    # (question index, lane, start, end): q2 and q3 free slots for q4 and q5.
    runs = [(0, 0, 0.8, 4.4), (1, 1, 0.8, 2.4), (2, 2, 0.8, 3.2), (3, 1, 2.6, 5.4), (4, 2, 3.4, 4.8)]
    order = sorted(range(5), key=lambda i: runs[i][3])  # completion order
    record_at = 6.4

    for q, lane, start, end in runs:
        ly = lanes[lane]
        # Progress bar for this run.
        fill = svg.keyframes([(0.0, "transform:scaleX(0);opacity:0"), (start, "transform:scaleX(0);opacity:1"),
                              (end, "transform:scaleX(1);opacity:1"), (end + 0.3, "transform:scaleX(1);opacity:0"),
                              (svg.period, "transform:scaleX(1);opacity:0")])
        svg.add(f'<rect class="{fill}" x="336" y="{ly - 5}" width="236" height="10" rx="5" '
                f'fill="var(--t-scout)" stroke="var(--c-scout)" stroke-width="1" '
                f'style="transform-box:fill-box;transform-origin:left center"/>')
        # The card itself moves: queue -> lane -> join slot -> ledger slot.
        slot = order.index(q)
        qx, qy = 110 - 36, queue_y[q] - 12
        lx, lyy = 284, ly - 12
        jx, jy = 770 - 36, join_y[slot] - 12
        rx, ry = 950 - 36, queue_y[q] - 12
        move = svg.keyframes([
            (0.0, f"transform:translate({qx}px,{qy}px);opacity:1"),
            (start - 0.35 if start > 0.4 else 0.4, f"transform:translate({qx}px,{qy}px);opacity:1"),
            (start, f"transform:translate({lx}px,{lyy}px);opacity:1"),
            (end, f"transform:translate({lx}px,{lyy}px);opacity:1"),
            (end + 0.5, f"transform:translate({jx}px,{jy}px);opacity:1"),
            (record_at + q * 0.25, f"transform:translate({jx}px,{jy}px);opacity:1"),
            (record_at + q * 0.25 + 0.7, f"transform:translate({rx}px,{ry}px);opacity:1"),
            (svg.period - 0.4, f"transform:translate({rx}px,{ry}px);opacity:1"),
            (svg.period, f"transform:translate({rx}px,{ry}px);opacity:0"),
        ])
        svg.add(f'<g class="{move}"><rect width="72" height="24" rx="6" fill="var(--t-scout)" '
                f'stroke="var(--c-scout)" stroke-width="1.4"/>{text(36, 16, f"q{q + 1}", size=11.5, weight=600, cls="mono")}</g>')

    captions = [
        ((0.0, 2.6), "Three scouts start; q4 and q5 wait for a free slot."),
        ((2.6, 5.9), "Providers answer at different speeds, so results finish as q2, q3, q1, q5, q4."),
        ((5.9, 6.5), "The join holds them in completion order. No branch wrote to shared state."),
        ((6.5, 16.0), "The serial record step sorts by plan position and appends: the ledger is always q1…q5."),
    ]
    for span, words in captions:
        caption(svg, 530, 396, words, [span], size=13)
    return svg


# ---------------------------------------------------------------------------------------------
# 3. Academic acquisition: a tool call through the source policy, cache, and providers, back as
#    a normalized record, then evidence checked against that output and stored.
# ---------------------------------------------------------------------------------------------

def academic_ingestion() -> Svg:
    svg = Svg(1120, 500, 15.0, "How scholarly data is acquired, checked, and stored",
              "A scout calls a scholarly tool. The request passes the blocked-source policy and the cache, "
              "reaches OpenAlex, Crossref, arXiv, ACL Anthology, or OpenCitations, and returns as a normalized "
              "record. The agent's evidence is checked against that tool output and appended to the ledger, "
              "then persisted to Postgres and the bibliography.")
    svg.add(text(28, 36, "Academic data: request → normalize → cite → check → store", size=16, weight=700, anchor="start"),
            text(28, 56, "one scholarly tool call, and one refused because the task blocks that source",
                 size=11.5, anchor="start", cls="muted"))

    agent = Box(96, 280, 128, 300)
    svg.add(f'<rect class="node" x="{agent.x - 64}" y="{agent.y - 150}" width="128" height="300" rx="12"/>',
            text(agent.x, 250, "Scout /", size=13, weight=600), text(agent.x, 266, "deep dive", size=13, weight=600),
            text(agent.x, 286, "PydanticAI", size=10, cls="muted"), text(agent.x, 300, "agent", size=10, cls="muted"))

    r1, r2, r3 = 150, 290, 420
    tool = Box(270, r1, 150, 50)
    gate = Box(452, r1, 150, 50)
    cache = Box(636, r1, 150, 50)
    providers = ["OpenAlex", "Crossref", "arXiv", "ACL Anthology", "OpenCitations", "Web (HTTPS)"]
    prov = [Box(960, 96 + i * 34, 150, 26) for i in range(len(providers))]
    norm = Box(760, r2, 190, 54)
    window = Box(470, r2, 190, 54)
    evidence = Box(300, r3, 170, 54)
    checks = Box(512, r3, 170, 54)
    ledger = Box(724, r3, 170, 54)
    store = Box(960, r3, 190, 64)

    edge(svg, curve((agent.x + 64, r1), tool.left))
    edge(svg, curve(tool.right, gate.left))
    edge(svg, curve(gate.right, cache.left))
    for box in prov:
        edge(svg, curve(cache.right, box.left))
    svg.add(f'<path class="edge" d="M 1037 {prov[0].y} L 1052 {prov[0].y} L 1052 {prov[-1].y} L 1037 {prov[-1].y}"/>')
    edge(svg, f"M 1052 {prov[-1].y} L 1052 {r2} L {norm.x + 97} {r2}")
    edge(svg, curve(norm.left, window.right))
    edge(svg, f"M {window.x - 95} {r2} L {agent.x + 64} {r2}")
    edge(svg, curve((agent.x + 64, r3), evidence.left))
    edge(svg, curve(evidence.right, checks.left))
    edge(svg, curve(checks.right, ledger.left))
    edge(svg, curve(ledger.right, store.left))
    edge(svg, f"M {checks.x} {checks.y - 27} C {checks.x} 360, {window.x} 350, {window.x} {r2 + 27}", dashed=True)
    svg.add(text(430, 356, "checked against", size=10, anchor="end", cls="muted"),
            text(430, 369, "this run's tool output", size=10, anchor="end", cls="muted"))

    t = {"req": (0.4, 1.4), "gate": (1.4, 2.4), "cache": (2.4, 3.4), "prov": (3.4, 4.6), "norm": (5.0, 6.0),
         "window": (6.4, 7.4), "back": (7.4, 8.0), "cite": (8.2, 9.0), "check": (9.0, 10.0),
         "ledger": (10.3, 11.1), "store": (11.4, 14.4), "blocked": (1.0, 2.2)}
    node(svg, tool, "Tool call", "scholar_* · web_*", "scout", [t["req"]])
    node(svg, gate, "SourcePolicy", "blocked? refuse", "block", [t["blocked"]])
    node(svg, cache, "Cache + job memo", "live|record|replay|off", "join", [t["cache"]])
    for i, (box, name) in enumerate(zip(prov, providers)):
        node(svg, box, name, "", "deep", [t["prov"]] if i in (0, 2) else [])
    node(svg, norm, "Normalize → ScholarWork", "ids, status, status_basis", "data", [t["norm"]])
    node(svg, window, "Bounded tool result", "≤12,000 chars, next_start", "data", [t["window"]])
    node(svg, evidence, "Evidence", "SourceRef + quote", "scout", [t["cite"]])
    node(svg, checks, "quote_check", "source_check", "verify", [t["check"]])
    node(svg, ledger, "EvidenceLedger", "unique claim IDs", "join", [t["ledger"]])
    node(svg, store, "Postgres + files", "ledger JSON · tool hashes", "done", [t["store"]])
    svg.add(text(store.x, store.y + 24, "bibliography.json", size=9.5, cls="mono muted"))

    token(svg, curve((agent.x + 64, r1), tool.left), "scout", [(0.1, 0.4)])
    token(svg, curve(tool.right, gate.left), "scout", [(1.0, 1.4)])
    token(svg, curve(gate.right, cache.left), "scout", [(2.2, 2.4)])
    token(svg, curve(cache.right, prov[0].left), "deep", [(3.2, 3.6)])
    token(svg, curve(cache.right, prov[2].left), "deep", [(3.2, 3.6)])
    token(svg, f"M 1052 {prov[2].y} L 1052 {r2} L {norm.x + 97} {r2}", "data", [(4.4, 5.0)])
    token(svg, curve(norm.left, window.right), "data", [(6.0, 6.4)])
    token(svg, f"M {window.x - 95} {r2} L {agent.x + 64} {r2}", "data", [t["back"]])
    token(svg, curve((agent.x + 64, r3), evidence.left), "scout", [(8.0, 8.2)])
    token(svg, curve(evidence.right, checks.left), "verify", [(8.8, 9.0)])
    token(svg, curve(checks.right, ledger.left), "join", [(10.0, 10.3)])
    token(svg, curve(ledger.right, store.left), "done", [(11.1, 11.4)])
    # A second call hits a blocked source and bounces back as a BlockedSource error.
    bounce = f"M {agent.x + 64} {r1 - 16} C 250 90, 360 96, {gate.x - 75} {r1 - 16}"
    token(svg, bounce, "block", [(1.0, 1.9)])
    svg.add(f'<path class="edge dash" d="{bounce}"/>')
    caption(svg, gate.x, 108, "BlockedSource", [(1.9, 3.0)], size=11, cls="mono")

    captions = [
        ((0.0, 2.4), "A scout calls a scholarly tool. A fetch of a blocked source is refused before any request."),
        ((2.4, 4.6), "Cache and per-job memo first; then the provider APIs (search hits OpenAlex and arXiv)."),
        ((4.6, 8.0), "Provider records become ScholarWork records: IDs, dates, publication status and its basis."),
        ((8.0, 10.2), "The agent cites a SourceRef with a verbatim quote; code checks both against its tool output."),
        ((10.2, 15.0), "The ledger renames claims uniquely; Postgres keeps the ledger and hashed tool events."),
    ]
    for span, words in captions:
        caption(svg, 560, 482, words, [span], size=13)
    return svg


# ---------------------------------------------------------------------------------------------
# 4. A long-horizon study: questions run one job at a time, publish atomically, then aggregate
#    and synthesize once.
# ---------------------------------------------------------------------------------------------

def long_horizon() -> Svg:
    svg = Svg(1100, 380, 16.0, "A long-horizon study",
              "A spec lists research questions. Each runs as its own research-graph job and publishes its "
              "folder atomically with a run.json of file hashes; a failed question is recorded and skipped. "
              "Aggregation merges the completed ledgers and bibliographies, and one synthesis job writes the "
              "study report, catalogs, and hypotheses.")
    svg.add(text(28, 36, "Long-horizon study: many research jobs, one synthesis", size=16, weight=700, anchor="start"),
            text(28, 56, "research-long-horizon --all-questions, then --aggregate and --synthesize",
                 size=11.5, anchor="start", cls="mono muted"))

    spec = Box(96, 200, 144, 70)
    node(svg, spec, "spec.toml", "questions, window,", "planner", [(0.2, 1.2)])
    svg.add(text(spec.x, spec.y + 24, "source policy, budgets", size=9.5, cls="mono muted"))
    qs = ["q01", "q02", "q03", "q04", "q05", "q06"]
    boxes = [Box(262 + i * 88, 200, 74, 46) for i in range(len(qs))]
    edge(svg, curve(spec.right, boxes[0].left))
    for a, b in zip(boxes, boxes[1:]):
        edge(svg, curve(a.right, b.left))
    svg.add(text(boxes[-1].x + 54, 196, "…", size=18, cls="muted"))
    agg = Box(850, 200, 148, 56)
    syn = Box(1005, 200, 124, 56)
    edge(svg, curve((boxes[-1].x + 37, 200), agg.left))
    edge(svg, curve(agg.right, syn.left))

    step = 1.35
    for i, (box, label) in enumerate(zip(boxes, qs)):
        start = 1.2 + i * step
        failed = label == "q04"
        node(svg, box, label, "graph run", "block" if failed else "scout", [(start, start + step * 0.8)])
        badge = "✕ failed" if failed else "✓ run.json"
        accent = "block" if failed else "verify"
        show = svg.keyframes([(0.0, "opacity:0"), (start + step * 0.8, "opacity:0"), (start + step * 0.85, "opacity:1"),
                              (svg.period - 0.3, "opacity:1"), (svg.period, "opacity:0")])
        svg.add(f'<g class="{show}">{text(box.x, box.y + 42, badge, size=10.5, cls="mono")}'
                f'<rect x="{box.x - 34}" y="{box.y + 29}" width="68" height="18" rx="4" fill="none" '
                f'stroke="var(--c-{accent})" stroke-width="1.2"/></g>')
    t_agg = 1.2 + len(qs) * step + 0.2
    node(svg, agg, "Aggregate", "ledgers + bibliography", "join", [(t_agg, t_agg + 1.2)])
    node(svg, syn, "Synthesize", "one agent job", "synth", [(t_agg + 1.4, t_agg + 3.6)])
    for i, name in enumerate(("report.md", "catalogs", "hypotheses")):
        chip(svg, syn.x - 60, 250 + i * 24, name, "synth", t_agg + 2.8 + i * 0.3, w=120)

    captions = [
        ((0.0, 1.3), "The spec fixes the scope: questions, publication window, source tiers, budgets."),
        ((1.3, 5.3), "Questions run one job at a time; each publishes its folder in one rename."),
        ((5.3, 6.6), "A failed question is recorded and the run moves on; max_failed_questions failures stop it."),
        ((6.6, 9.4), "run.json holds each file's hash and the objective hash; edited or stale outputs are left out."),
        ((9.4, 16.0), "Aggregation merges completed evidence; one synthesis job writes the report, catalogs, hypotheses."),
    ]
    for span, words in captions:
        caption(svg, 550, 120, words, [span], size=13)
    return svg


DIAGRAMS = {
    "research-graph": research_graph,
    "fan-out-join": fan_out_join,
    "academic-ingestion": academic_ingestion,
    "long-horizon": long_horizon,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--freeze", type=float, nargs="*", default=[],
                        help="also write stills paused at these seconds, for reviewing layout")
    parser.add_argument("--output", type=Path, default=ASSETS)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, build in DIAGRAMS.items():
        svg = build()
        (args.output / f"{name}.svg").write_text(svg.render(), encoding="utf-8")
        for second in args.freeze:
            (args.output / f"{name}-t{second:g}.svg").write_text(svg.render(freeze=second), encoding="utf-8")
    print(f"wrote {len(DIAGRAMS)} diagrams to {args.output}")


if __name__ == "__main__":
    main()
