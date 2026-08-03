from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from app.main import app, bootstrap_url


def main() -> None:
    parser = argparse.ArgumentParser(description="PlantDiseases local admin panel")
    parser.add_argument("--port", type=int, default=8787, help="loopback port (default: 8787)")
    parser.add_argument("--no-browser", action="store_true", help="print the secure URL without opening it")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    url = bootstrap_url(args.port)
    print("\nPlantDiseases Admin is starting on loopback only.")
    print(f"Open this one-time URL: {url}\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
