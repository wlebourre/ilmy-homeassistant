# Ilmy — Home Assistant integration

Privacy-friendly Islamic prayer times (salah), Hijri date, qibla, hadith of
the day and verse of the day for Home Assistant.

**No account, no sign-in, no API key** (since v0.1.0). Prayer times and the
qibla direction are computed **100% locally** on your Home Assistant device —
there is no cloud dependency for the times themselves. The only network
traffic is a small anonymous daily fetch of the content sensors (Hijri date,
hadith, verse), from the Ilmy platform services:

- **hijri** — Hijri calendar (`https://hijri.ilmy.org`)
- **hadith** — hadith collections (`https://hadith.ilmy.org`)
- **quran** — quran text and translations (`https://quran.ilmy.org`)

**Requirements:** Home Assistant **2025.12.0 or newer**.

## Features

- **12 sensors**: 7 prayer timestamp sensors (fajr, sunrise, dhuhr, asr,
  maghrib, isha, midnight) usable in `time` automation triggers exactly like
  the built-in `sun` integration — with `offset` support — plus
  `next_prayer`, `hijri_date`, `qibla`, `hadith_of_the_day` and
  `verse_of_the_day`.
- An **`ilmy_prayer_time` event** fired at every prayer, with a ready-made
  **notification blueprint**.
- **19 calculation methods** (MWL, Egyptian, Karachi, Umm Al-Qura, ISNA,
  UOIF, Diyanet, …) with a **country-based default** derived from your HA
  configuration, an **Asr juristic school** setting (Shafi'i/Maliki/Hanbali
  or Hanafi) and **high-latitude adjustment** for polar day/night.
- Daily content in **English, French or Arabic**, from your chosen hadith
  collection.
- Fully configured through the UI — no YAML.

## Privacy & politeness

- **Exactly 3 anonymous GETs per day** (Hijri date, hadith, verse), sent as
  one batch shortly after local **Maghrib** plus a random 0–20 min jitter —
  a fleet of installs never hits the services at the same second.
- The hadith book list and surah list are cached locally for 30 days, so
  they are not part of the daily traffic.
- Every request carries an identifying `User-Agent`
  (`HomeAssistant-ilmy/0.2.2`) — no credentials of any kind are sent.
- **Your location never leaves your home**: prayer times and qibla are
  computed locally from the coordinates you configure.
- `https://` is enforced for public service hosts (plain `http://` is
  accepted only for localhost/private LAN addresses, for self-hosters).
- On failure the daily batch retries with backoff (15/30/60 min, honoring
  `Retry-After`), then keeps the previous content until the next Maghrib.

## Installation

### HACS (custom repository)

1. In HACS, open the menu → **Custom repositories**, add
   `https://github.com/wlebourre/ilmy-homeassistant` with category
   **Integration**.
2. Find **Ilmy** in HACS and click **Download**.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/ilmy` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.

> **Always restart after installing or updating.** Custom integration code
> is only loaded at startup: after installing the integration — and after
> every HACS update — you **must restart Home Assistant** before new
> entities, triggers or fixes become available.

## Configuration

Everything is done in the UI — **Settings → Devices & services → Add
integration → Ilmy**:

- **Service URLs** — the defaults (`https://{hijri,hadith,quran}.ilmy.org`)
  work for most users. Self-hosting is supported: point them at your own
  stack (`http://` is allowed for localhost/private hosts).
- **Location** — latitude/longitude/time zone, prefilled from your Home
  Assistant home location.
- **Content language** (en/fr/ar) and **hadith collection** (fetched live
  from the hadith service, with a built-in fallback list). Besides the
  individual collections you can pick **All collections** or **The Two
  Sahihs — Al-Bukhari & Muslim** to rotate the hadith of the day across
  several collections.
- The calculation method defaults from your HA-configured **country**.

The **Configure** button on the integration card re-opens the options at any
time: calculation method, Asr school, high-latitude rule, language, hadith
collection — and the location, so you can fine-tune it without re-adding the
integration.

Verify in **Developer tools → States** (prayer timestamps in your local time
zone, hijri date, hadith/verse references) and listen to `ilmy_prayer_time`
in **Developer tools → Events**.

## Upgrading from ≤ 0.3.0

v0.1.0 is a **clean break**: the ilmy account / OAuth sign-in is gone.

1. Open **Settings → Devices & services → Ilmy** and **delete** the old
   config entry.
2. Update the integration (HACS or copy the new files) and restart.
3. Add the integration again — no sign-in this time.

Entity unique IDs are unchanged, so your dashboards, automations and the
blueprint keep working after re-adding.

## Entities

| Entity | State | Notes |
| --- | --- | --- |
| `sensor.ilmy_fajr` … `sensor.ilmy_midnight` | timestamp | fajr, sunrise, dhuhr, asr, maghrib, isha, midnight |
| `sensor.ilmy_next_prayer` | prayer name | attribute: `time` |
| `sensor.ilmy_hijri_date` | e.g. `2 Dhul-Qa'dah 1446` | attributes: day/month/year, `is_ramadan`, `stale`, … |
| `sensor.ilmy_qibla` | degrees from north | attributes: compass direction, arabic label |
| `sensor.ilmy_hadith_of_the_day` | e.g. `Sahih al-Bukhari 756` | attributes: arabic, translation, book, chapter |
| `sensor.ilmy_verse_of_the_day` | e.g. `2:255` | attributes: uthmani_text, translation, surah, juz, page |

Long hadith/verse text lives in the attributes (HA states are limited to
255 characters). A prayer timestamp reads `unknown` when the sun never
reaches that prayer's angle (polar day/night) — enable a high-latitude
adjustment in the options if that affects you.

## Automations

### Prayer time trigger (recommended — no YAML)

1. Go to **Settings → Automations & scenes** and create (or edit) an
   automation.
2. Click **Add trigger**.
3. In the dialog, open the **"By type"** tab.
   ⚠️ The trigger is **not** listed under **"By target"** — that tab only
   groups triggers that act on a specific device or entity, and prayer
   times are global to your home, so "No triggers available for Ilmy"
   there is expected.
4. Type **"Prayer time"** in the search box (or scroll to the **Ilmy**
   section) and pick **Prayer time**.
5. Choose the **prayer** (Fajr, Sunrise, Dhuhr, Asr, Maghrib, Isha,
   Midnight — or **Any prayer** to fire at every one) and optionally an
   **offset** — use a negative duration like `-00:10:00` to fire *before*
   the prayer.
6. Add your action(s). In message/text fields you can use the trigger
   variables `{{ trigger.prayer }}`, `{{ trigger.time }}` and
   `{{ trigger.hijri_date }}`.

Prefer zero setup? The **notification blueprint** (below) builds a prayer
notification automation for you in a few clicks.

In YAML, the same trigger is:

```yaml
triggers:
  - trigger: ilmy.prayer_time
    options:
      prayer: maghrib
      offset: "-00:10:00"
actions:
  - action: notify.notify
    data:
      message: "Maghrib in 10 minutes — {{ trigger.hijri_date }}"
```

### Other ways to trigger (YAML)

Fire on the raw `ilmy_prayer_time` event:

```yaml
triggers:
  - trigger: event
    event_type: ilmy_prayer_time
    event_data:
      prayer: maghrib
```

Or use the prayer timestamp sensors in a standard time trigger (with an
optional offset nested inside `at:`):

```yaml
triggers:
  - trigger: time
    at:
      entity_id: sensor.ilmy_maghrib
      offset: "-00:10:00"
```

### Notification blueprint

Copy `blueprints/automation/ilmy/prayer_notification.yaml` from this
directory into your HA `config/blueprints/automation/ilmy/` folder, then go
to **Settings → Automations → Create automation → Ilmy prayer time
notification**. Pick the prayer, the notify target (e.g.
`mobile_app_my_phone`) and the message.

## Daily content

The hadith and verse of the day rotate deterministically with the calendar
day (no repeats within a cycle — across the combined range of all selected
collections when **All collections** or **The Two Sahihs** is chosen) and
refresh once per day,
shortly after local Maghrib. The Hijri date sensor is marked with a
`stale: true` attribute if its last fetch failed and the previous value is
being kept. Content survives restarts — the last known values are restored
until the next daily batch.

## Development

### Tests

```bash
cd homeassistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
python -m pytest tests/
```

The suite includes minute-exact parity tests of the local prayer engine and
qibla calculation against the verified Go implementation they port.

### Smoke test (dev container)

```bash
cd homeassistant
docker compose -f docker-compose.dev.yml up -d
# open http://localhost:8123, complete onboarding, then add the Ilmy integration
```

Checklist:

1. Add the integration via the UI — no sign-in prompt — and confirm all 12
   entities appear under **Settings → Devices & services → Ilmy**.
2. Check the states in **Developer tools → States** (prayer timestamps in
   your local time zone, hijri date, hadith/verse references).
3. Listen to `ilmy_prayer_time` in **Developer tools → Events** and wait for
   the next prayer (or check the logs for `Fired ilmy_prayer_time`).
4. Check the service access logs: exactly one anonymous batch (hijri date,
   hadith, verse) per day, after local Maghrib.

## Structure

```
homeassistant/
├── custom_components/ilmy/   # the integration (incl. the local prayer engine)
├── blueprints/automation/ilmy/prayer_notification.yaml
├── tests/                    # unit / parity tests
└── docker-compose.dev.yml    # optional dev container
```

## License

MIT — see [LICENSE](LICENSE). The Ilmy name, logo, and icon
(`custom_components/ilmy/brand/`) are **not** covered by the MIT license;
they remain © Ilmy, all rights reserved (see [NOTICE](NOTICE)).
