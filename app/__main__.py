"""``python -m app`` — start the bot + API + UI using host/port from config.yaml."""
from __future__ import annotations

import argparse

import uvicorn

from app.config import DEFAULT_CONFIG_PATH, load_config_file


def main() -> None:
    ap = argparse.ArgumentParser(description="MSC Bot (Market Structure & Confluence) for MetaTrader 5")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to config.yaml")
    ap.add_argument("--host", default=None, help="override server.host")
    ap.add_argument("--port", type=int, default=None, help="override server.port")
    args = ap.parse_args()
    cfg = load_config_file(__import__("pathlib").Path(args.config))
    from app.main import create_app

    app = create_app(args.config)
    uvicorn.run(app, host=args.host or cfg.server.host, port=args.port or cfg.server.port,
                log_level=cfg.server.log_level.lower(), access_log=False)


if __name__ == "__main__":
    main()
