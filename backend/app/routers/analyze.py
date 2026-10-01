from fastapi import APIRouter, HTTPException

from app.models.schemas import AnalyzeRequest, AnalysisResult
from app.services.analyzer import analyze_website

router = APIRouter(prefix="/api", tags=["analyze"])


@router.post("/analyze", response_model=AnalysisResult)
async def analyze(request: AnalyzeRequest):
    try:
        result = await analyze_website(str(request.url))
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to analyze website: {type(e).__name__}: {e}")