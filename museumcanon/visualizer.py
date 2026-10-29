"""Блок 3: таблицы и графики.

Visualizer работает только с абстрактными Collection и Grouping — про MoMA
и Tate он ничего не знает. Любой график можно перестроить с другими
параметрами: период, шаг (десятилетие/пятилетие), группы, музеи, доля/штуки.
"""
from __future__ import annotations

import re
from functools import cached_property
from pathlib import Path
from typing import Optional, Sequence

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

from .models import Collection
from .preprocessing import Grouping

# категориальная палитра (проверена на различимость, в т.ч. при дальтонизме);
# цвет закреплён за группой по её порядку в Grouping, а не по рангу на графике
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MUTED = "#c9c8c2"
INK = "#52514e"
COLD_WAR = (1947, 1991)


def _style(ax: plt.Axes, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=12, color="#0b0b0b")
    ax.set_xlabel(xlabel, color=INK)
    ax.set_ylabel(ylabel, color=INK)
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)
    top = ax.dataLim.intervaly[1]
    ax.set_ylim(0, top * 1.12 if top > 0 else 1)     # запас сверху под легенду
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#bdbcb6")
    ax.tick_params(colors=INK)


def _legend(ax: plt.Axes) -> None:
    if ax.get_legend_handles_labels()[0]:
        ax.legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(0, 0.93))
    else:
        ax.text(0.5, 0.5, "нет данных для выбранных параметров", transform=ax.transAxes,
                ha="center", color=INK)


class Visualizer:
    def __init__(self, collection: Collection, grouping: Grouping, start: int = 1920,
                 end: Optional[int] = None, bin_size: int = 10, out_dir: Optional[str | Path] = None,
                 show_cold_war: bool = True):
        self.collection = collection
        self.grouping = grouping
        self.start, self.end, self.bin_size = start, end, bin_size
        self.out_dir = Path(out_dir) if out_dir else None
        self.show_cold_war = show_cold_war
        self.colors = {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(grouping.names)}

    # ------------------------------------------------------------------ данные
    @cached_property
    def groups(self) -> pd.DataFrame:
        assigned = self.grouping.assign(self.collection)
        return pd.DataFrame([(k, g, e) for k, (g, e) in assigned.items()],
                            columns=["artist_id", "group", "emigre"])

    @cached_property
    def artworks(self) -> pd.DataFrame:
        """Все работы × художники, с группой (NaN — вне групп)."""
        df = self.collection.artworks_frame()
        if df.empty:
            return df
        return df.merge(self.groups, on="artist_id", how="left")

    @cached_property
    def exhibitions(self) -> pd.DataFrame:
        df = self.collection.exhibitions_frame()
        if df.empty:
            return df
        return df.merge(self.groups, on="artist_id", how="left")

    def _period(self, years: pd.Series) -> pd.Series:
        return (years // self.bin_size * self.bin_size).astype("Int64")

    def _clip(self, df: pd.DataFrame, column: str) -> pd.DataFrame:
        df = df[df[column].notna()]
        df = df[df[column] >= self.start]
        return df[df[column] <= self.end] if self.end else df

    def _grouped_works(self, museums: Optional[Sequence[str]] = None,
                       groups: Optional[Sequence[str]] = None) -> pd.DataFrame:
        df = self.artworks
        if df.empty:
            return df
        df = df[df["group"].notna()]
        if museums:
            df = df[df["museum"].isin(museums)]
        if groups:
            df = df[df["group"].isin(groups)]
        # работа двух художников одной группы считается один раз
        return df.drop_duplicates(["museum", "artwork_id", "group"])

    # ----------------------------------------------------------------- таблицы
    def summary(self) -> pd.DataFrame:
        """Сколько материала: по музеям и группам."""
        rows = []
        artists = self.collection.artists_frame().merge(self.groups, on="artist_id")
        for (museum, group), sub in artists.groupby(["museum", "group"]):
            works = self._grouped_works([museum], [group])
            ex = self.exhibitions
            ex = ex[(ex["museum"] == museum) & (ex["group"] == group)] if not ex.empty else ex
            rows.append({"музей": museum, "группа": group, "художников": len(sub),
                         "из них эмигрантов": int(sub["emigre"].sum()),
                         "работ": len(works),
                         "выставок": ex["exhibition_id"].nunique() if not ex.empty else 0})
        result = pd.DataFrame(rows)
        if result.empty:
            return result
        result["группа"] = pd.Categorical(result["группа"], categories=self.grouping.names, ordered=True)
        return result.sort_values(["музей", "группа"]).reset_index(drop=True)

    def acquisitions(self, museum: str, share: bool = False) -> pd.DataFrame:
        """Поступления работ групп по периодам; share=True — % от всех поступлений музея."""
        works = self._clip(self._grouped_works([museum]), "acquisition_year")
        table = (works.assign(period=self._period(works["acquisition_year"]))
                      .pivot_table(index="period", columns="group", values="artwork_id", aggfunc="count", fill_value=0)
                      .reindex(columns=[g for g in self.grouping.names], fill_value=0))
        if share:
            everything = self.artworks[self.artworks["museum"] == museum].drop_duplicates("artwork_id")
            everything = self._clip(everything, "acquisition_year")
            total = everything.groupby(self._period(everything["acquisition_year"])).size()
            table = table.div(total.reindex(table.index), axis=0).mul(100).round(2)
        return table

    def acquisition_types(self, museum: str, group: Optional[str] = None) -> pd.DataFrame:
        works = self._clip(self._grouped_works([museum], [group] if group else None), "acquisition_year")
        works = works.drop_duplicates(["museum", "artwork_id"])
        return (works.assign(period=self._period(works["acquisition_year"]))
                     .pivot_table(index="period", columns="acquisition_type", values="artwork_id",
                                  aggfunc="count", fill_value=0))

    def top_donors(self, museum: Optional[str] = None, group: Optional[str] = None, n: int = 10) -> pd.DataFrame:
        """Самые частые credit line среди работ групп: кто сформировал корпус."""
        works = self._grouped_works([museum] if museum else None, [group] if group else None)
        works = works.drop_duplicates(["museum", "artwork_id"])
        table = (works.groupby(["museum", "credit_line"])
                      .agg(работ=("artwork_id", "count"), с=("acquisition_year", "min"), по=("acquisition_year", "max"))
                      .sort_values("работ", ascending=False).head(n).reset_index())
        table["доля, %"] = (table["работ"] / len(works) * 100).round(1)
        return table

    def exhibitions_by_year(self, group: Optional[str] = None) -> pd.DataFrame:
        ex = self._clip(self.exhibitions, "start_year")
        if ex.empty:
            return ex
        total = ex.groupby("start_year")["exhibition_id"].nunique().rename("все выставки")
        mask = ex["group"].notna() if group is None else ex["group"] == group
        hit = ex[mask].groupby("start_year")["exhibition_id"].nunique().rename("с художниками группы")
        return pd.concat([total, hit], axis=1).fillna(0).astype(int)

    def top_exhibitions(self, group: Optional[str] = None, n: int = 10) -> pd.DataFrame:
        ex = self.exhibitions
        ex = ex[ex["group"].notna()] if group is None else ex[ex["group"] == group]
        return (ex.groupby(["exhibition_id", "title", "start_year"])["artist_id"].nunique()
                  .rename("художников группы").sort_values(ascending=False).head(n).reset_index())

    def co_exhibition_graph(self, groups: Optional[Sequence[str]] = None, min_shared: int = 1) -> nx.Graph:
        """Художники групп; ребро = общие выставки, вес = их число."""
        ex = self.exhibitions
        if ex.empty:
            return nx.Graph()
        ex = ex[ex["group"].notna()]
        if groups:
            ex = ex[ex["group"].isin(groups)]
        graph = nx.Graph()
        for artist_id, sub in ex.groupby("artist_id"):
            artist = self.collection.artists[artist_id]
            graph.add_node(artist_id, label=artist.name, group=sub["group"].iloc[0],
                           emigre=bool(sub["emigre"].iloc[0]), exhibitions=sub["exhibition_id"].nunique())
        for _, sub in ex.groupby("exhibition_id"):
            ids = sorted(sub["artist_id"].unique())
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    weight = graph[a][b]["weight"] + 1 if graph.has_edge(a, b) else 1
                    graph.add_edge(a, b, weight=weight)
        weak = [(a, b) for a, b, w in graph.edges(data="weight") if w < min_shared]
        graph.remove_edges_from(weak)
        graph.remove_nodes_from([n for n in list(graph) if graph.degree(n) == 0])
        return graph

    def centrality(self, graph: nx.Graph, n: int = 15) -> pd.DataFrame:
        """Кто связывает сеть: степень и посредничество (betweenness)."""
        if not graph:
            return pd.DataFrame()
        between = nx.betweenness_centrality(graph, weight=None)
        rows = [{"художник": d["label"], "группа": d["group"], "эмигрант": d["emigre"],
                 "выставок": d["exhibitions"], "связей": graph.degree(node),
                 "посредничество": round(between[node], 3)} for node, d in graph.nodes(data=True)]
        return pd.DataFrame(rows).sort_values("посредничество", ascending=False).head(n).reset_index(drop=True)

    # ----------------------------------------------------------------- графики
    def _save(self, fig: plt.Figure, name: str) -> plt.Figure:
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            fig.savefig(self.out_dir / f"{name}.png", dpi=150, bbox_inches="tight")
        return fig

    def _cold_war(self, ax: plt.Axes) -> None:
        if self.show_cold_war:
            ax.axvspan(*COLD_WAR, color="#8a8984", alpha=0.08, zorder=0)
            ax.text(COLD_WAR[0] + 1, 1.0, "холодная война", transform=ax.get_xaxis_transform(),
                    va="top", fontsize=8, color=INK)

    def plot_acquisitions(self, museums: Optional[Sequence[str]] = None, share: bool = False,
                          highlight: Optional[str] = None) -> plt.Figure:
        """Поступления по периодам, столбики с накоплением по группам.

        highlight — регулярка по credit line (например 'Rothschild'): такие работы
        выделяются штриховкой, чтобы один крупный дар не выдавался за «тренд».
        """
        museums = list(museums or self.collection.museums)
        museums = [m for m in museums if not self.artworks.empty and (self.artworks["museum"] == m).any()]
        if not museums:
            print("Нет работ с датами поступления — график пропущен")
            return plt.figure()
        fig, axes = plt.subplots(1, len(museums), figsize=(6.5 * len(museums), 4.2), squeeze=False)
        width = self.bin_size * 0.82
        for ax, museum in zip(axes[0], museums):
            table = self.acquisitions(museum, share=share)
            self._cold_war(ax)
            bottom = pd.Series(0.0, index=table.index)
            highlighted = 0
            for group in self.grouping.names:
                if group not in table or not table[group].any():
                    continue
                values = table[group].astype(float)
                ax.bar(table.index.astype(int) + self.bin_size / 2, values, width=width, bottom=bottom,
                       color=self.colors[group], label=group, edgecolor="white", linewidth=1)
                if highlight and not share:
                    marked = self._grouped_works([museum], [group])
                    marked = self._clip(marked[marked["credit_line"].fillna("").str.contains(
                        highlight, flags=re.IGNORECASE)], "acquisition_year")
                    counts = marked.groupby(self._period(marked["acquisition_year"])).size()\
                                   .reindex(table.index, fill_value=0)
                    highlighted += int(counts.sum())
                    if counts.any():
                        ax.bar(table.index.astype(int) + self.bin_size / 2, counts, width=width,
                               bottom=bottom, color="none", hatch="///", edgecolor="white", linewidth=0)
                bottom = bottom + values
            if highlighted:
                ax.bar([int(table.index[0])], [0], color="#8a8984", hatch="///", edgecolor="white",
                       label=f"из них {highlight}: {highlighted}")
            ylabel = "% всех поступлений музея" if share else "работ"
            _style(ax, f"{museum}: поступления по {self.bin_size}-летиям", "год поступления", ylabel)
            _legend(ax)
        fig.tight_layout()
        return self._save(fig, f"acquisitions{'_share' if share else ''}")

    def plot_acquisition_types(self, museums: Optional[Sequence[str]] = None,
                               group: Optional[str] = None) -> plt.Figure:
        """Дары и покупки: кто «делает» присутствие группы в музее."""
        museums = [m for m in (museums or self.collection.museums)
                   if not self.artworks.empty and (self.artworks["museum"] == m).any()]
        if not museums:
            return plt.figure()
        colors = {"дар": PALETTE[0], "покупка": PALETTE[1], "другое": MUTED, "нет данных": "#e6e5e0"}
        fig, axes = plt.subplots(1, len(museums), figsize=(6.5 * len(museums), 4.2), squeeze=False)
        for ax, museum in zip(axes[0], museums):
            table = self.acquisition_types(museum, group)
            self._cold_war(ax)
            bottom = pd.Series(0.0, index=table.index)
            for kind in colors:
                if kind in table:
                    ax.bar(table.index.astype(int) + self.bin_size / 2, table[kind], width=self.bin_size * 0.82,
                           bottom=bottom, color=colors[kind], label=kind, edgecolor="white", linewidth=1)
                    bottom = bottom + table[kind]
            who = group or "все группы"
            _style(ax, f"{museum}: дары и покупки ({who})", "год поступления", "работ")
            _legend(ax)
        fig.tight_layout()
        return self._save(fig, "acquisition_types")

    def plot_exhibitions(self, group: Optional[str] = None) -> plt.Figure:
        table = self.exhibitions_by_year(group)
        fig, ax = plt.subplots(figsize=(10, 4))
        if table.empty:
            print("Нет данных о выставках — график пропущен")
            return fig
        self._cold_war(ax)
        ax.bar(table.index, table["все выставки"], width=0.8, color=MUTED, label="все выставки")
        color = self.colors.get(group, PALETTE[0])
        ax.bar(table.index, table["с художниками группы"], width=0.8, color=color,
               label=f"с художниками: {group or 'любой группы'}")
        _style(ax, "Выставки по годам", "год открытия", "выставок")
        _legend(ax)
        fig.tight_layout()
        return self._save(fig, f"exhibitions{'_' + group if group else ''}")

    def plot_network(self, groups: Optional[Sequence[str]] = None, min_shared: int = 2,
                     html: Optional[str | Path] = None, label_top: int = 12) -> plt.Figure | Path:
        """Сеть совместных выставок. html='network.html' — интерактивная версия (нужен pyvis)."""
        graph = self.co_exhibition_graph(groups, min_shared)
        if not graph:
            print("Сеть пуста: нет выставок или порог min_shared слишком высокий")
            return plt.figure()
        if html:
            return self._network_html(graph, Path(html))
        # на статичной картинке — только главная связная компонента, иначе мелкие «островки» сжимают её
        dropped = len(graph)
        graph = graph.subgraph(max(nx.connected_components(graph), key=len)).copy()
        dropped -= len(graph)
        fig, ax = plt.subplots(figsize=(10, 8))
        pos = nx.kamada_kawai_layout(graph)
        weights = [graph[a][b]["weight"] for a, b in graph.edges]
        nx.draw_networkx_edges(graph, pos, ax=ax, width=[0.4 + w * 0.3 for w in weights],
                               edge_color="#bdbcb6", alpha=0.7)
        for group in self.grouping.names:
            nodes = [n for n, d in graph.nodes(data=True) if d["group"] == group]
            if nodes:
                nx.draw_networkx_nodes(graph, pos, nodelist=nodes, ax=ax, node_color=self.colors[group],
                                       node_size=[25 + 12 * graph.nodes[n]["exhibitions"] for n in nodes],
                                       edgecolors=["#0b0b0b" if graph.nodes[n]["emigre"] else "white"
                                                   for n in nodes], linewidths=1.2, label=group)
        between = nx.betweenness_centrality(graph)
        top = sorted(graph, key=between.get, reverse=True)[:label_top]   # подписываем «посредников»
        for node in top:
            x, y = pos[node]
            ax.annotate(graph.nodes[node]["label"], (x, y), xytext=(6, 6), textcoords="offset points",
                        fontsize=8, color="#0b0b0b",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
        note = f"; не показано вне основной сети: {dropped}" if dropped else ""
        ax.set_title(f"Совместные выставки (≥{min_shared} общих), размер узла — число выставок,\nчёрная обводка — эмигрант{note}",
                     loc="left", fontsize=11)
        ax.legend(frameon=False, fontsize=8, markerscale=0.6)
        ax.axis("off")
        fig.tight_layout()
        return self._save(fig, "network")

    def _network_html(self, graph: nx.Graph, path: Path) -> Path:
        try:
            from pyvis.network import Network
        except ImportError as error:
            raise ImportError("Для интерактивной сети: pip install pyvis") from error
        # cdn_resources="in_line": JS/CSS встроены прямо в HTML. Нужно, чтобы файл
        # был полностью самодостаточным — без папки lib/ рядом и без обращений к внешним
        # CDN, которые блокируются в песочнице вывода Colab (там "remote" не грузится,
        # и сеть выглядит статичной, без интерактивности).
        net = Network(height="720px", width="100%", bgcolor="#fcfcfb", font_color="#0b0b0b",
                      cdn_resources="in_line")
        # Старт — та же раскладка, что и у статичного PNG, чтобы узлы не начинали
        # из случайных точек. Физику оставляем включённой, но сильно "задемпфированной"
        # (damping) и с маленьким timestep — вместо резкого дрожания получается
        # медленное, плавное доведение до равновесия.
        pos = nx.kamada_kawai_layout(graph, scale=600)
        for node, d in graph.nodes(data=True):
            note = ", эмигрант" if d["emigre"] else ""
            x, y = pos[node]
            net.add_node(node, label=d["label"], color=self.colors[d["group"]], size=6 + 2 * d["exhibitions"] ** 0.7,
                         title=f"{d['label']}\n{d['group']}{note}\nвыставок: {d['exhibitions']}",
                         x=float(x), y=float(y))
        net.set_options("""{
            "physics": {
                "enabled": true,
                "solver": "barnesHut",
                "barnesHut": {
                    "gravitationalConstant": -500,
                    "centralGravity": 0.1,
                    "springLength": 120,
                    "springConstant": 0.01,
                    "damping": 0.5,
                    "avoidOverlap": 0.1
                },
                "minVelocity": 0.75,
                "timestep": 0.2,
                "stabilization": {"enabled": false}
            }
        }""")
        for a, b, w in graph.edges(data="weight"):
            # smooth=False: по умолчанию кривизна ребра считается через невидимую
            # вспомогательную точку, которая физически симулируется даже при
            # physics=False на самих узлах — отсюда "дёргающиеся" рёбра. Прямая
            # линия между зафиксированными узлами не нуждается в такой точке.
            net.add_edge(a, b, value=w, title=f"общих выставок: {w}", color="#bdbcb6", smooth=False)
        if self.out_dir and not path.is_absolute():
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / path
        net.write_html(str(path), notebook=False)
        return path
