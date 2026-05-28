#!/usr/bin/env python3
"""Stdlib-only code-map / knowledge-graph generator for ha-pricehawk.

This is a Claude-generated stand-in for the `graphify` tool referenced in
AGENTS.md: the real binary is not installable in the CI sandbox (the public
`graphify` package is an unrelated random-graph generator, and PyPI is
unreachable). It produces the same *consumption contract* AGENTS.md points
reviewers at — `graphify-out/GRAPH_REPORT.md` and `graphify-out/wiki/index.md`
— plus a machine-readable `graph.json`.

Method: pure AST analysis, no execution of the target code, no network, no API
cost. Re-run with `python graphify-out/build_codemap.py` after code changes
(the poor-man's `graphify update .`).

Graph model
-----------
- Nodes: each Python module under custom_components/pricehawk/.
- Directed edges: A -> B means "module A imports from module B" (A depends on B).
- PageRank is computed on this import graph, so modules that many others depend
  on accrue rank => "load-bearing / god nodes".
- Communities: label propagation on the undirected projection.
- Cycles: Tarjan strongly-connected components (SCC size > 1 = import cycle).
- External boundary: third-party imports per module (homeassistant, aiohttp,
  aemo_to_tariff, ...), to surface I/O and framework coupling.
- Test coverage: which tests/ files import each module ("tested-by").
"""
from __future__ import annotations

import ast
import json
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG_ROOT = "custom_components.pricehawk"
SRC_DIR = REPO / "custom_components" / "pricehawk"
TESTS_DIR = REPO / "tests"
OUT = REPO / "graphify-out"

# Third-party prefixes we classify as "external boundary" of interest.
BOUNDARY = {
    "aiohttp": "network I/O",
    "aemo_to_tariff": "tariff library",
    "homeassistant": "HA framework",
    "voluptuous": "schema/validation",
}


def to_module(path: Path) -> str:
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def short(full: str) -> str:
    if full == PKG_ROOT:
        return "__init__ (package root)"
    if full.startswith(PKG_ROOT + "."):
        return full[len(PKG_ROOT) + 1:]
    return full


def containing_package(full: str, is_pkg: bool) -> str:
    return full if is_pkg else full.rpartition(".")[0]


def resolve_relative(pkg: str, level: int, module: str | None) -> str:
    base_parts = pkg.split(".") if pkg else []
    up = level - 1
    if up > 0:
        base_parts = base_parts[:-up] if up <= len(base_parts) else []
    base = ".".join(base_parts)
    if module:
        return f"{base}.{module}" if base else module
    return base


def collect_files() -> list[Path]:
    return sorted(SRC_DIR.rglob("*.py"))


def parse_module(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return tree


def public_symbols(tree: ast.Module) -> dict:
    funcs, classes = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                methods = sum(
                    1
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not n.name.startswith("_")
                )
                classes.append((node.name, methods))
    return {"functions": funcs, "classes": classes}


def imports(tree: ast.Module, cur: str, is_pkg: bool, known: set):
    """Return (internal_targets, external_pkgs, symbol_uses).

    internal_targets: set of repo module dotted names this module depends on.
    external_pkgs: set of top-level third-party package names.
    symbol_uses: list of (target_module, symbol_name) for imported repo symbols.
    """
    pkg = containing_package(cur, is_pkg)
    internal, external, sym_uses = set(), set(), []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == PKG_ROOT or name.startswith(PKG_ROOT + "."):
                    if name in known and name != cur:
                        internal.add(name)
                else:
                    external.add(name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = resolve_relative(pkg, node.level, node.module)
            else:
                base = node.module or ""
            if base == PKG_ROOT or base.startswith(PKG_ROOT + "."):
                # Each imported name may itself be a submodule or a symbol.
                matched_submodule = False
                for alias in node.names:
                    sub = f"{base}.{alias.name}"
                    if sub in known and sub != cur:
                        internal.add(sub)
                        matched_submodule = True
                    elif base in known and base != cur:
                        sym_uses.append((base, alias.name))
                if base in known and base != cur and not matched_submodule:
                    internal.add(base)
            elif base:
                external.add(base.split(".")[0])
    return internal, external, sym_uses


def pagerank(nodes, out_edges, d=0.85, iters=80):
    n = len(nodes)
    if n == 0:
        return {}
    pr = {x: 1.0 / n for x in nodes}
    dangling = [x for x in nodes if not out_edges.get(x)]
    for _ in range(iters):
        dsum = sum(pr[x] for x in dangling)
        new = {x: (1 - d) / n + d * dsum / n for x in nodes}
        for a in nodes:
            outs = out_edges.get(a)
            if outs:
                share = pr[a] / len(outs)
                for b in outs:
                    new[b] += d * share
        pr = new
    return pr


def tarjan_scc(nodes, out_edges):
    index = {}
    low = {}
    onstack = {}
    stack = []
    counter = [0]
    result = []

    import sys
    sys.setrecursionlimit(10000)

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        onstack[v] = True
        for w in sorted(out_edges.get(v, ())):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif onstack.get(w):
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                onstack[w] = False
                comp.append(w)
                if w == v:
                    break
            result.append(comp)

    for v in sorted(nodes):
        if v not in index:
            strong(v)
    return result


def communities(nodes, undirected, iters=200):
    label = {n: i for i, n in enumerate(sorted(nodes))}
    order = sorted(nodes)
    for _ in range(iters):
        changed = False
        for n in order:
            nbrs = undirected.get(n)
            if not nbrs:
                continue
            counts = defaultdict(int)
            for m in nbrs:
                counts[label[m]] += 1
            best = max(sorted(counts), key=lambda lbl: (counts[lbl], -lbl))
            if label[n] != best:
                label[n] = best
                changed = True
        if not changed:
            break
    groups = defaultdict(list)
    for n, lbl in label.items():
        groups[lbl].append(n)
    return [sorted(v) for _, v in sorted(groups.items())]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True
        ).strip()
    except Exception:
        return "unknown"


def build():
    files = collect_files()
    modules = {}  # full -> info
    known = {to_module(p) for p in files}

    for p in files:
        full = to_module(p)
        is_pkg = p.name == "__init__.py"
        tree = parse_module(p)
        internal, external, sym_uses = imports(tree, full, is_pkg, known)
        modules[full] = {
            "path": str(p.relative_to(REPO)),
            "is_pkg": is_pkg,
            "doc": (ast.get_docstring(tree) or "").strip().split("\n")[0],
            "loc": len(p.read_text(encoding="utf-8").splitlines()),
            "symbols": public_symbols(tree),
            "deps": sorted(internal),
            "external": sorted(external),
            "sym_uses": sym_uses,
        }

    nodes = set(modules)
    out_edges = {m: set(info["deps"]) for m, info in modules.items()}
    in_edges = defaultdict(set)
    for a, outs in out_edges.items():
        for b in outs:
            in_edges[b].add(a)

    undirected = defaultdict(set)
    for a, outs in out_edges.items():
        for b in outs:
            undirected[a].add(b)
            undirected[b].add(a)

    pr = pagerank(nodes, out_edges)
    sccs = [c for c in tarjan_scc(nodes, out_edges) if len(c) > 1]
    comms = communities(nodes, undirected)

    # Symbol-level in-degree (how many modules import a given public symbol).
    sym_in = defaultdict(int)
    for info in modules.values():
        for tgt, name in info["sym_uses"]:
            sym_in[(tgt, name)] += 1

    # Test coverage: which test files import each module.
    tested_by = defaultdict(set)
    if TESTS_DIR.exists():
        for tp in sorted(TESTS_DIR.rglob("test_*.py")):
            try:
                ttree = parse_module(tp)
            except Exception:
                continue
            ti, _, _ = imports(ttree, "tests." + tp.stem, False, known)
            for m in ti:
                tested_by[m].add(tp.name)

    return {
        "modules": modules,
        "nodes": nodes,
        "out_edges": out_edges,
        "in_edges": in_edges,
        "pagerank": pr,
        "sccs": sccs,
        "communities": comms,
        "sym_in": sym_in,
        "tested_by": tested_by,
    }


def community_label(members: list[str]) -> str:
    """Name a community by its common package prefix."""
    shorts = [short(m) for m in members]
    prefixes = set()
    for s in shorts:
        prefixes.add(s.split(".")[0] if "." in s else "(root)")
    if len(prefixes) == 1:
        p = next(iter(prefixes))
        return "root package" if p == "(root)" else f"`{p}` subtree"
    return "mixed: " + ", ".join(sorted(prefixes))


def write_report(g):
    modules = g["modules"]
    pr = g["pagerank"]
    in_edges = g["in_edges"]
    out_edges = g["out_edges"]
    tested_by = g["tested_by"]
    sym_in = g["sym_in"]

    total_edges = sum(len(v) for v in out_edges.values())
    ext_counter = defaultdict(set)
    for m, info in modules.items():
        for e in info["external"]:
            ext_counter[e].add(m)

    ranked = sorted(
        modules, key=lambda m: (pr[m], len(in_edges[m])), reverse=True
    )

    lines = []
    lines.append("# ha-pricehawk — Code Map (Knowledge Graph)\n")
    lines.append(
        "> **Provenance:** Generated by Claude Code via `graphify-out/build_codemap.py` "
        f"(stdlib AST analysis, no API cost). Commit `{git_sha()}`, {date.today().isoformat()}. "
        "This is a stand-in for the `graphify` tool (not installable in-sandbox); it satisfies "
        "the same consumption contract AGENTS.md references. Regenerate with "
        "`python graphify-out/build_codemap.py`.\n"
    )
    lines.append("## How to read this\n")
    lines.append(
        "- **Edge direction:** `A -> B` means *A imports/depends on B*. "
        "So a module with high **in-degree** is depended upon by many others — load-bearing.\n"
        "- **PageRank** runs on the import graph, so foundational modules (imported widely, "
        "directly or transitively) rank highest. Treat the top of the table as the **god nodes**: "
        "changing them has the widest blast radius.\n"
        "- **Communities** are import-coupling clusters (label propagation). They tell a reviewer "
        "which modules move together.\n"
        "- **Tested-by** counts the `tests/` files importing a module: load-bearing *and* "
        "well-tested vs load-bearing *and* thin.\n"
    )

    lines.append("\n## Summary\n")
    lines.append(f"- Modules (nodes): **{len(modules)}**")
    lines.append(f"- Internal dependency edges: **{total_edges}**")
    lines.append(f"- Import cycles (SCC > 1): **{len(g['sccs'])}**")
    lines.append(f"- Communities: **{len(g['communities'])}**")
    total_loc = sum(i["loc"] for i in modules.values())
    lines.append(f"- Total LOC (package): **{total_loc}**\n")

    lines.append("\n## God nodes / load-bearing modules\n")
    lines.append("Ranked by PageRank on the import graph (highest blast radius first).\n")
    lines.append("| Rank | Module | PageRank | In-deg | Out-deg | Tested-by | Role |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for i, m in enumerate(ranked, 1):
        info = modules[m]
        role = info["doc"] or "_(no module docstring)_"
        if len(role) > 70:
            role = role[:67] + "..."
        lines.append(
            f"| {i} | `{short(m)}` | {pr[m]:.3f} | {len(in_edges[m])} | "
            f"{len(out_edges[m])} | {len(tested_by.get(m, ()))} | {role} |"
        )

    lines.append("\n## Communities (import-coupling clusters)\n")
    for idx, members in enumerate(g["communities"], 1):
        lines.append(f"### Community {idx} — {community_label(members)}\n")
        for m in members:
            info = modules[m]
            deg = f"in {len(in_edges[m])} / out {len(out_edges[m])}"
            lines.append(f"- `{short(m)}` ({deg}) — {info['doc'] or '_(no docstring)_'}")
        lines.append("")

    lines.append("\n## Import cycles\n")
    if g["sccs"]:
        for comp in g["sccs"]:
            lines.append("- ⚠️ " + " ↔ ".join(f"`{short(x)}`" for x in comp))
    else:
        lines.append("None. The dependency graph is a DAG (no import cycles).")

    lines.append("\n## External boundary (third-party coupling)\n")
    lines.append("Where the package touches the outside world — the surfaces most worth guarding.\n")
    lines.append("| Package | Kind | Modules touching it |")
    lines.append("|---|---|---|")
    for e in sorted(ext_counter, key=lambda x: (-len(ext_counter[x]), x)):
        kind = BOUNDARY.get(e, "stdlib/other")
        mods = ", ".join(f"`{short(m)}`" for m in sorted(ext_counter[e]))
        lines.append(f"| `{e}` | {kind} | {mods} |")

    lines.append("\n## Most-imported public symbols\n")
    top_syms = sorted(sym_in.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))[:15]
    if top_syms:
        lines.append("| Symbol | Defined in | Imported by N modules |")
        lines.append("|---|---|---:|")
        for (tgt, name), cnt in top_syms:
            lines.append(f"| `{name}` | `{short(tgt)}` | {cnt} |")
    else:
        lines.append("_(no internal symbol imports detected)_")

    lines.append("\n## Per-module detail\n")
    for m in sorted(modules):
        info = modules[m]
        lines.append(f"### `{short(m)}`\n")
        lines.append(f"- Path: `{info['path']}` · LOC: {info['loc']}")
        if info["doc"]:
            lines.append(f"- Purpose: {info['doc']}")
        classes = ", ".join(f"`{c}`(+{n})" for c, n in info["symbols"]["classes"]) or "—"
        funcs = ", ".join(f"`{f}`" for f in info["symbols"]["functions"]) or "—"
        lines.append(f"- Public classes: {classes}")
        lines.append(f"- Public functions: {funcs}")
        deps = ", ".join(f"`{short(d)}`" for d in info["deps"]) or "—"
        rdeps = ", ".join(f"`{short(d)}`" for d in sorted(in_edges[m])) or "—"
        lines.append(f"- Depends on: {deps}")
        lines.append(f"- Depended on by: {rdeps}")
        ext = ", ".join(f"`{e}`" for e in info["external"]) or "—"
        lines.append(f"- External: {ext}")
        tb = ", ".join(sorted(tested_by.get(m, ()))) or "⚠️ none"
        lines.append(f"- Tested by: {tb}")
        lines.append("")

    (OUT / "GRAPH_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def write_wiki(g):
    modules = g["modules"]
    pr = g["pagerank"]
    in_edges = g["in_edges"]
    (OUT / "wiki").mkdir(parents=True, exist_ok=True)
    ranked = sorted(modules, key=lambda m: pr[m], reverse=True)
    lines = []
    lines.append("# ha-pricehawk Wiki — Index\n")
    lines.append(
        "Navigation entry point for the code map. Full detail lives in "
        "[`../GRAPH_REPORT.md`](../GRAPH_REPORT.md).\n"
    )
    lines.append("## Start here (highest blast radius)\n")
    for m in ranked[:5]:
        lines.append(
            f"- **`{short(m)}`** — {modules[m]['doc'] or 'core module'} "
            f"(depended on by {len(in_edges[m])} modules)"
        )
    lines.append("\n## Communities\n")
    for idx, members in enumerate(g["communities"], 1):
        names = ", ".join(f"`{short(x)}`" for x in members)
        lines.append(f"{idx}. {community_label(members)}: {names}")
    lines.append("\n## Where do I find…\n")
    lines.append("- **Cost/tariff math:** `tariff_engine`, `wholesale.flow_power.pricing`, "
                 "`wholesale.flow_power.tariff_utils`, `wholesale.amber.calculator`")
    lines.append("- **HA wiring:** `__init__ (package root)`, `coordinator`, `sensor`, `config_flow`")
    lines.append("- **Provider abstraction:** `wholesale.protocol`, `wholesale.amber.provider`, "
                 "`wholesale.flow_power`")
    lines.append("- **Data import/backfill:** `backfill`, `csv_analyzer`")
    lines.append("- **Constants/contracts:** `const`, `wholesale.flow_power.const`\n")
    (OUT / "wiki" / "index.md").write_text("\n".join(lines), encoding="utf-8")


def write_json(g):
    modules = g["modules"]
    pr = g["pagerank"]
    payload = {
        "generated": date.today().isoformat(),
        "commit": git_sha(),
        "generator": "graphify-out/build_codemap.py (Claude stand-in for graphify)",
        "nodes": [
            {
                "id": short(m),
                "module": m,
                "path": modules[m]["path"],
                "loc": modules[m]["loc"],
                "doc": modules[m]["doc"],
                "pagerank": round(pr[m], 5),
                "in_degree": len(g["in_edges"][m]),
                "out_degree": len(g["out_edges"][m]),
                "tested_by": sorted(g["tested_by"].get(m, ())),
                "external": modules[m]["external"],
                "classes": [c for c, _ in modules[m]["symbols"]["classes"]],
                "functions": modules[m]["symbols"]["functions"],
            }
            for m in sorted(modules)
        ],
        "edges": [
            {"from": short(a), "to": short(b)}
            for a in sorted(g["out_edges"])
            for b in sorted(g["out_edges"][a])
        ],
        "communities": [[short(x) for x in c] for c in g["communities"]],
        "cycles": [[short(x) for x in c] for c in g["sccs"]],
    }
    (OUT / "graph.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    g = build()
    write_report(g)
    write_wiki(g)
    write_json(g)
    print(
        f"Wrote graphify-out/: {len(g['modules'])} modules, "
        f"{sum(len(v) for v in g['out_edges'].values())} edges, "
        f"{len(g['communities'])} communities, {len(g['sccs'])} cycles."
    )


if __name__ == "__main__":
    main()
