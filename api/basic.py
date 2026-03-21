
from __future__ import annotations
import os, re, json
from pathlib import Path
from typing import Any, Dict, List
from dotenv import load_dotenv
from openai import OpenAI

# --- Environment setup ---
load_dotenv("Config.env")
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in Config.env or environment.")

DEFAULT_SCHEMA_PATH   = os.getenv("SCHEMA_PATH", "schema_tree.json")
DEFAULT_KEYWORDS_PATH = os.getenv("KEYWORDS_PATH", "keyword_to_tables.json")
DEFAULT_SQL_DIALECT   = os.getenv("SQL_DIALECT", "mysql")
DEFAULT_MODEL         = os.getenv("OPENAI_MODEL", "gpt-5")

client = OpenAI(api_key=API_KEY)

# --- Helper functions ---
def load_json(path: str | Path) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def chunk_str(s: str, n: int = 280_000) -> List[str]:
    return [s[i:i+n] for i in range(0, len(s), n)]

def normalize_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[\t\n\r]+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()

def to_list_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize various schema shapes to a unified list format."""
    t = schema.get("tables", {})
    if isinstance(t, dict):
        out: List[Dict[str, Any]] = []
        for tname, tinfo in t.items():
            tinfo = tinfo if isinstance(tinfo, dict) else {}
            cols_obj = tinfo.get("columns", {})
            cols_list: List[Dict[str, Any]] = []
            if isinstance(cols_obj, dict):
                for cname, cinfo in cols_obj.items():
                    node = {"name": str(cname)}
                    if isinstance(cinfo, dict):
                        node.update(cinfo)
                    cols_list.append(node)
            elif isinstance(cols_obj, list):
                for c in cols_obj:
                    if isinstance(c, dict):
                        if "name" not in c and "Name" in c:
                            c = {"name": c["Name"], **{k:v for k,v in c.items() if k!="Name"}}
                        if "name" in c:
                            cols_list.append(c)
            out.append({"table": str(tname), "columns": cols_list})
        return {"tables": out}

    if isinstance(t, list):
        out: List[Dict[str, Any]] = []
        for item in t:
            if isinstance(item, dict):
                tbl = item.get("table") or item.get("name") or item.get("Table") or item.get("Name")
                if not tbl:
                    continue
                cols = item.get("columns") or item.get("Columns") or []
                norm_cols: List[Dict[str, Any]] = []
                if isinstance(cols, dict):
                    for cname, cinfo in cols.items():
                        node = {"name": str(cname)}
                        if isinstance(cinfo, dict):
                            node.update(cinfo)
                        norm_cols.append(node)
                elif isinstance(cols, list):
                    for c in cols:
                        if isinstance(c, dict):
                            if "name" not in c and "Name" in c:
                                c = {"name": c["Name"], **{k:v for k,v in c.items() if k!="Name"}}
                            if "name" in c:
                                norm_cols.append(c)
                out.append({"table": str(tbl), "columns": norm_cols})
            elif isinstance(item, str):
                out.append({"table": item, "columns": []})
        return {"tables": out}
    return {"tables": []}

def slice_schema(schema: Dict[str, Any], tables_needed: List[str], cap: int = 25) -> Dict[str, Any]:
    norm = to_list_schema(schema)
    all_tables = {tobj.get("table"): tobj for tobj in norm.get("tables", []) if isinstance(tobj, dict)}
    if not tables_needed:
        return {"tables": list(all_tables.values())[:cap]}
    out = [all_tables[name] for name in tables_needed if name in all_tables]
    if not out:
        out = list(all_tables.values())
    return {"tables": out[:cap]}

def extract_candidate_tables(question: str, kwmap: Dict[str, List[str]], max_tables: int = 12) -> List[str]:
    q = normalize_text(question)
    keywords = sorted(kwmap.keys(), key=lambda k: len(k), reverse=True)
    score: Dict[str, int] = {}
    tokens = set(re.findall(r"[a-z0-9]+", q))
    for kw in keywords:
        tables = kwmap.get(kw, [])
        if not isinstance(tables, list):
            continue
        if " " in kw and kw in q:
            for t in tables:
                score[t] = score.get(t, 0) + max(3, len(kw)//5)
        if " " not in kw and kw in tokens:
            for t in tables:
                score[t] = score.get(t, 0) + 1
    ranked = sorted(score.items(), key=lambda x: x[1], reverse=True)
    return [t for t, _ in ranked][:max_tables]

SYSTEM_SQL = """You are an expert Open Dental SQL generator.
- SQL dialect: {dialect}.
- Generate exactly ONE SQL query that answers the task.
- Use ONLY tables/columns present in the provided schema slice.
- Prefer readable aliases and explicit JOINs.
- Avoid SELECT *; list columns explicitly when practical.
- If crucial columns are missing, clearly say what is missing and STOP.
After the SQL, add a blank line, then 'EXPLANATION:' followed by 1–2 concise sentences describing what the query returns.
Return plain text only (SQL first, then the EXPLANATION block)."""

def build_messages(slice_: Dict[str, Any], question: str, dialect: str) -> List[Dict[str, str]]:
    schema_str = json.dumps(slice_, ensure_ascii=False)
    chunks = chunk_str(schema_str)
    msgs: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_SQL.format(dialect=dialect)}]
    for i, ch in enumerate(chunks, start=1):
        msgs.append({"role": "user", "content": f"-- SCHEMA SLICE {i}/{len(chunks)} --\n{ch}"})
    msgs.append({"role": "user", "content": f"Task:\n{question}\n\nReturn SQL first, then an EXPLANATION block."})
    return msgs

def ask_model(slice_: Dict[str, Any], question: str, dialect: str, model: str) -> str:
    resp = client.chat.completions.create(
        model=model, messages=build_messages(slice_, question, dialect)
    )
    return resp.choices[0].message.content.strip()

def run_pipeline(
    question: str,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    keywords_path: str | Path = DEFAULT_KEYWORDS_PATH,
    dialect: str = DEFAULT_SQL_DIALECT,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    schema = load_json(schema_path)
    kwmap_raw = load_json(keywords_path)
    kwmap: Dict[str, List[str]] = {}
    for k, v in kwmap_raw.items():
        k2 = str(k).lower()
        if isinstance(v, list):
            kwmap[k2] = [str(x) for x in v]
        elif isinstance(v, str):
            kwmap[k2] = [v]
    tables = extract_candidate_tables(question, kwmap)
    sliced = slice_schema(schema, tables)
    answer = ask_model(sliced, question, dialect, model)
    sql, explanation = answer, ""
    parts = re.split(r"\bEXPLANATION:\s*", answer, flags=re.IGNORECASE)
    if len(parts) >= 2:
        sql = parts[0].strip()
        explanation = parts[1].strip()
    return {
        "sql": sql,
        "explanation": explanation,
        "candidate_tables": tables,
        "schema_tables_sent": [t["table"] for t in sliced.get("tables", [])],
    }
