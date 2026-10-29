"""Консольный запуск:

    python -m museumcanon --museums configs/moma.yaml configs/tate.yaml \
                          --groups configs/groups_russia.yaml --out output

    python -m museumcanon --download --museums configs/moma.yaml configs/tate.yaml
"""
from __future__ import annotations

import argparse
from functools import reduce
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from . import Cleaner, Grouping, Visualizer, download, load_collection  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="museumcanon",
                                     description="Группа художников в коллекциях и выставках музеев")
    parser.add_argument("--museums", nargs="+", required=True, help="YAML-конфиги музеев")
    parser.add_argument("--groups", help="YAML с группами художников")
    parser.add_argument("--out", default="output", help="папка для графиков и таблиц")
    parser.add_argument("--start", type=int, default=1920, help="с какого года смотреть")
    parser.add_argument("--end", type=int, help="по какой год")
    parser.add_argument("--bin", type=int, default=10, help="шаг в годах (10 — десятилетия)")
    parser.add_argument("--highlight", help="регулярка по credit line, напр. Rothschild")
    parser.add_argument("--download", action="store_true", help="только скачать данные")
    args = parser.parse_args()

    if args.download:
        download(args.museums)
        return
    if not args.groups:
        parser.error("нужен --groups (или --download)")

    collections = [Cleaner.from_config(path).run(load_collection(path, skip_missing=True))
                   for path in args.museums]
    collection = reduce(lambda a, b: a + b, collections)          # Collection.__add__
    print(collection)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    viz = Visualizer(collection, Grouping.from_yaml(args.groups), args.start, args.end, args.bin, out)
    summary = viz.summary()
    print(summary.to_string(index=False))
    summary.to_csv(out / "summary.csv", index=False)
    viz.top_donors(n=20).to_csv(out / "top_donors.csv", index=False)

    viz.plot_acquisitions(highlight=args.highlight)
    viz.plot_acquisitions(share=True)
    viz.plot_acquisition_types()
    viz.plot_exhibitions()
    main_group = [viz.grouping.names[0]]                          # сеть по первой (главной) группе
    graph = viz.co_exhibition_graph(main_group, min_shared=2)
    if graph:
        viz.plot_network(main_group, min_shared=2)
        viz.centrality(graph, n=30).to_csv(out / "network_centrality.csv", index=False)
        try:
            viz.plot_network(viz.grouping.names, min_shared=2, html="network.html")
        except ImportError as error:
            print(error)
    print(f"Готово: {out.resolve()}")


if __name__ == "__main__":
    main()
