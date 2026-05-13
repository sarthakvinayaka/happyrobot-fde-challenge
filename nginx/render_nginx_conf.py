#!/usr/bin/env python3
"""Substitute __API_KEY__ in nginx prod template (nginx map string escaping)."""

from __future__ import annotations

import os
from pathlib import Path


def escape_for_nginx_double_quoted_map_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main() -> None:
    key = os.environ.get("API_KEY", "")
    template_path = Path(os.environ.get("NGINX_TEMPLATE", "/etc/nginx/nginx.prod.conf.template"))
    out_path = Path(os.environ.get("NGINX_CONF_OUT", "/etc/nginx/nginx.conf"))
    raw = template_path.read_text(encoding="utf-8")
    escaped = escape_for_nginx_double_quoted_map_value(key)
    out_path.write_text(raw.replace("__API_KEY__", escaped), encoding="utf-8")


if __name__ == "__main__":
    main()
