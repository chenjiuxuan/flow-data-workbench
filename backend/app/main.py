"""FastAPI 入口：uvicorn app.main:app --port 8000"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import NodeRecord
from .services.demo_seed import demo_drafts
from .services.merge import merge_drafts
from .api import router


def seed_if_empty() -> None:
    session: Session = SessionLocal()
    try:
        if session.query(NodeRecord).count() == 0:
            nodes, edges = demo_drafts()
            merge_drafts(session, "demo", nodes, edges)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_if_empty()
    yield


app = FastAPI(title="数据流梳理平台", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)
