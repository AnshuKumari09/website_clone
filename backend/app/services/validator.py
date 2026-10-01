import asyncio
import re
import subprocess

from app.config import settings

ANSI = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _run_build() -> tuple[bool, str]:
    proc = subprocess.run(
        "npm run build",
        cwd=str(settings.frontend_dir),
        shell=True,  # needed on Windows so npm.cmd resolves
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    out = ANSI.sub("", (proc.stdout or "") + "\n" + (proc.stderr or ""))
    return proc.returncode == 0, out[-6000:]  # keep the tail, that's where errors are


async def validate_build() -> tuple[bool, str]:
    """Run `next build` off the event loop. Returns (success, build log tail)."""
    try:
        return await asyncio.to_thread(_run_build)
    except subprocess.TimeoutExpired:
        return False, "Build timed out after 300s"