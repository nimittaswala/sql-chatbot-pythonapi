from __future__ import annotations
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv("Config.env")

SCHEMA_PATH = os.getenv("SCHEMA_PATH", "schema_tree.json")
KEYWORDS_PATH = os.getenv("KEYWORDS_PATH", "keyword_to_tables.json")
SQL_DIALECT = os.getenv("SQL_DIALECT", "mysql")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

app = FastAPI(title="Dental SQL Generator API", version="3.3.0")
router = APIRouter(prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

@router.get("/health")
def health():
    return {
        "status": "ok",
        "schema_path": SCHEMA_PATH,
        "keywords_path": KEYWORDS_PATH,
        "dialect": SQL_DIALECT,
        "model": MODEL,
    }

@router.post("/generate_sql", response_model=GenerateResponse)
def generate_sql(req: GenerateRequest):
    try:
        from api.basic import run_pipeline

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

@router.post("/generate_sql_premium", response_model=GenerateResponse)
def generate_sql_premium(req: GenerateRequest):
    try:
        from api.premium import run_pipeline_premium

        result = run_pipeline_premium(
            question=req.question.strip(),
            schema_path=req.schema_path or SCHEMA_PATH,
            keywords_path=req.keywords_path or KEYWORDS_PATH,
            dialect=req.dialect or SQL_DIALECT,
            model=req.model or MODEL,
        )
        return GenerateResponse(
            sql=result.get("sql", ""),
            explanation=result.get("explanation", ""),
            candidate_tables=result.get("seed_tables", []),
            schema_tables_sent=result.get("final_tables_sent", []),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"generation failed: {e}")

app.include_router(router)