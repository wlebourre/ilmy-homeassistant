"""Prayer-time calculation methods.

Pure-Python port of hijri/internal/prayer/methods.go (and its TypeScript
sibling main/frontend/src/lib/prayerEngine.ts). Stdlib only, import-safe,
fully synchronous.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_METHOD = "mwl"

# High-latitude adjustment modes (mirror Go's HighLatMethod constants).
HIGH_LAT_NONE = 0
HIGH_LAT_ANGLE_BASED = 1  # angle/60 * night duration
HIGH_LAT_MIDDLE_NIGHT = 2  # half of the night
HIGH_LAT_ONE_SEVENTH = 3  # 1/7th of the night


@dataclass(frozen=True)
class CalculationMethod:
    """Parameters of one prayer-time calculation method (mirrors Go's Method).

    Exactly one of ``isha_angle`` / ``isha_interval_minutes`` is set:
    angle-based methods define Isha as a depression angle, interval-based
    ones (Umm Al-Qura, Qatar, UAE, Gulf) as minutes after Maghrib.
    """

    id: str
    name: str
    name_ar: str
    fajr_angle: float
    isha_angle: float | None
    isha_interval_minutes: float | None
    isha_ramadan_minutes: float | None
    # Reserved (no method defines Maghrib by angle); always None.
    maghrib_angle: float | None
    # 0 = Maghrib at sunset, >0 = minutes after sunset (Morocco: 3).
    maghrib_interval_minutes: float
    midnight_method: str
    countries: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    description_ar: str = ""

    @property
    def isha_is_interval(self) -> bool:
        """True when Isha is minutes-after-Maghrib rather than an angle."""
        return self.isha_interval_minutes is not None


def _angle_method(
    id: str,
    name: str,
    name_ar: str,
    fajr_angle: float,
    isha_angle: float,
    *,
    maghrib_interval_minutes: float = 0,
    countries: tuple[str, ...] = (),
    description: str = "",
    description_ar: str = "",
) -> CalculationMethod:
    return CalculationMethod(
        id=id,
        name=name,
        name_ar=name_ar,
        fajr_angle=fajr_angle,
        isha_angle=isha_angle,
        isha_interval_minutes=None,
        isha_ramadan_minutes=None,
        maghrib_angle=None,
        maghrib_interval_minutes=maghrib_interval_minutes,
        midnight_method="standard",
        countries=countries,
        description=description,
        description_ar=description_ar,
    )


def _interval_method(
    id: str,
    name: str,
    name_ar: str,
    fajr_angle: float,
    isha_interval_minutes: float,
    *,
    isha_ramadan_minutes: float | None = None,
    countries: tuple[str, ...] = (),
    description: str = "",
    description_ar: str = "",
) -> CalculationMethod:
    return CalculationMethod(
        id=id,
        name=name,
        name_ar=name_ar,
        fajr_angle=fajr_angle,
        isha_angle=None,
        isha_interval_minutes=isha_interval_minutes,
        isha_ramadan_minutes=isha_ramadan_minutes,
        maghrib_angle=None,
        maghrib_interval_minutes=0,
        midnight_method="standard",
        countries=countries,
        description=description,
        description_ar=description_ar,
    )


METHODS: dict[str, CalculationMethod] = {m.id: m for m in (
    _angle_method(
        "mwl", "Muslim World League", "رابطة العالم الإسلامي", 18, 17,
        countries=("GB", "DE", "ES", "IT", "NL", "BE", "CH", "AT", "SE", "NO",
                   "DK", "FI", "PL", "PT", "RU", "CN", "JP", "KR", "AU", "NZ",
                   "ZA", "NG", "GH", "SN", "CI", "ML", "NE", "TD", "SD"),
        description="Used in Europe, Far East, and parts of the Americas. Fajr: 18°, Isha: 17°.",
        description_ar="يُستخدم في أوروبا والشرق الأقصى وأجزاء من الأمريكتين. الفجر: 18°، العشاء: 17°.",
    ),
    _angle_method(
        "egyptian", "Egyptian General Authority of Survey",
        "الهيئة المصرية العامة للمساحة", 19.5, 17.5,
        countries=("EG", "SY", "LB", "IQ", "LY", "SS", "KE", "TZ", "UG", "RW",
                   "BI", "MW", "ZM", "ZW"),
        description="Used in Egypt, Africa, Syria, Lebanon and Iraq. Fajr: 19.5°, Isha: 17.5°.",
        description_ar="يُستخدم في مصر وأفريقيا وسوريا ولبنان والعراق. الفجر: 19.5°، العشاء: 17.5°.",
    ),
    _angle_method(
        "karachi", "University of Islamic Sciences, Karachi",
        "جامعة العلوم الإسلامية، كراتشي", 18, 18,
        countries=("PK", "BD", "AF", "IN"),
        description="Used in Pakistan, Bangladesh, India, and Afghanistan. Fajr: 18°, Isha: 18°.",
        description_ar="يُستخدم في باكستان وبنغلاديش والهند وأفغانستان. الفجر: 18°، العشاء: 18°.",
    ),
    _interval_method(
        "umm_al_qura", "Umm Al-Qura University, Makkah",
        "جامعة أم القرى، مكة المكرمة", 18.5, 90,
        isha_ramadan_minutes=120,
        countries=("SA",),
        description="Official method for Saudi Arabia. Fajr: 18.5°, Isha: 90 min after Maghrib (120 in Ramadan).",
        description_ar="الطريقة الرسمية للمملكة العربية السعودية. الفجر: 18.5°، العشاء: 90 دقيقة بعد المغرب (120 في رمضان).",
    ),
    _angle_method(
        "isna", "Islamic Society of North America (ISNA)",
        "الجمعية الإسلامية لأمريكا الشمالية", 15, 15,
        countries=("US", "CA"),
        description="Used in the USA and Canada. Fajr: 15°, Isha: 15°.",
        description_ar="يُستخدم في الولايات المتحدة وكندا. الفجر: 15°، العشاء: 15°.",
    ),
    _angle_method(
        "uoif", "Union des Organisations Islamiques de France (UOIF)",
        "اتحاد المنظمات الإسلامية في فرنسا", 12, 12,
        countries=("FR",),
        description="Used in France and parts of Europe. Fajr: 12°, Isha: 12°.",
        description_ar="يُستخدم في فرنسا وأجزاء من أوروبا. الفجر: 12°، العشاء: 12°.",
    ),
    _angle_method(
        "diyanet", "Diyanet İşleri Başkanlığı (Turkey)",
        "رئاسة الشؤون الدينية (تركيا)", 18, 17,
        countries=("TR", "MK", "BA", "AL", "XK", "ME", "RS", "BG", "GR"),
        description="Official method for Turkey and the Balkans. Fajr: 18°, Isha: 17°.",
        description_ar="الطريقة الرسمية لتركيا والبلقان. الفجر: 18°، العشاء: 17°.",
    ),
    _angle_method(
        "kuwait", "Ministry of Awqaf and Islamic Affairs, Kuwait",
        "وزارة الأوقاف والشؤون الإسلامية، الكويت", 18, 17.5,
        countries=("KW",),
        description="Official method for Kuwait. Fajr: 18°, Isha: 17.5°.",
        description_ar="الطريقة الرسمية للكويت. الفجر: 18°، العشاء: 17.5°.",
    ),
    _angle_method(
        "muis", "Majlis Ugama Islam Singapura (MUIS)",
        "المجلس الإسلامي الأعلى بسنغافورة", 20, 18,
        countries=("SG",),
        description="Official method for Singapore. Fajr: 20°, Isha: 18°.",
        description_ar="الطريقة الرسمية لسنغافورة. الفجر: 20°، العشاء: 18°.",
    ),
    _angle_method(
        "jakim", "Jabatan Kemajuan Islam Malaysia (JAKIM)",
        "دائرة تطوير الإسلام في ماليزيا", 20, 18,
        countries=("MY", "BN"),
        description="Official method for Malaysia and Brunei. Fajr: 20°, Isha: 18°.",
        description_ar="الطريقة الرسمية لماليزيا وبروناي. الفجر: 20°، العشاء: 18°.",
    ),
    _angle_method(
        "indonesia", "Kementerian Agama Republik Indonesia",
        "وزارة الشؤون الدينية، إندونيسيا", 20, 18,
        countries=("ID",),
        description="Official method for Indonesia. Fajr: 20°, Isha: 18°.",
        description_ar="الطريقة الرسمية لإندونيسيا. الفجر: 20°، العشاء: 18°.",
    ),
    _angle_method(
        "morocco", "Moroccan Ministry of Habous and Islamic Affairs",
        "وزارة الأوقاف والشؤون الإسلامية، المغرب", 19, 17,
        maghrib_interval_minutes=3,
        countries=("MA",),
        description="Official method for Morocco. Fajr: 19°, Isha: 17°, Maghrib: 3 min after sunset.",
        description_ar="الطريقة الرسمية للمغرب. الفجر: 19°، العشاء: 17°، المغرب: 3 دقائق بعد الغروب.",
    ),
    _angle_method(
        "algeria", "Ministry of Religious Affairs, Algeria",
        "وزارة الشؤون الدينية والأوقاف، الجزائر", 18, 17,
        countries=("DZ",),
        description="Official method for Algeria. Fajr: 18°, Isha: 17°.",
        description_ar="الطريقة الرسمية للجزائر. الفجر: 18°، العشاء: 17°.",
    ),
    _angle_method(
        "tunisia", "Tunisian Ministry of Religious Affairs",
        "وزارة الشؤون الدينية، تونس", 18, 18,
        countries=("TN",),
        description="Official method for Tunisia. Fajr: 18°, Isha: 18°.",
        description_ar="الطريقة الرسمية لتونس. الفجر: 18°، العشاء: 18°.",
    ),
    _angle_method(
        "palestine", "Ministry of Awqaf, Palestine & Jordan",
        "وزارة الأوقاف والشؤون الدينية، فلسطين والأردن", 18, 18,
        countries=("PS", "JO"),
        description="Official method for Palestine and Jordan. Fajr: 18°, Isha: 18°.",
        description_ar="الطريقة الرسمية لفلسطين والأردن. الفجر: 18°، العشاء: 18°.",
    ),
    _interval_method(
        "qatar", "Ministry of Endowments and Islamic Affairs, Qatar",
        "وزارة الأوقاف والشؤون الإسلامية، قطر", 18, 90,
        countries=("QA",),
        description="Official method for Qatar. Fajr: 18°, Isha: 90 min after Maghrib.",
        description_ar="الطريقة الرسمية لقطر. الفجر: 18°، العشاء: 90 دقيقة بعد المغرب.",
    ),
    _interval_method(
        "uae", "General Authority of Islamic Affairs, UAE",
        "الهيئة العامة للشؤون الإسلامية والأوقاف، الإمارات", 18.5, 90,
        countries=("AE",),
        description="Official method for the UAE. Fajr: 18.5°, Isha: 90 min after Maghrib.",
        description_ar="الطريقة الرسمية لدولة الإمارات. الفجر: 18.5°، العشاء: 90 دقيقة بعد المغرب.",
    ),
    _interval_method(
        "gulf", "Gulf Region (Bahrain, Oman, Yemen)",
        "منطقة الخليج (البحرين، عُمان، اليمن)", 19.5, 90,
        countries=("BH", "OM", "YE"),
        description="Used in Bahrain, Oman, and Yemen. Fajr: 19.5°, Isha: 90 min after Maghrib.",
        description_ar="يُستخدم في البحرين وعُمان واليمن. الفجر: 19.5°، العشاء: 90 دقيقة بعد المغرب.",
    ),
    _angle_method(
        "custom", "Custom", "مخصص", 18, 17,
        description="Custom calculation method. Set your own Fajr and Isha angles.",
        description_ar="طريقة حساب مخصصة. حدد زوايا الفجر والعشاء بنفسك.",
    ),
)}


def _build_country_method_map() -> dict[str, str]:
    """ISO alpha-2 → method id, built like Go's buildCountryMethodMap.

    Method ids are ordered by descending country-list size (generic first)
    with an alphabetical tie-break, then applied in order so country-specific
    methods overwrite generic ones.
    """
    ids = sorted(METHODS, key=lambda i: (-len(METHODS[i].countries), i))
    result: dict[str, str] = {}
    for mid in ids:
        for cc in METHODS[mid].countries:
            result[cc] = mid
    return result


COUNTRY_METHOD_MAP: dict[str, str] = _build_country_method_map()


def method_for_country(iso2: str | None) -> str:
    """Default method id for an ISO 3166-1 alpha-2 code (MWL fallback)."""
    if iso2 is None:
        return DEFAULT_METHOD
    return COUNTRY_METHOD_MAP.get(iso2, DEFAULT_METHOD)
