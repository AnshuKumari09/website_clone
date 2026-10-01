import base64
import io
import json
import re
import shutil
from pathlib import Path
from groq import AsyncGroq
from PIL import Image

from app.config import settings
from app.models.schemas import AnalysisResult
from app.services.validator import validate_build

client = AsyncGroq(api_key=settings.groq_api_key, timeout=180)

MAX_FIX_ATTEMPTS = 3

VISION_PROMPT = """You are a senior UI engineer. Look at this website screenshot and describe it
for a developer who must rebuild it in React. Be concise (max ~350 words). Cover:
1. Sections from top to bottom (navbar, hero, features, etc.) with one line each
2. Layout of each section (columns, alignment, card grids, image placement)
3. Overall visual style (dark/light, rounded corners, shadows, spacing density)
Do not transcribe all text. Focus on structure and style."""

CODEGEN_SYSTEM = """You are an expert frontend engineer. You rebuild websites as Next.js code.

Output ONLY the raw source code of src/app/page.tsx. No JSON, no markdown fences, no explanation.
The first character of your reply must be the first character of the file.

Rules:
- Everything lives in ONE file: src/app/page.tsx. Put every section (nav, hero, features, list, footer)
  as separate functions in that file, then render them all in the default export.
- Output EXACTLY ONE file: src/app/page.tsx. Put every section (nav, hero, features, list, footer)
  as separate functions inside that same file, then render them all in the default export.
- Next.js App Router, TypeScript, Tailwind CSS. No other libraries, no next/image.
- If any section needs interactivity (mobile menu toggle, etc.) put "use client" as line 1 of the file.
- Use the REAL text, links and image URLs from the input. Never invent content.
- CRITICAL - stay short: for long repeated lists, hardcode EXACTLY 6 DIFFERENT items copied from the
  simplified_html (different titles, urls, points, authors, ids). NEVER repeat the same item twice.
  If the input has fewer than 6 distinct items, output only those.
- Set the base font size from base_font_size on the outermost wrapper (e.g. text-[13px]) and keep
  text small like the original. Outermost wrapper: min-h-screen bg-[<colors.background>] text-[<colors.text>].
  Use the nav's real bg color from simplified_html for the nav bar (full width).
- Style hints in the input's data-s attributes (bg, color, fs, fw, p, d, flex, br, ta) map to Tailwind
  arbitrary values, e.g. bg-[#0a0a0a] text-[#ffffff] text-[48px]. Convert rgb() to hex.
- colors.background and colors.text (top-level input) are the page's real background/body-text colors —
  apply colors.background to the outermost wrapper div and colors.text as the base text color.
  colors.primary/secondary are accent colors for links/buttons/headers only, never the page background.
- Exactly ONE <nav> and ONE footer for the whole page. Never add a second/duplicate nav, a floating
  bottom bar, or any extra fixed/sticky positioned element.
- Fully responsive, mobile-first (md:, lg: breakpoints).
- Keep the code compact: minimal whitespace, no comments, short but clear variable names.
- Semantic HTML, valid TSX only.
- No comments, no blank lines, no unused imports. Reuse shared Tailwind class strings via const variables.
- Your ENTIRE reply must be one valid JSON object and nothing else — no markdown fences, no text before
  or after. Inside the "content" string, escape every double-quote as \\" and every newline as \\n."""

FIX_SYSTEM = """You fix build errors in a Next.js (App Router, TypeScript, Tailwind) project.
Everything lives in ONE file: src/app/page.tsx.

Output ONLY the complete corrected raw source of src/app/page.tsx. No JSON, no markdown fences, no explanation.

Rules:
- Return the whole file, not a diff or snippet.
- Fix only the reported errors. Do not remove content or restyle anything.
- Escape quotes/apostrophes in JSX text (&apos; and &quot;).
- If any part uses hooks or event handlers, "use client" must be line 1 of the file."""


def _prepare_screenshot(b64: str, width: int = 1024, max_height: int = 4000) -> str:
    """Shrink full-page screenshot so it fits Groq's image limits and saves tokens."""
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    img = img.resize((width, int(img.height * width / img.width)))
    img = img.crop((0, 0, width, min(img.height, max_height)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


async def describe_layout(screenshot_b64: str | None) -> str:
    """Vision stage. Optional: if it fails, codegen still works from the simplified HTML."""
    if not screenshot_b64:
        return ""
    try:
        img = _prepare_screenshot(screenshot_b64)
        resp = await client.chat.completions.create(
            model=settings.vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
                    ],
                }
            ],
            max_tokens=2500,
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    except Exception as e:
        print(f"[vision stage skipped] {e}")
        return ""


def _extract_code(raw: str) -> str:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    m = re.search(r"```(?:tsx|typescript|ts|jsx|javascript)?\s*\n(.*?)```", raw, flags=re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        raw = re.sub(r"^```\w*\s*|\s*```$", "", raw)  # unclosed fence
    return raw.strip()


def _to_files(raw: str) -> list[dict]:
    code = _extract_code(raw)
    if "export default" not in code:
        raise ValueError("Model output has no default export (likely truncated)")
    return [{"path": "src/app/page.tsx", "content": code}]


async def generate_files(analysis: AnalysisResult, layout_notes: str) -> list[dict]:
    user_payload = {
        "url": analysis.url,
        "title": analysis.title,
        "colors": analysis.colors.model_dump(),
        "typography": analysis.typography.model_dump(),
        "nav_items": [n.model_dump() for n in analysis.nav_items],
        "layout_notes_from_screenshot": layout_notes[:500],
        "simplified_html": _compact(analysis.simplified_html or "")[:6000],
        "base_font_size": analysis.typography.body_font_size,
    }
    resp = await client.chat.completions.create(
        model=settings.code_model,
        messages=[
            {"role": "system", "content": CODEGEN_SYSTEM},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
        max_tokens=6000,
        temperature=0.2,
    )
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise ValueError("Model output truncated by max_tokens; raise it or shorten the input")
    return _to_files(choice.message.content or "")


def read_generated_files() -> list[dict]:
    root = Path(settings.frontend_dir).resolve()
    paths = [root / "src/app/page.tsx", *sorted((root / "src/components").glob("*.tsx"))]
    return [
        {"path": p.relative_to(root).as_posix(), "content": p.read_text(encoding="utf-8")}
        for p in paths
        if p.exists()
    ]

def _compact(html: str) -> str:
    html = re.sub(r"d:(?:inline|block|table-cell|table-row|table|inline-block);?", "", html)
    html = re.sub(r"ta:left;?|fw:400;?", "", html)
    return html


async def fix_files(build_output: str) -> list[dict]:
    payload = {"build_output": build_output, "files": read_generated_files()}
    resp = await client.chat.completions.create(
        model=settings.code_model,
        messages=[
            {"role": "system", "content": FIX_SYSTEM},
            {"role": "user", "content": json.dumps(payload)},
        ],
        max_tokens=6000,
        temperature=0.1,
    )
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise ValueError("Fix output truncated by max_tokens")
    return _to_files(choice.message.content or "")

CLIENT_HINTS = re.compile(
    r"\b(useState|useEffect|useRef|useReducer|useContext|useCallback|useMemo|useRouter)\b"
    r"|\bon(Click|Change|Submit|Input|KeyDown|MouseEnter|MouseLeave)\s*="
)

def _ensure_use_client(content: str) -> str:
    body = re.sub(r"^\s*['\"]use client['\"];?\s*", "", content)  # drop misplaced/duplicate directive
    if CLIENT_HINTS.search(body):
        return '"use client";\n' + body
    return body

def write_files(files: list[dict], clean: bool = True) -> list[str]:
    """Write generated files into the Next.js project. Path allow-list blocks anything unsafe."""
    root = Path(settings.frontend_dir).resolve()
    comp_dir = root / "src" / "components"
    if clean and comp_dir.exists():
        shutil.rmtree(comp_dir)  # remove stale components from the previous run
    comp_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for f in files:
        rel = f["path"].replace("\\", "/")
        allowed = rel == "src/app/page.tsx" or re.fullmatch(r"src/components/[A-Za-z0-9_]+\.tsx", rel)
        if not allowed:
            print(f"[skipped disallowed path] {rel}")
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _ensure_use_client(f["content"]) if rel.endswith(".tsx") else f["content"]
        target.write_text(content, encoding="utf-8")


async def generate_frontend(analysis: AnalysisResult) -> dict:
    layout_notes = await describe_layout(analysis.screenshot_base64)
    files = await generate_files(analysis, layout_notes)
    written = write_files(files)

    attempts = 0
    ok, output = await validate_build()
    while not ok and attempts < MAX_FIX_ATTEMPTS:
        attempts += 1
        print(f"[build failed] auto-fix attempt {attempts}")
        try:
            fixed = await fix_files(output)
            write_files(fixed, clean=False)
        except Exception as e:
            print(f"[fix attempt failed] {e}")
            break
        ok, output = await validate_build()

    return {
        "layout_notes": layout_notes,
        "files_written": written,
        "build_ok": ok,
        "fix_attempts": attempts,
        "build_log_tail": None if ok else output[-1500:],
    }