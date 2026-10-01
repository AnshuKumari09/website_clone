from typing import Optional

from pydantic import BaseModel, HttpUrl


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class ColorPalette(BaseModel):
    primary: Optional[str] = None
    secondary: Optional[str] = None
    background: Optional[str] = None
    text: Optional[str] = None
    all_colors: list[str] = []


class Typography(BaseModel):
    font_families: list[str] = []
    heading_font_size: Optional[str] = None
    body_font_size: Optional[str] = None


class NavItem(BaseModel):
    label: str
    href: Optional[str] = None


class ImageAsset(BaseModel):
    src: str
    alt: Optional[str] = None


class SectionInfo(BaseModel):
    tag: str
    heading: Optional[str] = None
    text_snippet: Optional[str] = None
    image_count: int = 0


class AnalysisResult(BaseModel):
    url: str
    title: Optional[str] = None
    screenshot_base64: Optional[str] = None

    nav_items: list[NavItem] = []
    sections: list[SectionInfo] = []
    images: list[ImageAsset] = []

    # NEW: structured Hacker News stories
    stories: list[dict] = []

    colors: ColorPalette = ColorPalette()
    typography: Typography = Typography()

    raw_dom_summary: Optional[str] = None
    simplified_html: Optional[str] = None