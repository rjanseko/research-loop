"""Build the animated SVG diagrams in docs/assets/ that the README embeds.

The diagrams are generated rather than drawn so their timing and geometry stay exact and a
change to the research loop can be reflected by editing one table here. Animations are CSS
only (keyframes and offset-path), with no script, so GitHub and browsers play them inside an
<img>; they honor prefers-reduced-motion and prefers-color-scheme.

    .venv/bin/python scripts/readme_diagrams.py            # write docs/assets/*.svg
    .venv/bin/python scripts/readme_diagrams.py --freeze 6 # also write *-t6.svg stills for review

Labels follow one convention: sentence case for anything written in words, and monospace only
for real code names. Schedules are written in script seconds; each diagram's `speed`
divides them to give the played duration.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html import escape
from itertools import pairwise
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
        f":root{{--bg:#ffffff;--fg:#0f172a;--muted:#475569;--node:#f1f5f9;--edge:#94a3b8;--frame:#cbd5e1;{light}}}"
        f"@media (prefers-color-scheme: dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--muted:#9da7b3;"
        f"--node:#1c2129;--edge:#6e7681;--frame:#3d444d;{dark}}}}}"
        "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"
        "text{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;fill:var(--fg)}"
        ".mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}"
        ".muted{fill:var(--muted)}"
        ".edge{fill:none;stroke:var(--edge);stroke-width:2}"
        ".dash{stroke-dasharray:5 4}"
        ".arrow{fill:var(--edge)}"
        ".node{fill:var(--node);stroke:var(--edge);stroke-width:1.6}"
        ".frame{fill:var(--bg);stroke:var(--frame);stroke-width:1}"
        ".panel{fill:none;stroke:var(--edge);stroke-width:1.4;stroke-dasharray:5 4}"
    )


@dataclass
class Svg:
    """One animated diagram: elements, a keyframe collector, and a loop length in seconds."""

    width: int
    height: int
    period: float
    title: str
    desc: str
    speed: float = 1.0  # played duration is period / speed
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
        self.css.append(f"@keyframes {name}{{{steps}}}.{name}{{animation:{name} {self.period / self.speed:.3f}s linear infinite}}")
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
            css += f"*{{animation-delay:-{freeze / self.speed:.3f}s!important;animation-play-state:paused!important}}"
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img" aria-labelledby="t d">'
            f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc)}</desc>'
            f"<style>{css}</style>"
            '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" '
            'orient="auto-start-reverse"><path class="arrow" d="M0 0L10 5L0 10z"/></marker></defs>'
            f'<rect class="frame" x="0.5" y="0.5" width="{self.width - 1}" height="{self.height - 1}" rx="14"/>'
            + "".join(self.body)
            + "</svg>"
        )


EASE = "animation-timing-function:cubic-bezier(.45,0,.2,1)"


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


def states(svg: Svg, accent: str, spans: list[tuple[float, float]], ramp: float = 0.2) -> str:
    """Class for a shape that glows in its accent during each span and keeps an accent outline
    after it has run, until the period restarts."""
    idle = "fill:var(--node);stroke:var(--edge);stroke-width:1.6;filter:drop-shadow(0 0 0 transparent)"
    lit = (f"fill:var(--t-{accent});stroke:var(--c-{accent});stroke-width:2.6;"
           f"filter:drop-shadow(0 0 5px var(--c-{accent}))")
    ran = f"fill:var(--node);stroke:var(--c-{accent});stroke-width:2;filter:drop-shadow(0 0 0 transparent)"
    frames = [(0.0, idle)]
    before = idle
    for start, end in sorted(spans):
        frames += [(max(start - ramp, 0.0), before), (start, lit), (end, lit), (end + ramp, ran)]
        before = ran
    frames += [(svg.period - 0.4, before), (svg.period, idle)]
    return svg.keyframes(frames)


def node(svg: Svg, box: Box, title: str, sub: str, accent: str, spans: list[tuple[float, float]], *,
         code: bool = True) -> None:
    """A labelled box that lights up in its role's accent during each span. `sub` is a code name,
    set in monospace, unless `code` is false."""
    lit = states(svg, accent, spans) if spans else ""
    svg.add(
        f'<rect class="node {lit}" x="{box.x - box.w / 2}" y="{box.y - box.h / 2}" width="{box.w}" '
        f'height="{box.h}" rx="9"/>',
        text(box.x, box.y - (3 if sub else -5), title, size=13.5, weight=600),
    )
    if sub:
        svg.add(text(box.x, box.y + 14, sub, size=10.5 if code else 11, cls="mono muted" if code else "muted"))


def curve(a: tuple[float, float], b: tuple[float, float], *, vertical: bool = False) -> str:
    (x1, y1), (x2, y2) = a, b
    if vertical:
        dy = (y2 - y1) * 0.5
        return f"M {x1} {y1} C {x1} {y1 + dy}, {x2} {y2 - dy}, {x2} {y2}"
    dx = max(abs(x2 - x1) * 0.5, 12)
    return f"M {x1} {y1} C {x1 + dx} {y1}, {x2 - dx} {y2}, {x2} {y2}"


def edge(svg: Svg, d: str, *, dashed: bool = False) -> None:
    svg.add(f'<path class="edge{" dash" if dashed else ""}" d="{d}" marker-end="url(#a)"/>')


def token(svg: Svg, d: str, accent: str, spans: list[tuple[float, float]], r: float = 6.5) -> None:
    """A dot that travels along path `d` during each span, hidden otherwise."""
    frames: list[tuple[float, str]] = [(0.0, "offset-distance:0%;opacity:0"), (svg.period, "offset-distance:100%;opacity:0")]
    for start, end in spans:
        frames += [
            (max(start - 0.01, 0.0), "offset-distance:0%;opacity:0"),
            (start, f"offset-distance:0%;opacity:1;{EASE}"),
            (end, "offset-distance:100%;opacity:1"),
            (min(end + 0.01, svg.period), "offset-distance:100%;opacity:0"),
        ]
    cls = svg.keyframes(frames)
    path = d.replace("'", "")
    svg.add(
        f'<g class="{cls}" style="offset-path:path(\'{path}\');offset-rotate:0deg">'
        f'<circle r="{r * 1.9}" fill="var(--c-{accent})" opacity="0.22"/>'
        f'<circle r="{r}" fill="var(--c-{accent})"/></g>'
    )


def caption(svg: Svg, x: float, y: float, value: str, spans: list[tuple[float, float]], *,
            size: float = 14, anchor: str = "middle", cls: str = "") -> None:
    """Text visible only during its spans."""
    fade = svg.windows("opacity:1", "opacity:0", spans, ramp=0.3)
    svg.add(f'<g class="{fade}">{text(x, y, value, size=size, anchor=anchor, cls=cls)}</g>')


def chip(svg: Svg, x: float, y: float, label: str, accent: str, appear: float, *, w: float = 34,
         code: bool = True) -> None:
    """A small labelled block that appears at `appear` and stays until the period restarts."""
    show = svg.keyframes([(0.0, "opacity:0"), (max(appear - 0.01, 0.0), "opacity:0"), (appear, "opacity:1"),
                          (svg.period - 0.3, "opacity:1"), (svg.period, "opacity:0")])
    svg.add(
        f'<g class="{show}"><rect x="{x}" y="{y}" width="{w}" height="18" rx="4" '
        f'fill="var(--t-{accent})" stroke="var(--c-{accent})" stroke-width="1.2"/>'
        f'{text(x + w / 2, y + 13, label, size=10 if code else 11, cls="mono" if code else "")}</g>'
    )


def header(svg: Svg, title: str, subtitle: str) -> None:
    svg.add(text(28, 38, title, size=17, weight=700, anchor="start"),
            text(28, 60, subtitle, size=12.5, anchor="start", cls="muted"))


def captions(svg: Svg, y: float, lines: list[tuple[tuple[float, float], str]]) -> None:
    """The running explanation, one sentence at a time, centered at the bottom."""
    for span, words in lines:
        caption(svg, svg.width / 2, y, words, [span], size=14)


# ---------------------------------------------------------------------------------------------
# 1. One run of research-graph-v1: plan, fan out, join, gap analysis, deep dives, synthesize,
#    verify, and one verification round.
# ---------------------------------------------------------------------------------------------

def research_graph() -> Svg:
    svg = Svg(1100, 500, 21.0, "One run of the research loop",
              "Animated walk through the research graph: the planner fans out to parallel scouts, a join "
              "records their results in plan order, gap analysis fans out to deep dives, the synthesizer "
              "writes a report, the verifier checks it, and one verification round loops back before the run ends.",
              speed=1.45)
    header(svg, "One run of the research loop",
           "Each box lights up while it runs. The dots are results passed from one step to the next.")

    y = 200
    start = Box(40, y, 18, 18)
    plan = Box(118, y, 104, 48)
    scouts = [Box(250, 110, 104, 42), Box(250, y, 104, 42), Box(250, 290, 104, 42)]
    join1 = Box(378, y, 118, 48)
    gap = Box(500, y, 104, 48)
    deeps = [Box(626, 150, 110, 42), Box(626, 250, 110, 42)]
    join2 = Box(750, y, 118, 48)
    synth = Box(868, y, 104, 48)
    verify = Box(990, y, 104, 48)
    end = Box(1072, 118, 18, 18)
    vdeeps = [Box(990, 318, 110, 40), Box(990, 382, 110, 40)]
    vjoin = Box(868, 350, 118, 44)

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
    e["v1"] = f"M {verify.x + 52} {verify.y + 10} C 1080 250, 1080 382, {vdeeps[1].x + 55} {vdeeps[1].y}"
    e["vd0"] = curve(vdeeps[0].left, vjoin.right)
    e["vd1"] = curve(vdeeps[1].left, vjoin.right)
    e["vj"] = curve(vjoin.top, synth.bottom, vertical=True)
    for key, d in e.items():
        edge(svg, d, dashed=key in {"v0", "v1", "vd0", "vd1", "vj"})

    svg.add(text(982, 256, "Follow-ups, while", size=11, anchor="end", cls="muted"),
            text(982, 270, "rounds remain", size=11, anchor="end", cls="muted"),
            text(1022, 146, "Done", size=11, anchor="end", cls="muted"))

    # Evidence ledger strip: results land here only through the serial record steps.
    svg.add('<rect class="panel" x="60" y="352" width="690" height="92" rx="10"/>',
            text(76, 375, "Evidence ledger", size=13, weight=600, anchor="start"),
            text(76, 392, "EvidenceLedger", size=10.5, anchor="start", cls="mono muted"))
    for x in (378, 750):
        svg.add(f'<path class="edge dash" d="M {x} {y + 24} L {x} 352"/>')
    svg.add(f'<path class="edge dash" d="M {vjoin.x - 59} 360 L 750 360"/>')

    t = {  # schedule, script seconds
        "start": (0.3, 1.0), "plan": (1.0, 2.0),
        "fan": (2.0, 2.6), "scout": [(2.6, 4.6), (2.6, 3.8), (2.6, 5.2)],
        "join1": (5.8, 6.8), "to_gap": (6.8, 7.3), "gap": (7.3, 8.3),
        "fan2": (8.3, 8.9), "deep": [(8.9, 10.4), (8.9, 9.8)],
        "join2": (11.0, 11.8), "to_synth": (11.8, 12.3), "synth": (12.3, 13.3), "sv": (13.3, 13.8),
        "verify": (13.8, 14.8), "vfan": (14.8, 15.3), "vdeep": [(15.3, 16.0), (15.3, 15.8)],
        "vjoin": (16.5, 17.0), "vj": (17.0, 17.4), "synth2": (17.4, 18.0), "sv2": (18.0, 18.4),
        "verify2": (18.4, 19.0), "to_end": (19.0, 19.4), "end": (19.4, 20.6),
    }

    svg.add(f'<circle cx="{start.x}" cy="{y}" r="9" class="node"/>',
            f'<circle cx="{end.x}" cy="{end.y}" r="9" class="node {states(svg, "done", [t["end"]])}"/>')
    node(svg, plan, "Plan", "planner", "planner", [t["plan"]])
    for i, box in enumerate(scouts):
        node(svg, box, f"Scout q{i + 1}", "scout", "scout", [t["scout"][i]])
    node(svg, join1, "Join and record", "In plan order", "join", [t["join1"]], code=False)
    node(svg, gap, "Gap analysis", "gap_analyst", "gap", [t["gap"]])
    for i, box in enumerate(deeps):
        node(svg, box, f"Deep dive q{(1, 3)[i]}", "deep_dive", "deep", [t["deep"][i]])
    node(svg, join2, "Join and record", "In plan order", "join", [t["join2"]], code=False)
    node(svg, synth, "Synthesize", "synthesizer", "synth", [t["synth"], t["synth2"]])
    node(svg, verify, "Verify", "verifier", "verify", [t["verify"], t["verify2"]])
    for i, box in enumerate(vdeeps):
        node(svg, box, f"Deep dive q{(2, 3)[i]}", "attempt=1", "deep", [t["vdeep"][i]])
    node(svg, vjoin, "Join and record", "In plan order", "join", [t["vjoin"]], code=False)

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
        chip(svg, 80 + i * 42, 408, label, "scout", 6.1 + i * 0.2)
    for i, label in enumerate(("q1", "q3")):
        chip(svg, 220 + i * 42, 408, label, "deep", 11.2 + i * 0.2)
    for i, label in enumerate(("q2", "q3")):
        chip(svg, 318 + i * 42, 408, label, "deep", 16.6 + i * 0.2)
    svg.add(text(420, 421, "Scout results, then each round of deep dives, in plan order",
                 size=11, anchor="start", cls="muted"))

    captions(svg, 480, [
        ((0.0, 5.8), "The planner splits the objective into questions, and a scout takes each one."),
        ((5.8, 8.8), "Results are recorded in plan order, then checked for gaps."),
        ((8.8, 12.2), "The most severe gaps get deep dives, which also run in parallel."),
        ((12.2, 14.9), "The synthesizer writes a report, and the verifier checks each claim."),
        ((14.9, 18.4), "Follow-ups get another round of research and a new report."),
        ((18.4, 21.0), "The run ends with its report, its verification, and its review reasons."),
    ])
    return svg


# ---------------------------------------------------------------------------------------------
# 2. Fan-out and join in detail: a bounded semaphore, out-of-order completion, ordered record.
# ---------------------------------------------------------------------------------------------

def fan_out_join() -> Svg:
    svg = Svg(1060, 420, 10.0, "Parallel scouts, recorded in plan order",
              "Five scout work items enter a semaphore with three slots, finish out of order, gather in the "
              "join in completion order, and are then sorted by plan position and appended to the evidence ledger.",
              speed=1.1)
    header(svg, "Parallel scouts, recorded in plan order",
           "Three slots here (the default is eight) and five questions.")

    columns = [(110, "Questions", "In plan order"), (430, "Three scout slots", "A semaphore"),
               (770, "Join", "Collects results"), (950, "Evidence ledger", "Sorted by plan")]
    for x, head, sub in columns:
        svg.add(text(x, 98, head, size=13.5, weight=600), text(x, 115, sub, size=11, cls="muted"))
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
            (start - 0.35 if start > 0.4 else 0.4, f"transform:translate({qx}px,{qy}px);opacity:1;{EASE}"),
            (start, f"transform:translate({lx}px,{lyy}px);opacity:1"),
            (end, f"transform:translate({lx}px,{lyy}px);opacity:1;{EASE}"),
            (end + 0.5, f"transform:translate({jx}px,{jy}px);opacity:1"),
            (record_at + q * 0.25, f"transform:translate({jx}px,{jy}px);opacity:1;{EASE}"),
            (record_at + q * 0.25 + 0.6, f"transform:translate({rx}px,{ry}px);opacity:1"),
            (svg.period - 0.4, f"transform:translate({rx}px,{ry}px);opacity:1"),
            (svg.period, f"transform:translate({rx}px,{ry}px);opacity:0"),
        ])
        svg.add(f'<g class="{move}"><rect width="72" height="24" rx="6" fill="var(--t-scout)" '
                f'stroke="var(--c-scout)" stroke-width="1.4"/>{text(36, 16, f"q{q + 1}", size=11.5, weight=600, cls="mono")}</g>')

    captions(svg, 396, [
        ((0.0, 2.6), "Three scouts start. The other two questions wait for a free slot."),
        ((2.6, 5.9), "Scouts finish in whatever order their providers answer."),
        ((5.9, 10.0), "One step then sorts the results into plan order and adds them to the ledger."),
    ])
    return svg


# ---------------------------------------------------------------------------------------------
# 3. How a claim is checked: a quote is looked up in the tool output that research call saw,
#    the evidence enters the ledger under a claim ID, the report cites it, and the verifier checks it.
# ---------------------------------------------------------------------------------------------

def evidence_check() -> Svg:
    svg = Svg(1100, 480, 11.0, "How a claim is checked",
              "A scout records two quotes from a source it read. Code looks for each quote in the tool output "
              "that call received: one is verified and one is not found. The evidence enters the ledger as claim "
              "q1/c1, the report cites q1/c1, and the verifier checks the report claim against that evidence.")
    header(svg, "How a claim is checked",
           "Code checks quotes against what the tools returned. The verifier checks the report against the evidence.")

    r1, r2 = 160, 360
    tool = Box(170, r1, 230, 60)
    evidence = Box(530, r1, 230, 60)
    check = Box(890, r1, 230, 60)
    ledger = Box(170, r2, 230, 60)
    report = Box(530, r2, 230, 60)
    verifier = Box(890, r2, 230, 60)

    compare = f"M {check.x} {check.y - 30} C {check.x} 104, {tool.x} 104, {tool.x} {tool.y - 30}"
    down = f"M {check.x} {check.y + 30} L {check.x} 280 L {ledger.x} 280 L {ledger.x} {ledger.y - 30}"
    back = f"M {verifier.x} {verifier.y + 30} C {verifier.x} 416, {ledger.x} 416, {ledger.x} {ledger.y + 30}"
    edge(svg, curve(tool.right, evidence.left))
    edge(svg, curve(evidence.right, check.left))
    edge(svg, compare, dashed=True)
    edge(svg, down)
    edge(svg, curve(ledger.right, report.left))
    edge(svg, curve(report.right, verifier.left))
    edge(svg, back, dashed=True)
    svg.add(text(530, 102, "Looks for each quote in this call's tool output", size=11, cls="muted"),
            text(530, 434, "Checks the claim against the evidence it cites", size=11, cls="muted"))

    t = {"tool": (0.2, 1.2), "cite": (1.7, 2.7), "check": (3.2, 4.4), "ledger": (5.4, 6.2),
         "report": (6.7, 7.5), "verify": (8.0, 9.2)}
    node(svg, tool, "Tool result", "scholar_fetch", "scout", [t["tool"]])
    node(svg, evidence, "Evidence", "SourceRef + quote", "data", [t["cite"]])
    node(svg, check, "Quote and source check", "quotes.py", "verify", [t["check"]])
    node(svg, ledger, "Evidence ledger", "claim q1/c1", "join", [t["ledger"]])
    node(svg, report, "Report claim", "claim_ids=[q1/c1]", "synth", [t["report"]])
    node(svg, verifier, "Verifier", "ClaimCheck", "verify", [t["verify"]])

    # What the tool returned, and the two quotes the scout recorded.
    svg.add(text(tool.x, 210, "“…the method cut error rates", size=11, cls="muted"),
            text(tool.x, 225, "by half on both benchmarks…”", size=11, cls="muted"))
    chip(svg, evidence.x - 115, 200, "“cut error rates by half”", "data", 1.9, w=160, code=False)
    chip(svg, evidence.x - 115, 226, "“eliminated all errors”", "data", 2.2, w=160, code=False)
    chip(svg, evidence.x + 51, 200, "verified", "verify", 4.0, w=70)
    chip(svg, evidence.x + 51, 226, "not_found", "block", 4.3, w=70)
    chip(svg, verifier.x - 40, 396, "supported", "verify", 9.1, w=80)

    token(svg, curve(tool.right, evidence.left), "scout", [(1.2, 1.7)])
    token(svg, curve(evidence.right, check.left), "data", [(2.7, 3.2)])
    token(svg, compare, "verify", [(3.3, 4.0)])
    token(svg, down, "verify", [(4.6, 5.4)])
    token(svg, curve(ledger.right, report.left), "join", [(6.2, 6.7)])
    token(svg, curve(report.right, verifier.left), "synth", [(7.5, 8.0)])
    token(svg, back, "verify", [(8.2, 9.0)])

    captions(svg, 464, [
        ((0.0, 2.7), "A scout reads a source and records what it says, with quotes."),
        ((2.7, 4.6), "Code looks for each quote in what the tools actually returned."),
        ((4.6, 6.7), "The evidence goes into the ledger under a claim ID."),
        ((6.7, 8.0), "A report claim can only cite claim IDs that are in the ledger."),
        ((8.0, 11.0), "The verifier checks each report claim against the evidence it cites."),
    ])
    return svg


# ---------------------------------------------------------------------------------------------
# 4. A long-horizon study: questions run one job at a time, publish atomically, then aggregate
#    and synthesize once.
# ---------------------------------------------------------------------------------------------

def long_horizon() -> Svg:
    svg = Svg(1100, 350, 12.5, "A long-horizon study",
              "A spec lists research questions. Each runs as its own research-graph job and publishes its "
              "folder atomically with a run.json of file hashes; a failed question is recorded and skipped. "
              "Aggregation merges the completed ledgers and bibliographies, and one synthesis job writes the "
              "study report, catalogs, and hypotheses.",
              speed=1.3)
    header(svg, "A long-horizon study",
           "Each question runs as its own research job, and one synthesis combines them.")

    spec = Box(96, 165, 136, 56)
    node(svg, spec, "Study spec", "spec.toml", "planner", [(0.2, 1.2)])
    boxes = [Box(248 + i * 92, 165, 86, 46) for i in range(6)]
    edge(svg, curve(spec.right, boxes[0].left))
    for a, b in pairwise(boxes):
        edge(svg, curve(a.right, b.left))
    svg.add(text(boxes[-1].x + 60, 161, "…", size=18, cls="muted"))
    agg = Box(870, 165, 136, 56)
    syn = Box(1018, 165, 128, 56)
    edge(svg, curve((boxes[-1].x + 43, 165), agg.left))
    edge(svg, curve(agg.right, syn.left))

    step = 1.0
    for i, box in enumerate(boxes):
        start = 1.2 + i * step
        failed = i == 3
        node(svg, box, f"Question {i + 1}", "", "block" if failed else "scout", [(start, start + step * 0.8)])
        badge = "✕ Failed" if failed else "✓ Published"
        accent = "block" if failed else "verify"
        show = svg.keyframes([(0.0, "opacity:0"), (start + step * 0.8, "opacity:0"), (start + step * 0.85, "opacity:1"),
                              (svg.period - 0.3, "opacity:1"), (svg.period, "opacity:0")])
        svg.add(f'<g class="{show}">{text(box.x, box.y + 43, badge, size=11)}'
                f'<rect x="{box.x - 40}" y="{box.y + 29}" width="80" height="20" rx="4" fill="none" '
                f'stroke="var(--c-{accent})" stroke-width="1.4"/></g>')
    t_agg = 1.2 + len(boxes) * step + 0.2
    node(svg, agg, "Aggregate", "Checks file hashes", "join", [(t_agg, t_agg + 1.2)], code=False)
    node(svg, syn, "Synthesize", "One agent job", "synth", [(t_agg + 1.4, t_agg + 3.4)], code=False)
    for i, name in enumerate(("Report", "Catalogs", "Hypotheses")):
        chip(svg, syn.x - 55, 212 + i * 24, name, "synth", t_agg + 2.6 + i * 0.3, w=110, code=False)

    captions(svg, 326, [
        ((0.0, 1.2), "The spec fixes the questions, sources, and budgets."),
        ((1.2, 4.2), "Questions run one at a time, and each publishes its results in a single step."),
        ((4.2, 7.4), "A failed question is recorded and skipped. The others still count."),
        ((7.4, 12.5), "Aggregation checks every file's hash, and one synthesis job writes the study."),
    ])
    return svg


DIAGRAMS = {
    "research-graph": research_graph,
    "fan-out-join": fan_out_join,
    "evidence-check": evidence_check,
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
