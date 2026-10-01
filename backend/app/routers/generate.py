from fastapi import APIRouter, HTTPException

from app.models.schemas import AnalyzeRequest
from app.services.analyzer import analyze_website
from app.services.generator import generate_frontend

router = APIRouter(prefix="/api", tags=["generate"])


@router.post("/generate")
async def generate(request: AnalyzeRequest):
    try:
        analysis = await analyze_website(str(request.url))
        result = await generate_frontend(analysis)
        return {"url": str(request.url), **result}
    except Exception as e:
        import traceback
        traceback.print_exc()  # full traceback shows up in the uvicorn terminal
        raise HTTPException(status_code=500, detail=f"Generation failed: {type(e).__name__}: {e}")