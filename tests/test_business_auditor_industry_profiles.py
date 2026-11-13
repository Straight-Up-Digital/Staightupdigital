import unittest

import business_auditor


class IndustryProfileTests(unittest.TestCase):
    def test_accommodation_terms_select_the_accommodation_profile(self):
        self.assertEqual(business_auditor.industry_profile("Guest house")["key"], "accommodation")
        self.assertEqual(business_auditor.industry_profile("B&B")["key"], "accommodation")
        self.assertEqual(business_auditor.industry_profile("Plumber")["key"], "trade")

    def test_accommodation_profile_does_not_penalize_missing_opening_hours(self):
        listing = {
            "phone": None,
            "has_hours": False,
            "business_status": "OPERATIONAL",
            "primary_type": "lodging",
            "photo_count": None,
        }

        self.assertEqual(business_auditor.score_google_profile(listing, hours_expected=False), 20)
        self.assertLess(business_auditor.score_google_profile(listing), 20)

    def test_accommodation_briefs_include_rooms_and_booking_content(self):
        briefs = business_auditor.build_briefs(
            "Seaside Lodge",
            "Port Alfred",
            [],
            [],
            {"own_question_headings": 0},
            {"pages": [], "combined_text": ""},
            {},
            industry="Guest house",
        )

        page_types = {brief["page_type"] for brief in briefs}
        self.assertIn("Rooms and rates page", page_types)
        self.assertIn("Booking and enquiry path", page_types)


if __name__ == "__main__":
    unittest.main()
