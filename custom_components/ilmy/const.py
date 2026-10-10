"""Constants for the Ilmy integration."""

from typing import Final

DOMAIN: Final = "ilmy"

# Config entry data keys
CONF_HIJRI_URL: Final = "hijri_url"
CONF_HADITH_URL: Final = "hadith_url"
CONF_QURAN_URL: Final = "quran_url"
CONF_TIME_ZONE_NAME: Final = "time_zone_name"

# Config entry options keys
CONF_METHOD: Final = "method"
CONF_ASR: Final = "asr"
CONF_HIGH_LATITUDE: Final = "high_latitude"
CONF_HADITH_BOOK: Final = "hadith_book"

# Default service base URLs (overridable in the config flow for self-hosters)
DEFAULT_HIJRI_URL: Final = "https://hijri.ilmy.org"
DEFAULT_HADITH_URL: Final = "https://hadith.ilmy.org"
DEFAULT_QURAN_URL: Final = "https://quran.ilmy.org"

# Defaults
DEFAULT_METHOD: Final = "mwl"
DEFAULT_ASR: Final = 1  # 1 = Shafi'i/Maliki/Hanbali, 2 = Hanafi
DEFAULT_HIGH_LATITUDE: Final = 0  # 0 = none, 1 = angle-based, 2 = middle of night, 3 = 1/7th
DEFAULT_LANGUAGE: Final = "en"
DEFAULT_HADITH_BOOK: Final = "bukhari"

LANGUAGES: Final = ["en", "fr", "ar"]

# Identifying header sent with every request to the Ilmy services
USER_AGENT: Final = "HomeAssistant-ilmy/0.2.1"

# Synthetic hadith book choices (rotate across collections instead of one)
HADITH_BOOK_ALL: Final = "all"
HADITH_BOOK_SAHIHAIN: Final = "sahihain"
SAHIHAIN_SLUGS: Final = ("bukhari", "muslim")

# Book kind returned by the books API for real collections (as opposed to
# commentaries/grading books, which are never offered or picked)
BOOK_KIND_COLLECTION: Final = "collection"

# Bus event fired at every prayer time
EVENT_PRAYER: Final = "ilmy_prayer_time"

# Prayer time keys, in chronological order
PRAYERS: Final = ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha", "midnight"]

# Total number of ayat in the ayat table (contiguous global ids 1..6236)
TOTAL_AYAT: Final = 6236

# How many alternative id_in_book values to try when a by-ref lookup 404s
# (numbers that exist only as supplementary chains are not resolvable)
HADITH_404_RETRIES: Final = 10

# Random delay added after Maghrib before the daily content batch, so a
# fleet of installs does not hit the services at the same second
FETCH_JITTER_MINUTES: Final = 20

# Backoff schedule (minutes) for retrying a failed daily batch
RETRY_DELAYS_MINUTES: Final = (15, 30, 60)

# Random delay range (minutes) before the first-ever content fetch when the
# integration is set up after Maghrib — long enough to not fire mid-startup,
# short enough that the content sensors fill in promptly
FIRST_FETCH_DELAY_MINUTES: Final = (1, 5)

# How long the cached book/surah lists are reused before being re-fetched
STATIC_CACHE_TTL_DAYS: Final = 30

# Persistent cache (homeassistant.helpers.storage)
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}_cache"
