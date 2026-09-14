import unittest

from scripts.scrub_eval_data import (
    ScrubStats,
    ThreadRedactor,
    scan_residual_pii,
)


class EvalDataScrubbingTests(unittest.TestCase):
    def test_redacts_direct_identifiers_and_signature(self):
        redactor = ThreadRedactor(ScrubStats())

        result = redactor.scrub(
            "Hi Jane Smith, email s1234567@example.edu or call +61 400 123 456. "
            "Best regards, Lecturer Name",
            strip_signature=True,
        )

        self.assertIn("Hi [PERSON_1]", result)
        self.assertIn("[EMAIL_1]", result)
        self.assertIn("[PHONE_1]", result)
        self.assertIn("[SIGNATURE]", result)
        self.assertNotIn("Jane Smith", result)
        self.assertNotIn("Lecturer Name", result)

    def test_preserves_public_rmit_url_but_removes_query_and_fragment(self):
        redactor = ThreadRedactor(ScrubStats())

        result = redactor.scrub(
            "See https://www.rmit.edu.au/students/policy?user=123#private and "
            "https://private.example.edu/token/abc."
        )

        self.assertIn("https://www.rmit.edu.au/students/policy", result)
        self.assertNotIn("user=123", result)
        self.assertIn("[PRIVATE_URL_1]", result)

    def test_output_has_no_residual_direct_identifier_patterns(self):
        redactor = ThreadRedactor(ScrubStats())
        result = redactor.scrub(
            "Contact student@example.edu, s1234567, +61 400 123 456, "
            "or https://private.example.edu/a?token=secret."
        )

        self.assertEqual(scan_residual_pii(result), {})

    def test_discovers_names_in_flattened_mail_headers_and_inline_greetings(self):
        redactor = ThreadRedactor(ScrubStats())
        source = (
            "Hi Student Name, please check with Tutor Name. Thanks, Lecturer Name "
            "From: Student Name <student@example.edu> Sent: Tuesday 1:00 PM "
            "To: Lecturer Name <lecturer@example.edu> Subject: Help"
        )
        redactor.discover_people(source)

        result = redactor.scrub(source)

        self.assertNotIn("Student Name", result)
        self.assertNotIn("Tutor Name", result)
        self.assertNotIn("Lecturer Name", result)
        self.assertIn("[PERSON_", result)
        self.assertIn("[EMAIL_TIMESTAMP]", result)

    def test_redacts_vocative_name_missed_after_first_greeting(self):
        redactor = ThreadRedactor(ScrubStats())
        source = "Hi Alex, Jordan, please submit the file. Great work, Priya!"
        redactor.discover_people(source)

        result = redactor.scrub(source)

        self.assertNotIn("Alex", result)
        self.assertNotIn("Jordan", result)
        self.assertNotIn("Priya", result)

    def test_residual_scan_detects_name_next_to_redacted_email(self):
        hits = scan_residual_pii("From: Jane Doe <[EMAIL_1]> Sent: yesterday")

        self.assertEqual(hits["PERSON_CONTEXT"], 1)


if __name__ == "__main__":
    unittest.main()
