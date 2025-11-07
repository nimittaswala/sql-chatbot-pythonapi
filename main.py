from __future__ import annotations
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from premium import run_pipeline_premium

# --- pull config for API layer only ---
load_dotenv("Config.env")
SCHEMA_PATH   = os.getenv("SCHEMA_PATH", "schema_tree.json")
KEYWORDS_PATH = os.getenv("KEYWORDS_PATH", "keyword_to_tables.json")
SQL_DIALECT   = os.getenv("SQL_DIALECT", "mysql")
MODEL         = os.getenv("OPENAI_MODEL", "gpt-5")

# --- import the logic layer ---
from basic import run_pipeline 


# --- FastAPI app ---
app = FastAPI(title="Dental SQL Generator API", version="3.3.0")

# Allow localhost (any port) and chat.local (any port), http or https
ALLOWED_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|^https?://chat\.local(:\d+)?$"

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

class GenerateRequest(BaseModel):
    question: str = Field(..., description="Natural-language analytics question.")
    schema_path: Optional[str] = None
    keywords_path: Optional[str] = None
    dialect: Optional[str] = None
    model: Optional[str] = None

class GenerateResponse(BaseModel):
    sql: str
    explanation: str
    candidate_tables: List[str]
    schema_tables_sent: List[str]

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/generate_sql", response_model=GenerateResponse)
def generate_sql(req: GenerateRequest):
    try:
        result = run_pipeline(
            question=req.question.strip(),
            schema_path=req.schema_path or SCHEMA_PATH,
            keywords_path=req.keywords_path or KEYWORDS_PATH,
            dialect=req.dialect or SQL_DIALECT,
            model=req.model or MODEL,
        )
        return GenerateResponse(**result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"generation failed: {e}")

# --- replace your existing /generate_sql_premium route with this
@app.post("/generate_sql_premium", response_model=GenerateResponse)
def generate_sql_premium(req: GenerateRequest):
    try:
        result = run_pipeline_premium(
            question=req.question.strip(),
            schema_path=req.schema_path or SCHEMA_PATH,
            keywords_path=req.keywords_path or KEYWORDS_PATH,
            dialect=req.dialect or SQL_DIALECT,
            model=req.model or MODEL,
        )
        return GenerateResponse(
            sql=result.get("sql",""),
            explanation=result.get("explanation",""),
            candidate_tables=result.get("seed_tables",[]),
            schema_tables_sent=result.get("final_tables_sent",[]),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"generation failed: {e}")

# @app.post("/generate_sql_premium", response_model=GenerateResponse)
# def generate_sql_premium(req: GenerateRequest, preview: bool = False):  # <--- add query param ?preview=true
#     result = run_pipeline_premium(
#         question=req.question.strip(),
#         schema_path=req.schema_path or SCHEMA_PATH,
#         keywords_path=req.keywords_path or KEYWORDS_PATH,
#         dialect=req.dialect or SQL_DIALECT,
#         model=req.model or MODEL,
#         preview_only=preview,  # <--- pass through
#     )
#     return GenerateResponse(
#         sql=result.get("sql",""),
#         explanation=result.get("explanation",""),
#         candidate_tables=result.get("seed_tables",[]),
#         schema_tables_sent=result.get("final_tables_sent",[]),
#     )



