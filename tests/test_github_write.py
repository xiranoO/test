import json
import unittest
from unittest.mock import patch

from issue_agent.github_write import GitHubCommentPublisher


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(
            {
                "id": 987,
                "html_url": "https://github.com/octocat/Hello-World/issues/7#issuecomment-987",
                "created_at": "2026-07-23T12:00:00Z",
            }
        ).encode()


class GitHubWriteTests(unittest.TestCase):
    @patch("issue_agent.github_write.urlopen", return_value=FakeResponse())
    def test_publisher_has_one_narrow_post_operation(self, mocked_urlopen):
        publisher = GitHubCommentPublisher("octocat/Hello-World", "write-token")
        result = publisher.create_issue_comment(7, "Reviewed response")
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertTrue(request.full_url.endswith("/issues/7/comments"))
        self.assertEqual(json.loads(request.data), {"body": "Reviewed response"})
        self.assertEqual(result["comment_id"], 987)


if __name__ == "__main__":
    unittest.main()
