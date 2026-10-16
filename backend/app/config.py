import os

ASSESS_MODEL = os.getenv("ASSESS_MODEL", "gemini-3.8-flash")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gemini-3.6-flash")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")
DAILY_AI_BUDGET = int(os.getenv("DAILY_AI_BUDGET", "600"))
PER_RUN_AI_CAP = int(os.getenv("PER_RUN_AI_CAP", "25"))
PER_RUN_QA_CAP = int(os.getenv("PER_RUN_QA_CAP", "20"))
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "low")        # low | medium | high  ("minimal" errors on 3.8 flash)
AI_TIMEOUT_S = float(os.getenv("AI_TIMEOUT_S", "35"))       # Firebase Hosting cuts requests at 60 s
FAKE_AI = os.getenv("FAKE_AI", "") == "1"                   # local tests only: deterministic stand-in for Gemini
STORE = os.getenv("STORE", "firestore")                     # "firestore" | "memory"
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "120"))
AI_RATE_LIMIT_PER_MIN = int(os.getenv("AI_RATE_LIMIT_PER_MIN", "12"))

# 14-day outlook (El Niño heat-wave planning)
OUTLOOK_LAT = float(os.getenv("OUTLOOK_LAT", "10.1"))        # reference point in the Philippines (the island itself is fictional)
OUTLOOK_LON = float(os.getenv("OUTLOOK_LON", "125.6"))
OUTLOOK_PATHS = int(os.getenv("OUTLOOK_PATHS", "600"))         # Monte Carlo paths
PER_RUN_OUTLOOK_CAP = int(os.getenv("PER_RUN_OUTLOOK_CAP", "6"))
CLIMATE_LIVE = os.getenv("CLIMATE_LIVE", "1") == "1"           # tests set 0 so they never touch the network
