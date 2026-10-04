import unittest

from app.schemas.github import PullRequestData, PullRequestFile
from app.services import llm_review_service
from app.services.llm_review_service import LLMReviewService, bounded_description


def pull_request(files, body=None):
    return PullRequestData(
        owner="owner", repository="repo", number=1, title="Change", body=body,
        base_branch="main", head_branch="fix", base_sha="base", head_sha="head",
        files=[PullRequestFile(filename=name, status="modified", additions=1, deletions=0,
                               changes=1, patch=patch) for name, patch in files],
    )


class PromptBoundTests(unittest.TestCase):
    def test_small_diff_is_unchanged_apart_from_missing_patches(self):
        diff = LLMReviewService._build_diff(pull_request([("a.py", "+x = 1"), ("logo.png", None)]))
        self.assertIn("FILE: a.py", diff)
        self.assertIn("- logo.png (no patch from GitHub)", diff)

    def test_oversized_files_are_skipped_and_listed_while_later_files_fit(self):
        limit = llm_review_service.MAX_DIFF_CHARS
        diff = LLMReviewService._build_diff(pull_request([
            ("small.py", "+a = 1"), ("huge.py", "+" + "x" * limit), ("later.py", "+b = 2"),
        ]))
        self.assertLessEqual(len(diff), limit + 500)
        self.assertIn("FILE: small.py", diff)
        self.assertIn("FILE: later.py", diff)
        self.assertNotIn("FILE: huge.py", diff)
        self.assertIn("- huge.py (omitted: diff size limit)", diff)

    def test_description_is_bounded(self):
        self.assertEqual(bounded_description(None), "")
        self.assertEqual(bounded_description("short"), "short")
        long = bounded_description("d" * (llm_review_service.MAX_DESCRIPTION_CHARS + 10))
        self.assertTrue(long.endswith("[TRUNCATED: remaining description omitted]"))
        self.assertLess(len(long), llm_review_service.MAX_DESCRIPTION_CHARS + 100)


if __name__ == "__main__":
    unittest.main()
