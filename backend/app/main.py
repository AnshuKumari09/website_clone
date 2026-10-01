from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import analyze, generate

app = FastAPI(
    title="Website Cloning Agent API",
    description="AI Agent that analyzes a public website and regenerates its frontend as React/Next.js code.",
    version="0.1.0",
)

# Allow the local Next.js frontend (default dev port) to call this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    """Simple liveness check to confirm the API is up before wiring anything else."""
    return {"status": "ok", "service": "website-cloning-agent-backend"}


app.include_router(analyze.router)
app.include_router(generate.router)