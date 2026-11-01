"""Downloads a Whisper model into models/whisper-<name>, resuming after dropped connections.
Usage: python download_whisper.py [tiny|base|small|medium]   (default: small)"""
import sys
import time
import urllib.request
from pathlib import Path

FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def expected_size(url: str) -> int | None:
    """Hugging Face reports the real file size on the (redirecting) HEAD response."""
    try:
        r = urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, method="HEAD"), timeout=30)
        status, headers = r.status, r.headers
    except urllib.error.HTTPError as e:      # the 302 itself lands here
        status, headers = e.code, e.headers
    except Exception:
        return None
    size = headers.get("X-Linked-Size") or (headers.get("Content-Length") if status == 200 else None)
    return int(size) if size else None


def fetch(url: str, dest: Path):
    size = expected_size(url)
    for attempt in range(1, 61):
        have = dest.stat().st_size if dest.exists() else 0
        if size is not None and have == size:
            print(f"{dest.name}: done")
            return
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:      # server ignored the range: start over
                    have = 0
                with open(dest, "ab" if have else "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        have += len(chunk)
                        print(f"\r{dest.name}: {have / 1e6:.0f} MB", end="", flush=True)
            print()
            return
        except urllib.error.HTTPError as e:
            if e.code == 416:                     # already complete
                print(f"{dest.name}: done")
                return
            err = e
        except Exception as e:
            err = e
        print(f"\n{dest.name}: connection dropped ({err}), resuming (try {attempt})")
        time.sleep(min(attempt, 10))
    sys.exit(f"Could not download {url}")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "small"
    out = Path(__file__).resolve().parent / "models" / f"whisper-{name}"
    out.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        fetch(f"https://huggingface.co/Systran/faster-whisper-{name}/resolve/main/{f}", out / f)
    print(f"Saved to {out}. Set WHISPER_MODEL={name} in .env")


if __name__ == "__main__":
    main()
