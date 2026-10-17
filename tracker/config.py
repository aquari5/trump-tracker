"""Loads config.yaml; secrets come only from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    email = cfg.setdefault("email", {})
    email["smtp_host"] = os.environ.get("SMTP_HOST") or email.get("smtp_host", "smtp.gmail.com")
    email["smtp_port"] = int(os.environ.get("SMTP_PORT") or email.get("smtp_port", 587))
    email.setdefault("timezone", "Europe/Warsaw")
    email["user"] = os.environ.get("SMTP_USER", "").strip()
    email["password"] = os.environ.get("SMTP_PASSWORD", "").strip()
    email["to"] = [a.strip() for a in os.environ.get("EMAIL_TO", "").split(",") if a.strip()]
    email["from"] = os.environ.get("EMAIL_FROM", "").strip() or email["user"]

    cfg.setdefault("analysis", {})
    cfg.setdefault("sources", [])
    cfg.setdefault("trades", {})
    cfg.setdefault("weekly", {})
    repo = os.environ.get("GITHUB_REPOSITORY")
    cfg["repo_url"] = f"https://github.com/{repo}" if repo else ""
    return cfg
