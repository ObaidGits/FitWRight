"""Cover letter and outreach greetings.

The bug this covers: both prompts were given the job description and the
candidate's own resume, and never told who the recipient was. The only human name
in context was the candidate's, so the model greeted them - "Hi Obaidullah," on a
letter the candidate is *sending*. It went out to employers in the user's name and
made them look careless.

The prompts now state who is who. This guard exists because a prompt is a request:
a document sent to an employer under someone's name should not depend on the model
having complied.
"""
from app.services.cover_letter import fix_self_addressed_greeting

RESUME = {"personal_info": {"name": "Obaidullah Zeeshan"}}


class TestSelfAddressedGreeting:
    def test_rewrites_a_greeting_using_the_full_name(self):
        text = "Hi Obaidullah Zeeshan,\n\nI saw the platform role."
        assert fix_self_addressed_greeting(text, RESUME).startswith("Hi Hiring Manager,")

    def test_rewrites_a_greeting_using_the_first_name(self):
        text = "Hi Obaidullah,\n\nI saw the platform role."
        fixed = fix_self_addressed_greeting(text, RESUME)
        assert fixed.startswith("Hi Hiring Manager,")
        assert "Obaidullah," not in fixed.split("\n")[0]

    def test_handles_dear_as_well(self):
        text = "Dear Obaidullah,\n\nRegarding the role."
        assert fix_self_addressed_greeting(text, RESUME).startswith("Dear Hiring Manager,")

    def test_is_case_insensitive(self):
        text = "HELLO OBAIDULLAH,\n\nBody."
        assert "OBAIDULLAH" not in fix_self_addressed_greeting(text, RESUME).split("\n")[0]

    def test_keeps_the_body_intact(self):
        text = "Hi Obaidullah,\n\nI built ETL pipelines at Acme.\n\nRegards,\nObaidullah Zeeshan"
        fixed = fix_self_addressed_greeting(text, RESUME)
        assert "I built ETL pipelines at Acme." in fixed
        # The sign-off is the one place the candidate's name belongs.
        assert fixed.rstrip().endswith("Obaidullah Zeeshan")


class TestLeavesCorrectGreetingsAlone:
    def test_a_proper_hiring_manager_greeting(self):
        text = "Dear Hiring Manager,\n\nBody."
        assert fix_self_addressed_greeting(text, RESUME) == text

    def test_a_named_recruiter(self):
        text = "Hi Priya,\n\nBody."
        assert fix_self_addressed_greeting(text, RESUME) == text

    def test_a_company_team_greeting(self):
        text = "Hi Globex team,\n\nBody."
        assert fix_self_addressed_greeting(text, RESUME) == text

    def test_the_candidate_name_later_in_the_letter(self):
        """A sign-off is correct and must never be rewritten."""
        text = "Dear Hiring Manager,\n\nBody here.\n\nBest regards,\nObaidullah Zeeshan"
        assert fix_self_addressed_greeting(text, RESUME) == text

    def test_text_that_does_not_start_with_a_greeting(self):
        text = "I noticed your team is rebuilding its data platform.\n\nObaidullah Zeeshan"
        assert fix_self_addressed_greeting(text, RESUME) == text

    def test_a_resume_with_no_name(self):
        text = "Hi Obaidullah,\n\nBody."
        assert fix_self_addressed_greeting(text, {}) == text

    def test_a_very_short_first_name_is_not_matched_alone(self):
        """"Hi Al," is not evidence of anything when the candidate is Al Smith."""
        text = "Hi Al,\n\nBody."
        assert fix_self_addressed_greeting(text, {"personal_info": {"name": "Al Smith"}}) == text

    def test_empty_input(self):
        assert fix_self_addressed_greeting("", RESUME) == ""
