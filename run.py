"""Start the backend and the dashboard together. Ctrl+C stops both.

    .venv\\Scripts\\python run.py
"""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
API_PORT = os.getenv("API_PORT", "8000")
DASH_PORT = os.getenv("DASH_PORT", "8501")


def wait_for(url: str, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> None:
    backend = subprocess.Popen([PY, "-m", "uvicorn", "backend.main:app", "--reload",
                                "--host", "127.0.0.1", "--port", API_PORT], cwd=ROOT)
    if not wait_for(f"http://127.0.0.1:{API_PORT}/health") or backend.poll() is not None:
        print(f"Backend did not start (is port {API_PORT} already in use?), see the errors above.")
        backend.terminate()
        sys.exit(1)
    dashboard = subprocess.Popen([PY, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", DASH_PORT,
                                  "--server.headless", "true", "--browser.gatherUsageStats", "false"], cwd=ROOT,
                                 env={**os.environ, "API_URL": f"http://127.0.0.1:{API_PORT}"})
    wait_for(f"http://127.0.0.1:{DASH_PORT}")
    print(f"\n  Dashboard: http://localhost:{DASH_PORT}\n  API docs:  http://127.0.0.1:{API_PORT}/docs\n  Ctrl+C to stop both.\n")
    if not os.getenv("NO_BROWSER"):
        webbrowser.open(f"http://localhost:{DASH_PORT}")
    try:
        while backend.poll() is None and dashboard.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (dashboard, backend):
            p.terminate()
        for p in (dashboard, backend):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
