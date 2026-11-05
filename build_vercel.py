"""Bundle a pinned, serverless-compatible Chromium without a Node runtime."""

import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.request import urlopen



def main():
    version = "153.0.0"
    registry = f"https://registry.npmjs.org/@sparticuz/chromium/{version}"
    with urlopen(registry, timeout=60) as response:
        package = json.load(response)
    with urlopen(package["dist"]["tarball"], timeout=120) as response:
        archive = response.read()
    if hashlib.sha1(archive).hexdigest() != package["dist"]["shasum"]:
        raise RuntimeError("Chromium package checksum mismatch")

    destination = Path(__file__).parent / "webapp" / "browser_bin"
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for name in ("chromium.br", "al2023.tar.br", "fonts.tar.br", "swiftshader.tar.br"):
            source = bundle.extractfile(f"package/bin/{name}")
            if source is None:
                raise RuntimeError(f"Chromium package is missing {name}")
            (destination / name).write_bytes(source.read())
    print(f"Bundled serverless Chromium {version}")


if __name__ == "__main__":
    main()
