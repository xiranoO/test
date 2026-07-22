import tempfile
import unittest
from pathlib import Path

from issue_agent.github_readonly import (
    GitHubReadOnlyClient,
    GitHubReadOnlyError,
    PublicRepositoryCache,
    validate_repository,
)


class StubGitHubClient(GitHubReadOnlyClient):
    def __init__(self, responses):
        super().__init__("octocat/Hello-World")
        self.responses = iter(responses)
        self.calls = []

    def _get_json(self, path, params=None):
        self.calls.append((path, params))
        return next(self.responses)


class GitHubReadOnlyTests(unittest.TestCase):
    def test_repository_validation_rejects_command_like_input(self):
        with self.assertRaises(ValueError):
            validate_repository("owner/repo;Remove-Item")

    def test_get_issue_maps_labels(self):
        client = StubGitHubClient([
            {
                "number": 7,
                "title": "A bug",
                "body": None,
                "labels": [{"name": "bug"}],
                "state": "open",
            }
        ])
        issue = client.get_issue(7)
        self.assertEqual(issue.number, 7)
        self.assertEqual(issue.labels, ["bug"])
        self.assertEqual(client.calls[0][0], "/repos/octocat/Hello-World/issues/7")

    def test_issue_history_filters_pull_requests(self):
        client = StubGitHubClient([[
            {"number": 1, "title": "Issue", "body": "", "labels": [], "state": "open"},
            {
                "number": 2,
                "title": "PR",
                "body": "",
                "labels": [],
                "state": "open",
                "pull_request": {"url": "example"},
            },
        ]])
        issues = client.list_issues(limit=10)
        self.assertEqual([item.number for item in issues], [1])

    def test_open_issue_query_uses_open_state(self):
        client = StubGitHubClient([[]])
        client.list_issues(limit=10, state="open")
        self.assertEqual(client.calls[0][1]["state"], "open")

    def test_issue_comments_include_author_identity(self):
        client = StubGitHubClient([[
            {
                "user": {"login": "maintainer"},
                "body": "This was fixed in the maintenance branch.",
                "created_at": "2022-01-02T03:04:05Z",
                "author_association": "MEMBER",
            }
        ]])
        comments = client.list_issue_comments(7)
        self.assertEqual(comments[0].author, "maintainer")
        self.assertEqual(comments[0].author_association, "MEMBER")
        self.assertEqual(
            client.calls[0][0],
            "/repos/octocat/Hello-World/issues/7/comments",
        )

    def test_pull_request_number_is_rejected(self):
        client = StubGitHubClient([{"number": 9, "pull_request": {}}])
        with self.assertRaises(GitHubReadOnlyError):
            client.get_issue(9)

    def test_cache_reuses_existing_git_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "octocat" / "Hello-World" / ".git"
            target.mkdir(parents=True)
            snapshot = PublicRepositoryCache(Path(temp_dir)).ensure("octocat/Hello-World")
            self.assertTrue(snapshot.reused)
            self.assertEqual(snapshot.path, target.parent.resolve())


if __name__ == "__main__":
    unittest.main()
