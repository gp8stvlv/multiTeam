#!/usr/bin/env python3
"""Render the physical three-database ER model directly from SQLAlchemy metadata.

Usage from repository root: python scripts/render_er.py
Graphviz `dot` is required. No application startup or secrets are needed.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql

from services.catalog.models import Base as CatalogBase
from services.users.models import Base as UsersBase
from services.recommendations.models import Base as RecommendationsBase


DATABASES = [
    ("catalog", CatalogBase, "#eaf3ff", "#3975bf"),
    ("users", UsersBase, "#eaf8ef", "#3c8a56"),
    ("recommendations", RecommendationsBase, "#fff3df", "#b67b25"),
]

EMBEDDED = {
    ("menu_imports", "draft_items"): "Embedded DraftItem[]: ingredients, allergens, nutrition and source IDs",
    ("menu_imports", "error"): "Embedded Error",
    ("menu_items", "nutrition"): "Embedded NutritionFacts per portion; unknown values stay null",
    ("menu_items", "allergens"): "Embedded AllergenDeclaration[]; evidence IDs are logical refs",
    ("menu_items", "evidence_ids"): "Embedded logical evidence UUID[]",
    ("diary_snapshots", "data"): "Immutable embedded snapshot: name, menu/dish/restaurant/version IDs, composition, price and nutrition; exact Decimal strings",
    ("recommendation_requests", "payload"): "Embedded request: mode, query, constraints, max_options and prior-context reference",
    ("recommendation_requests", "context"): "Embedded previous request context",
    ("recommendation_requests", "result"): "Embedded RecommendationResult: options[] with items[], restaurant snapshot, nutrition totals, reasons and evidence IDs; NOT separate tables",
    ("recommendation_requests", "error"): "Embedded Error",
    ("agent_runs", "data"): "Embedded trace: agent, status, timestamps, tools, evidence IDs, summary and token counts",
}

LOGICAL = [
    ("catalog", "restaurants", "active_menu_version_id", "catalog", "menu_versions", "id", "active menu pointer; no SQL FK"),
    ("catalog", "menu_imports", "published_menu_version_id", "catalog", "menu_versions", "id", "published version pointer; no SQL FK"),
    ("catalog", "menu_items", "evidence_ids", "catalog", "evidence", "id", "IDs inside evidence_ids / allergens / nutrition JSON; no SQL FK"),
    ("users", "dish_feedback", "dish_id", "catalog", "dishes", "id", "Catalog API verifies dish; no cross-DB FK"),
    ("users", "dish_feedback", "recommendation_id", "recommendations", "recommendation_requests", "id", "owned recommendation checked over API; no cross-DB FK"),
    ("users", "diary_snapshots", "data", "catalog", "menu_items", "id", "menu_item_id embedded in immutable JSON snapshot; no cross-DB FK"),
    ("recommendations", "recommendation_requests", "user_id", "users", "users", "id", "JWT / Users API identity; no cross-DB FK"),
    ("recommendations", "feed_items", "user_id", "users", "users", "id", "opt-in Users API context; no cross-DB FK"),
    ("recommendations", "agent_runs", "request_id", "recommendations", "recommendation_requests", "id", "local logical request reference; no SQL FK"),
    ("recommendations", "feed_items", "recommendation_id", "recommendations", "recommendation_requests", "id", "local logical request reference; no SQL FK"),
    ("recommendations", "recommendation_requests", "result", "catalog", "menu_items", "id", "menu IDs embedded in result.options.items JSON; no cross-DB FK"),
]


def quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def lines(value, width=58):
    return "<BR ALIGN='LEFT'/>".join(escape(x) for x in textwrap.wrap(value, width=width))


def node_id(database, table):
    return database + "__" + table


def table_label(database, table, fill, stroke):
    row_html = [f'<TR><TD COLSPAN="3" BGCOLOR="{stroke}"><FONT COLOR="white"><B>{database}.{escape(table.name)}</B></FONT></TD></TR>',
                '<TR><TD><B>column / keys</B></TD><TD><B>PostgreSQL type</B></TD><TD><B>null</B></TD></TR>']
    for column in table.columns:
        keys = []
        if column.primary_key:
            keys.append("PK")
        if column.foreign_keys:
            keys.append("FK")
        if column.unique:
            keys.append("UQ")
        key_text = f" [{', '.join(keys)}]" if keys else ""
        dtype = str(column.type.compile(dialect=postgresql.dialect()))
        note = EMBEDDED.get((table.name, column.name))
        if dtype == "JSON" and not note:
            note = "Embedded JSON value / array"
        if dtype.startswith("VECTOR"):
            note = "Real multilingual model-derived pgvector embedding"
        name = escape(column.name + key_text)
        if keys:
            name = f"<B>{name}</B>"
        row_html.append(f'<TR><TD PORT="{column.name}" ALIGN="LEFT">{name}</TD><TD ALIGN="LEFT">{escape(dtype)}</TD><TD>{"yes" if column.nullable else "no"}</TD></TR>')
        if note:
            row_html.append(f'<TR><TD COLSPAN="3" ALIGN="LEFT"><FONT POINT-SIZE="9" COLOR="#52616e">{lines(note)}</FONT></TD></TR>')
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and len(constraint.columns) > 1:
            text = "UNIQUE (" + ", ".join(x.name for x in constraint.columns) + ")"
            row_html.append(f'<TR><TD COLSPAN="3" ALIGN="LEFT"><FONT POINT-SIZE="10"><B>{escape(text)}</B></FONT></TD></TR>')
    return f'<<TABLE BORDER="1" COLOR="{stroke}" CELLBORDER="0" CELLSPACING="0" CELLPADDING="5" BGCOLOR="{fill}">' + "".join(row_html) + '</TABLE>>'


def make_dot():
    output = ["digraph PhysicalER {", 'graph [rankdir=LR, bgcolor="white", fontname="Helvetica", nodesep=0.40, ranksep=1.20, pad=0.35, splines=polyline, compound=true, label="GastroGuide — actual SQLAlchemy physical schema\nThree independently owned databases; 16 tables\nSolid arrows: SQL foreign keys. Dashed arrows: application references, NO SQL FK.\nJSON result/options/snapshots/trace are embedded values, not tables.", labelloc=t, fontsize=20];',
              'node [shape=plain, fontname="Helvetica", fontsize=11];',
              'edge [fontname="Helvetica", fontsize=9, color="#64748b", arrowsize=0.75];']
    for database, base, fill, stroke in DATABASES:
        output.append(f'subgraph cluster_{database} {{ label={quote(database + " PostgreSQL database — service-owned")}; color="{stroke}"; style="rounded"; penwidth=2;')
        for table in base.metadata.tables.values():
            output.append(f'{node_id(database, table.name)} [label={table_label(database, table, fill, stroke)}];')
        output.append("}")
    for database, base, _, stroke in DATABASES:
        for table in base.metadata.tables.values():
            for column in table.columns:
                for fk in column.foreign_keys:
                    target = fk.column
                    output.append(f'{node_id(database, table.name)}:{column.name} -> {node_id(database, target.table.name)}:{target.name} '
                                  f'[label={quote("SQL FK " + column.name)}, color="{stroke}", penwidth=1.2];')
    for db1, table1, col1, db2, table2, col2, label in LOGICAL:
        output.append(f'{node_id(db1, table1)}:{col1} -> {node_id(db2, table2)}:{col2} '
                      f'[style=dashed, color="#a0a0a0", fontcolor="#717171", constraint=false, label={quote(label)}];')
    output.append("}")
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "er-runtime.svg")
    parser.add_argument("--dot-output", type=Path, help="Optional Graphviz source output")
    args = parser.parse_args()
    source = make_dot()
    if args.dot_output:
        args.dot_output.parent.mkdir(parents=True, exist_ok=True)
        args.dot_output.write_text(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["dot", "-Tsvg", "-o", str(args.output)], input=source, text=True, check=True)
    print(f"Rendered {sum(len(base.metadata.tables) for _, base, _, _ in DATABASES)} SQLAlchemy tables to {args.output}")


if __name__ == "__main__":
    main()
