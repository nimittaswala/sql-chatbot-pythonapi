from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Set
from collections import defaultdict, deque

from api.basic import (
    load_json,
    slice_schema,
    extract_candidate_tables,
    ask_model,
    resolve_path,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_KEYWORDS_PATH,
    DEFAULT_SQL_DIALECT,
    DEFAULT_MODEL,
)

PREMIUM_MODEL = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
PREMIUM_SQL_DIALECT = os.getenv("SQL_DIALECT", DEFAULT_SQL_DIALECT)
PREMIUM_SCHEMA_PATH = os.getenv("SCHEMA_PATH", DEFAULT_SCHEMA_PATH)
PREMIUM_KEYWORDS = os.getenv("KEYWORDS_PATH", DEFAULT_KEYWORDS_PATH)

PREMIUM_MAX_SEEDS: Optional[int] = None
PREMIUM_MAX_RELATED: Optional[int] = None
PREMIUM_MAX_HOPS: int = int(os.getenv("PREMIUM_MAX_HOPS", "1"))

PREMIUM_SOFT_RULES_OVERLAY: Dict[str, str] = {
    # "ApptTypeNum": "appointmenttype",
    # "GuarNum": "patient",
}

W_FK_OUT = 1.00
W_FK_IN = 1.00
W_SOFT = 1.00
W_NAME = 1.00
W_HOP_PENALTY = 0.10

SOFT_LINK_RULES_BASE: Dict[str, str] = {
    "PatNum": "patient",
    "AptNum": "appointment",
    "ProvNum": "provider",
    "ClinicNum": "clinic",
    "OperatoryNum": "operatory",
    "OpNum": "operatory",
    "ScheduleNum": "schedule",
    "ProcNum": "procedurelog",
    "ProcCodeNum": "procedurecode",
    "ProcCode": "procedurecode",
    "CodeNum": "procedurecode",
    "ClaimNum": "claim",
    "ClaimProcNum": "claimproc",
    "PlanNum": "insplan",
    "InsSubNum": "inssub",
    "CarrierNum": "carrier",
    "BenefitNum": "benefit",
    "PayNum": "payment",
    "PaymentNum": "payment",
    "SplitNum": "paysplit",
    "PayPlanNum": "payplan",
    "AdjNum": "adjustment",
    "TreatPlanNum": "treatplan",
    "ReferralNum": "referral",
    "TaskNum": "task",
    "LabCaseNum": "labcase",
    "ImageCategoryNum": "imagecategory",
    "RxNum": "rx",
    "DefNum": "definition",
    "UserNum": "userod",
}


def _load_schema_as_dict(schema_path: str | Path) -> Dict[str, Dict[str, Any]]:
    data = load_json(schema_path)
    tables: Dict[str, Dict[str, Any]] = {}
    for t in data.get("tables", []):
        name = t.get("name") or t.get("table")
        if name:
            tables[str(name)] = t
    return tables


def _get_pk(table_obj: Dict[str, Any], table_name: str) -> Optional[str]:
    for c in table_obj.get("columns", []):
        if isinstance(c.get("summary", ""), str) and "Primary key" in c["summary"]:
            return c.get("name")
    guess = f"{table_name}Num"
    cols = {c.get("name") for c in table_obj.get("columns", [])}
    return guess if guess in cols else None


def _build_fk_indexes(tables: Dict[str, Dict[str, Any]]):
    fk_out: Dict[str, Set[str]] = defaultdict(set)
    fk_in: Dict[str, Set[str]] = defaultdict(set)
    fk_edges: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
    pks = {t: (_get_pk(obj, t) or "") for t, obj in tables.items()}

    for tname, tobj in tables.items():
        for c in tobj.get("columns", []):
            fk = (c.get("fk") or "").strip()
            if not fk:
                continue
            ref = fk.split()[0].split("(")[0].strip()
            if ref and ref in tables:
                fk_out[tname].add(ref)
                fk_in[ref].add(tname)
                fk_edges[(tname, ref)].append((c.get("name"), pks.get(ref, "")))
    return fk_out, fk_in, fk_edges, pks


def _infer_soft_edges(
    tables: Dict[str, Dict[str, Any]],
    pks: Dict[str, str],
    soft_rules_overlay: Optional[Dict[str, str]] = None,
):
    rules = dict(SOFT_LINK_RULES_BASE)
    if soft_rules_overlay:
        rules.update(soft_rules_overlay)

    soft_edges: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
    for tname, tobj in tables.items():
        colnames = [c.get("name") for c in tobj.get("columns", []) if c.get("name")]
        for col in colnames:
            if col in rules:
                tgt = rules[col]
                if tgt in tables:
                    soft_edges[(tname, tgt)].append((col, pks.get(tgt, "")))

            if col and col.lower().endswith("num"):
                base = col[:-3].lower()
                for candidate in tables.keys():
                    if candidate.lower() == base:
                        soft_edges[(tname, candidate)].append((col, pks.get(candidate, "")))
                        break
    return soft_edges


def expand_related_tables(
    seeds: List[str],
    schema_path: str | Path = PREMIUM_SCHEMA_PATH,
    max_tables: Optional[int] = PREMIUM_MAX_RELATED,
    max_hops: int = PREMIUM_MAX_HOPS,
    soft_rules_overlay: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    schema_path_resolved = resolve_path(schema_path, DEFAULT_SCHEMA_PATH)
    tables = _load_schema_as_dict(schema_path_resolved)

    seeds = [s for s in seeds if s in tables]
    if not seeds:
        return {
            "ranked_tables": [],
            "reasons": {},
            "join_edges": [],
            "note": "No valid seed tables provided.",
        }

    fk_out, fk_in, fk_edges, pks = _build_fk_indexes(tables)
    soft_edges = _infer_soft_edges(tables, pks, soft_rules_overlay)

    score: Dict[str, float] = defaultdict(float)
    reason_trail: Dict[str, List[str]] = defaultdict(list)

    for s in seeds:
        score[s] += 0.5
        reason_trail[s].append("seed")

    q = deque([(s, 0) for s in seeds])
    seen: Set[str] = set(seeds)

    while q:
        node, hop = q.popleft()
        if hop >= max_hops:
            continue

        for nb in fk_out.get(node, set()):
            if nb not in seen:
                seen.add(nb)
                q.append((nb, hop + 1))
            gain = max(0.0, W_FK_OUT - hop * W_HOP_PENALTY)
            if gain > 0:
                score[nb] += gain
                for fc, tc in fk_edges.get((node, nb), []):
                    reason_trail[nb].append(f"fk_out:{node}.{fc}->{nb}.{tc} (+{gain:.2f})")

        for nb in fk_in.get(node, set()):
            if nb not in seen:
                seen.add(nb)
                q.append((nb, hop + 1))
            gain = max(0.0, W_FK_IN - hop * W_HOP_PENALTY)
            if gain > 0:
                score[nb] += gain
                for fc, tc in fk_edges.get((nb, node), []):
                    reason_trail[nb].append(f"fk_in:{nb}.{fc}->{node}.{tc} (+{gain:.2f})")

        for (src, tgt), pairs in soft_edges.items():
            if src == node:
                nb = tgt
                if nb not in seen:
                    seen.add(nb)
                    q.append((nb, hop + 1))
                gain = max(0.0, W_SOFT - hop * W_HOP_PENALTY)
                if gain > 0:
                    score[nb] += gain
                    for fc, tc in pairs:
                        reason_trail[nb].append(f"soft:{src}.{fc}->{tgt}.{tc} (+{gain:.2f})")
            elif tgt == node:
                nb = src
                if nb not in seen:
                    seen.add(nb)
                    q.append((nb, hop + 1))
                gain = max(0.0, W_SOFT - hop * W_HOP_PENALTY)
                if gain > 0:
                    score[nb] += gain
                    for fc, tc in pairs:
                        reason_trail[nb].append(f"soft:{src}.{fc}->{tgt}.{tc} (+{gain:.2f})")

    seed_tokens = {s.lower() for s in seeds}
    for tname in tables.keys():
        for tok in seed_tokens:
            if tok != tname.lower() and tok in tname.lower():
                score[tname] += W_NAME
                reason_trail[tname].append(f"name_contains:{tok} (+{W_NAME:.2f})")

    ranked = sorted(score.items(), key=lambda kv: kv[1], reverse=True)
    ranked = [t for t, sc in ranked if sc > 0]
    if max_tables is not None:
        ranked = ranked[:max_tables]

    join_edges: List[Dict[str, Any]] = []
    selected = set(ranked)
    for (a, b), pairs in fk_edges.items():
        if a in selected and b in selected:
            for fc, tc in pairs:
                join_edges.append(
                    {"type": "fk", "from_table": a, "from_col": fc, "to_table": b, "to_col": tc}
                )
    for (a, b), pairs in soft_edges.items():
        if a in selected and b in selected:
            for fc, tc in pairs:
                join_edges.append(
                    {"type": "soft", "from_table": a, "from_col": fc, "to_table": b, "to_col": tc}
                )

    reasons = {
        t: {"score": round(score.get(t, 0.0), 4), "reasons": reason_trail.get(t, [])}
        for t in ranked
    }
    return {"ranked_tables": ranked, "reasons": reasons, "join_edges": join_edges}


def run_pipeline_premium(
    question: str,
    schema_path: str | Path = PREMIUM_SCHEMA_PATH,
    keywords_path: str | Path = PREMIUM_KEYWORDS,
    dialect: str = PREMIUM_SQL_DIALECT,
    model: str = PREMIUM_MODEL,
    max_seed_tables: Optional[int] = PREMIUM_MAX_SEEDS,
    max_related_tables: Optional[int] = PREMIUM_MAX_RELATED,
    max_hops: int = PREMIUM_MAX_HOPS,
    soft_rules_overlay: Optional[Dict[str, str]] = None,
    preview_only: Optional[bool] = None,
) -> Dict[str, Any]:
    schema_path_resolved = resolve_path(schema_path, DEFAULT_SCHEMA_PATH)
    keywords_path_resolved = resolve_path(keywords_path, DEFAULT_KEYWORDS_PATH)

    kwmap_raw = load_json(keywords_path_resolved)
    kwmap: Dict[str, List[str]] = {}
    for k, v in kwmap_raw.items():
        k2 = str(k).lower()
        if isinstance(v, list):
            kwmap[k2] = [str(x) for x in v]
        elif isinstance(v, str):
            kwmap[k2] = [v]

    seeds = extract_candidate_tables(
        question,
        kwmap,
        max_tables=(max_seed_tables if max_seed_tables is not None else 10**9),
    )
    if not seeds:
        seeds = ["patient", "appointment", "procedurelog", "claim", "insplan"]

    rel = expand_related_tables(
        seeds,
        schema_path=schema_path_resolved,
        max_tables=max_related_tables,
        max_hops=max_hops,
        soft_rules_overlay=soft_rules_overlay or PREMIUM_SOFT_RULES_OVERLAY,
    )

    final_tables = list(dict.fromkeys(seeds + rel["ranked_tables"]))

    schema = load_json(schema_path_resolved)
    sliced = slice_schema(schema, final_tables, cap=10**9)

    answer = ask_model(sliced, question, dialect, model)
    sql, explanation = answer, ""
    parts = re.split(r"\bEXPLANATION:\s*", answer, flags=re.IGNORECASE)
    if len(parts) >= 2:
        sql = parts[0].strip()
        explanation = parts[1].strip()

    return {
        "sql": sql,
        "explanation": explanation,
        "seed_tables": seeds,
        "expanded_tables": rel.get("ranked_tables", []),
        "final_tables_sent": [t["table"] for t in sliced.get("tables", [])],
        "reasons": rel.get("reasons", {}),
        "join_edges": rel.get("join_edges", []),
    }