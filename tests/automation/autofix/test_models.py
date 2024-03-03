import unittest

from pydantic import ValidationError

from seer.automation.autofix.models import (
    AutofixRequest,
    IssueDetails,
    RepoDefinition,
    SentryEvent,
    Stacktrace,
    StacktraceFrame,
)


class TestStacktraceHelpers(unittest.TestCase):
    def test_stacktrace_to_str(self):
        frames = [
            StacktraceFrame(
                function="main",
                filename="app.py",
                abs_path="/path/to/app.py",
                line_no=10,
                col_no=20,
                context=[(10, "    main()")],
                repo_name="my_repo",
                repo_id=1,
                in_app=True,
            ),
            StacktraceFrame(
                function="helper",
                filename="utils.py",
                abs_path="/path/to/utils.py",
                line_no=15,
                col_no=None,
                context=[(15, "    helper()")],
                repo_name="my_repo",
                repo_id=1,
                in_app=False,
            ),
        ]
        stacktrace = Stacktrace(frames=frames)
        expected_str = " helper in file utils.py in repo my_repo [Line 15] (Not in app)\n    helper()  <-- SUSPECT LINE\n------\n main in file app.py in repo my_repo [Line 10:20] (In app)\n    main()  <-- SUSPECT LINE\n------\n"
        self.assertEqual(stacktrace.to_str(), expected_str)

    def test_stacktrace_to_str_cutoff(self):
        frames = [
            StacktraceFrame(
                function="main",
                filename="app.py",
                abs_path="/path/to/app.py",
                line_no=10,
                col_no=20,
                context=[(10, "    main()")],
                repo_name="my_repo",
                repo_id=1,
                in_app=True,
            ),
            StacktraceFrame(
                function="helper",
                filename="utils.py",
                abs_path="/path/to/utils.py",
                line_no=15,
                col_no=None,
                context=[(15, "    helper()")],
                repo_name="my_repo",
                repo_id=1,
                in_app=False,
            ),
        ]
        stacktrace = Stacktrace(frames=frames)
        expected_str = " helper in file utils.py in repo my_repo [Line 15] (Not in app)\n    helper()  <-- SUSPECT LINE\n------\n"
        self.assertEqual(stacktrace.to_str(max_frames=1), expected_str)

    def test_stacktrace_frame_str(self):
        frame = StacktraceFrame(
            function="main",
            filename="app.py",
            abs_path="/path/to/app.py",
            line_no=10,
            col_no=20,
            context=[(10, "    main()")],
            repo_name="my_repo",
            repo_id=1,
            in_app=True,
        )
        expected_str = " main in file app.py in repo my_repo [Line 10:20] (In app)\n    main()  <-- SUSPECT LINE\n"
        stack_str = ""
        col_no_str = f":{frame.col_no}" if frame.col_no is not None else ""
        repo_str = f" in repo {frame.repo_name}" if frame.repo_name else ""
        stack_str += f" {frame.function} in file {frame.filename}{repo_str} [Line {frame.line_no}{col_no_str}] ({'In app' if frame.in_app else 'Not in app'})\n"
        for ctx in frame.context:
            is_suspect_line = ctx[0] == frame.line_no
            stack_str += f"{ctx[1]}{'  <-- SUSPECT LINE' if is_suspect_line else ''}\n"
        self.assertEqual(stack_str, expected_str)


class TestRepoDefinition(unittest.TestCase):
    def test_repo_definition_creation(self):
        repo_def = RepoDefinition(provider="github", owner="seer", name="automation")
        self.assertEqual(repo_def.provider, "github")
        self.assertEqual(repo_def.owner, "seer")
        self.assertEqual(repo_def.name, "automation")

    def test_repo_definition_uniqueness(self):
        repo_def1 = RepoDefinition(provider="github", owner="seer", name="automation")
        repo_def2 = RepoDefinition(provider="github", owner="seer", name="automation")
        self.assertEqual(hash(repo_def1), hash(repo_def2))

    def test_multiple_repos(self):
        repo_def1 = RepoDefinition(provider="github", owner="seer", name="automation")
        repo_def2 = RepoDefinition(provider="bitbucket", owner="seer", name="automation-tools")
        self.assertNotEqual(hash(repo_def1), hash(repo_def2))

    def test_repo_with_none_provider(self):
        repo_dict = {"provider": None, "owner": "seer", "name": "automation"}
        with self.assertRaises(ValidationError):
            RepoDefinition(**repo_dict)


class TestAutofixRequest(unittest.TestCase):
    def test_autofix_request_handler(self):
        repo_def = RepoDefinition(provider="github", owner="seer", name="automation")
        issue_details = IssueDetails(id=789, title="Test Issue", events=[SentryEvent(entries=[])])
        autofix_request = AutofixRequest(
            organization_id=123,
            project_id=456,
            repos=[repo_def],
            issue=issue_details,
        )
        self.assertEqual(autofix_request.organization_id, 123)
        self.assertEqual(autofix_request.project_id, 456)
        self.assertEqual(len(autofix_request.repos), 1)
        self.assertEqual(autofix_request.issue.id, 789)
        self.assertEqual(autofix_request.issue.title, "Test Issue")

    def test_autofix_request_with_duplicate_repos(self):
        repo_def1 = RepoDefinition(provider="github", owner="seer", name="automation")
        repo_def2 = RepoDefinition(provider="github", owner="seer", name="automation")
        with self.assertRaises(ValidationError):
            AutofixRequest(
                organization_id=123,
                project_id=456,
                repos=[repo_def1, repo_def2],
                issue=IssueDetails(id=789, title="Test Issue", events=[SentryEvent(entries=[])]),
            )

    def test_autofix_request_with_multiple_repos(self):
        repo_def1 = RepoDefinition(provider="github", owner="seer", name="automation")
        repo_def2 = RepoDefinition(provider="bitbucket", owner="seer", name="automation-tools")
        issue_details = IssueDetails(id=789, title="Test Issue", events=[SentryEvent(entries=[])])
        autofix_request = AutofixRequest(
            organization_id=123,
            project_id=456,
            repos=[repo_def1, repo_def2],
            issue=issue_details,
        )
        self.assertEqual(len(autofix_request.repos), 2)