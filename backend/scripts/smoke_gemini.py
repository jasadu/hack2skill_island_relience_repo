"""Day-1 smoke test: proves key, SDK, structured output, latency and the thinking setting.
Run:  export GEMINI_API_KEY=$(gcloud secrets versions access latest --secret=gemini-api-key)
      python scripts/smoke_gemini.py [model]"""
import os, sys, time
from google import genai
from pydantic import BaseModel


class Out(BaseModel):
    ok: bool
    note: str


models = sys.argv[1:] or ["gemini-3.8-flash", "gemini-3.6-flash"]
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
for model in models:
    for level in ("low", "medium"):
        t0 = time.time()
        it = client.interactions.create(
            model=model, input="Return ok=true and a one-sentence note.", store=False,
            system_instruction="You are a test endpoint.",
            generation_config={"thinking_level": level},
            response_format={"type": "text", "mime_type": "application/json", "schema": Out.model_json_schema()},
            timeout=30)
        print(f"{model:18} thinking={level:6} {time.time() - t0:5.1f}s ->", Out.model_validate_json(it.output_text))
