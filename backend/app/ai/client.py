"""Thin wrapper over the Google GenAI SDK (Interactions API). All Gemini calls go through here."""
import logging, os, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
from pydantic import ValidationError
from .. import config

log = logging.getLogger("ai")
_pool = ThreadPoolExecutor(max_workers=4)
_client = None


class AIUnavailable(Exception):
    pass


def client():
    global _client
    if _client is None:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise AIUnavailable("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=key)
    return _client


def with_timeout(fn, seconds=None):
    try:
        return _pool.submit(fn).result(timeout=seconds or config.AI_TIMEOUT_S + 3)
    except FTimeout:
        raise AIUnavailable("timeout")


def create(**kw):
    """Single Interactions call with the global timeout and thinking level applied."""
    t = kw.setdefault("timeout", config.AI_TIMEOUT_S)
    kw.setdefault("generation_config", {"thinking_level": config.THINKING_LEVEL})
    try:
        return with_timeout(lambda: client().interactions.create(**kw), t + 3)
    except AIUnavailable:
        raise
    except Exception as e:                      # SDK / API errors surface as honest "unavailable"
        log.warning("gemini call failed: %s", e)
        raise AIUnavailable(type(e).__name__ + ": " + str(e)[:200])


def json_format(schema_cls):
    return {"type": "text", "mime_type": "application/json", "schema": schema_cls.model_json_schema()}


def call_json(stage, model, system, text, schema_cls, fake_ctx=None):
    """Structured call. Returns a validated pydantic object. One retry if the JSON does not validate."""
    if config.FAKE_AI:
        from . import fake
        return schema_cls.model_validate(fake.respond(stage, fake_ctx or {}))
    last = None
    for attempt in range(2):
        it = create(model=model, system_instruction=system, input=text, store=False,
                    response_format=json_format(schema_cls))
        try:
            return schema_cls.model_validate_json(it.output_text)
        except (ValidationError, ValueError, TypeError) as e:
            last = e
            log.warning("invalid JSON from %s (%s), attempt %d", model, stage, attempt + 1)
    raise AIUnavailable(f"invalid structured output: {last}")


def budget_timeout(deadline, floor=6.0):
    """Per-call timeout that keeps a multi-call loop inside the 60 s Firebase Hosting limit."""
    import time as _t
    return max(floor, min(config.AI_TIMEOUT_S, deadline - _t.time()))
