"""Launch local Playwright normally, or bundled Chromium in Vercel Functions."""

import asyncio
import io
import os
import tarfile
import tempfile
from pathlib import Path

CLOUD = os.environ.get("VERCEL") == "1"
_lock = asyncio.Lock()
_executable = None


def _unpack():
    import brotli

    global _executable

    root = Path(tempfile.gettempdir()) / "web-scrapper-chromium"
    ready = root / ".ready"
    if not ready.exists():
        root.mkdir(parents=True, exist_ok=True)
        bundled = Path(__file__).with_name("browser_bin")
        for name in ("al2023.tar.br", "fonts.tar.br", "swiftshader.tar.br"):
            raw = brotli.decompress((bundled / name).read_bytes())
            target = root / name.removesuffix(".tar.br") if name != "swiftshader.tar.br" else root
            target.mkdir(exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                archive.extractall(target, filter="data")
        # The upstream config points at /tmp/fonts; our bundle has its own directory.
        (root / "fonts" / "fonts.conf").write_text(
            f'<?xml version="1.0"?><fontconfig><dir>{root / "fonts" / "fonts"}</dir>'
            f'<cachedir>{root / "font-cache"}</cachedir></fontconfig>', encoding="utf-8")
        ready.touch()
    if _executable is None:
        # Keep the executable in anonymous RAM: Vercel's /tmp is too small for
        # Chromium plus page rendering, while the compressed bundle fits easily.
        descriptor = os.memfd_create("web-scrapper-chromium", 0)
        with os.fdopen(descriptor, "wb", closefd=False) as binary:
            binary.write(brotli.decompress((Path(__file__).with_name("browser_bin") / "chromium.br").read_bytes()))
        os.fchmod(descriptor, 0o755)
        _executable = f"/proc/{os.getpid()}/fd/{descriptor}"
    return root


async def launch_browser(playwright, headless=False):
    if not CLOUD:
        return await playwright.chromium.launch(headless=headless)
    async with _lock:
        root = await asyncio.to_thread(_unpack)
    env = dict(os.environ)
    env["FONTCONFIG_PATH"] = str(root / "fonts")
    env["LD_LIBRARY_PATH"] = f"{root / 'al2023' / 'lib'}:{root}:" + env.get("LD_LIBRARY_PATH", "")
    return await playwright.chromium.launch(
        executable_path=_executable,
        headless=True,
        env=env,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
              "--no-zygote", "--single-process", "--disable-webgl",
              "--disable-gpu", "--disable-software-rasterizer",
              "--disable-site-isolation-trials",
              "--font-render-hinting=none",
              "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process",
              "--enable-features=NetworkServiceInProcess2"],
    )
