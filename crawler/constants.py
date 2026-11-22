"""Centralized constants for the self-healing crawler."""

YAR_API_URLS = [
    "https://codes.yar.gg/api/codes",
    "https://codes-backend.wwmcodes.workers.dev/v1/codes",
    "https://codes-backend-prod.wwmcodes.workers.dev/v1/codes",
]
YAR_HOME_URL = "https://codes.yar.gg/"

OFFICIAL_NEWS_URL = "https://www.wherewindsmeetgame.com/news/"
STEAM_NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
STEAM_APP_ID = 3564740
OFFICIAL_DOMAINS = ("wherewindsmeetgame.com", "store.steampowered.com")

STALE_HOURS = 24
SHRINK_RATIO = 0.5
GEMINI_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")

SCHEMA_VERSION = 1
SOURCE_TAG = "crawler:codes.yar.gg"
ADAPTIVE_DB_PATH = "data/adaptive.db"

CHALLENGE_MARKERS = (
    "cf-challenge",
    "challenge-platform",
    "Just a moment",
    "Attention Required",
    "verify you are human",
)
