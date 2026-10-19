"""Writes the API's OpenAPI document to web/openapi.json; the web app generates its TypeScript types from it."""
import json
from pathlib import Path

from quantlab.api.app import create_app

out = Path(__file__).resolve().parent.parent / "web" / "openapi.json"
out.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("wrote", out)
