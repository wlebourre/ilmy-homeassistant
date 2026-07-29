"""Unit tests for custom_components/ilmy/url_validation.py.

The module under test has no Home Assistant imports, so these tests run
standalone:

    python3 -m unittest discover -s tests
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "ilmy"
    / "url_validation.py"
)
_spec = importlib.util.spec_from_file_location("url_validation", _MODULE_PATH)
url_validation = importlib.util.module_from_spec(_spec)
sys.modules["url_validation"] = url_validation
_spec.loader.exec_module(url_validation)

validate_service_url = url_validation.validate_service_url
is_private_host = url_validation.is_private_host
non_default_hosts = url_validation.non_default_hosts
ERROR_INVALID_URL = url_validation.ERROR_INVALID_URL
ERROR_HTTPS_REQUIRED = url_validation.ERROR_HTTPS_REQUIRED

DEFAULTS = {
    "hijri_url": "https://hijri.ilmy.org",
    "hadith_url": "https://hadith.ilmy.org",
    "quran_url": "https://quran.ilmy.org",
}


class TestIsPrivateHost(unittest.TestCase):
    def test_localhost_names(self):
        self.assertTrue(is_private_host("localhost"))
        self.assertTrue(is_private_host("foo.localhost"))
        self.assertTrue(is_private_host("LOCALHOST"))

    def test_private_ips(self):
        for host in ("127.0.0.1", "127.0.1.5", "10.0.0.4", "172.16.3.2",
                     "172.31.255.255", "192.168.1.20", "::1",
                     "169.254.1.1", "fe80::1"):
            with self.subTest(host=host):
                self.assertTrue(is_private_host(host))

    def test_public_hosts(self):
        # 203.0.113.0/24 (TEST-NET-3) and 100.64.0.0/10 (CGNAT) are flagged
        # "private" by ipaddress.is_private but are NOT trusted LAN hosts.
        for host in ("8.8.8.8", "hijri.ilmy.org", "example.com", "",
                     None, "172.15.0.1", "172.32.0.1", "203.0.113.10",
                     "100.64.0.1"):
            with self.subTest(host=host):
                self.assertFalse(is_private_host(host))


class TestValidateServiceUrl(unittest.TestCase):
    def test_https_public_ok(self):
        self.assertIsNone(validate_service_url("https://hijri.ilmy.org"))
        self.assertIsNone(validate_service_url("https://example.com:8443/api"))

    def test_http_public_rejected(self):
        self.assertEqual(
            validate_service_url("http://hijri.ilmy.org"), ERROR_HTTPS_REQUIRED
        )
        self.assertEqual(
            validate_service_url("http://203.0.113.10"), ERROR_HTTPS_REQUIRED
        )

    def test_http_private_allowed(self):
        for url in ("http://localhost:8080", "http://127.0.0.1:8080",
                    "http://192.168.1.10", "http://10.0.0.5:9000",
                    "http://[::1]:8123"):
            with self.subTest(url=url):
                self.assertIsNone(validate_service_url(url))

    def test_invalid_urls(self):
        for url in ("", "not-a-url", "ftp://example.com", "https://",
                    "//example.com", "https://user:pass@example.com",
                    "https://user@example.com", "ws://example.com"):
            with self.subTest(url=url):
                self.assertEqual(validate_service_url(url), ERROR_INVALID_URL)

    def test_whitespace_tolerated(self):
        self.assertIsNone(validate_service_url("  https://hijri.ilmy.org  "))


class TestNonDefaultHosts(unittest.TestCase):
    def test_defaults_not_flagged(self):
        self.assertEqual(non_default_hosts(dict(DEFAULTS), DEFAULTS), {})

    def test_default_host_different_scheme_not_flagged(self):
        urls = dict(DEFAULTS, hijri_url="http://hijri.ilmy.org:8443")
        self.assertEqual(non_default_hosts(urls, DEFAULTS), {})

    def test_custom_hosts_flagged(self):
        urls = dict(
            DEFAULTS,
            hijri_url="http://192.168.1.10:8080",
            quran_url="https://quran.example.com",
        )
        self.assertEqual(
            non_default_hosts(urls, DEFAULTS),
            {"hijri_url": "192.168.1.10", "quran_url": "quran.example.com"},
        )

    def test_case_insensitive_host_comparison(self):
        urls = dict(DEFAULTS, hijri_url="https://HIJRI.ilmy.org")
        self.assertEqual(non_default_hosts(urls, DEFAULTS), {})


if __name__ == "__main__":
    unittest.main()
