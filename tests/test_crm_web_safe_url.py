import unittest

from crm_web import safe_url


class SafeUrlTests(unittest.TestCase):
    def test_plain_domain_and_port_are_normalized(self):
        self.assertEqual(safe_url("example.com/path"), "https://example.com/path")
        self.assertEqual(safe_url("example.com:8443/path"), "https://example.com:8443/path")

    def test_scheme_without_separator_is_not_mistaken_for_a_domain(self):
        self.assertEqual(safe_url("https:example.com"), "")


if __name__ == "__main__":
    unittest.main()
