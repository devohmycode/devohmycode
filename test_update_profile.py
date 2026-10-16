import unittest
from datetime import date
from io import BytesIO
from unittest.mock import patch
from xml.etree import ElementTree

import update_profile


class ProfileTests(unittest.TestCase):
    def test_uptime_handles_birthdays_month_borrowing_and_leap_years(self):
        cases = [
            (date(2026, 10, 16), "41 years, 0 months, 25 days"),
            (date(2026, 9, 20), "40 years, 11 months, 30 days"),
            (date(2026, 9, 21), "41 years, 0 months, 0 days"),
            (date(2026, 10, 21), "41 years, 1 month, 0 days"),
            (date(2026, 10, 22), "41 years, 1 month, 1 day"),
            (date(2026, 3, 1), "40 years, 5 months, 8 days"),
            (date(2028, 3, 1), "42 years, 5 months, 9 days"),
            (date(2027, 1, 1), "41 years, 3 months, 11 days"),
        ]
        for today, expected in cases:
            with self.subTest(today=today):
                self.assertEqual(update_profile.uptime(today), expected)

    def test_fetches_all_public_pages_and_excludes_forks_from_stars(self):
        profile = {"name": "OhMyCode", "created_at": "2025-11-22T04:21:25Z", "followers": 9, "node_id": "USER_ID"}
        first_page = [{"private": False, "fork": False, "stargazers_count": 1}] * 99
        first_page += [{"private": False, "fork": True, "stargazers_count": 500}]
        last_page = [
            {"private": False, "fork": False, "stargazers_count": 16},
            {"private": True, "fork": False, "stargazers_count": 999},
        ]
        with patch.object(update_profile, "request_json", side_effect=[profile, first_page, last_page]) as request, patch.object(
            update_profile, "fetch_contributions", return_value=(19, 8)
        ), patch.object(update_profile, "fetch_line_changes", return_value=(25, 10)):
            stats = update_profile.fetch_stats()
        self.assertEqual(request.call_args_list[-1].args, (
            "/users/devohmycode/repos?type=owner&per_page=100&page=2",
        ))
        self.assertEqual(stats["repos"], 101)
        self.assertEqual(stats["stars"], 115)
        self.assertEqual(stats["followers"], 9)
        self.assertEqual(stats["since"], "2025")
        self.assertEqual(stats["contributions"], 19)
        self.assertEqual(stats["commits"], 8)
        self.assertEqual(stats["additions"] - stats["deletions"], 15)

    def test_reads_public_calendar_and_rejects_unrecognized_markup(self):
        heading = b'<h2 class="f4" id="js-contribution-activity-description">\n4,390\ncontributions in 2026</h2>'
        with patch.object(update_profile, "urlopen", return_value=BytesIO(heading)):
            self.assertEqual(update_profile.fetch_calendar_total(2026), 4390)
        with patch.object(update_profile, "urlopen", return_value=BytesIO(b"Service unavailable")):
            with self.assertRaises(ValueError):
                update_profile.fetch_calendar_total(2026)

    def test_sums_each_year_and_excludes_private_commits(self):
        def collection(public_commits):
            return {"user": {"contributionsCollection": {"commitContributionsByRepository": [
                {"repository": {"isPrivate": False}, "contributions": {"totalCount": public_commits}},
                {"repository": {"isPrivate": True}, "contributions": {"totalCount": 999}},
            ]}}}

        with patch.object(update_profile, "datetime") as clock, patch.object(
            update_profile, "request_graphql", side_effect=[collection(3), collection(5)]
        ) as query, patch.object(update_profile, "fetch_calendar_total", side_effect=[12, 7]):
            clock.now.return_value.year = 2026
            self.assertEqual(update_profile.fetch_contributions(2025), (19, 8))
        self.assertEqual(query.call_args_list[0].args[1]["from"], "2025-01-01T00:00:00Z")
        self.assertEqual(query.call_args_list[0].args[1]["to"], "2025-12-31T23:59:59Z")
        self.assertEqual(query.call_args_list[1].args[1]["from"], "2026-01-01T00:00:00Z")

    def test_paginates_authored_line_changes_and_skips_forks_private_and_empty_repos(self):
        def history(nodes, more=False):
            return {"repository": {"isPrivate": False, "isFork": False, "defaultBranchRef": {"target": {
                "history": {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": "next"}}
            }}}}

        repositories = [
            {"name": "app", "private": False, "fork": False},
            {"name": "fork", "private": False, "fork": True},
            {"name": "private", "private": True, "fork": False},
            {"name": "empty", "private": False, "fork": False},
        ]
        responses = [
            history([{"additions": 20, "deletions": 7}], True),
            history([{"additions": 5, "deletions": 3}]),
            {"repository": {"isPrivate": False, "isFork": False, "defaultBranchRef": None}},
        ]
        with patch.object(update_profile, "request_graphql", side_effect=responses) as query:
            self.assertEqual(update_profile.fetch_line_changes(repositories, "USER_ID"), (25, 10))
        self.assertEqual(query.call_args_list[0].args[1]["author"], "USER_ID")
        self.assertEqual(query.call_args_list[1].args[1]["after"], "next")
        self.assertEqual(query.call_args_list[2].args[1]["name"], "empty")

    def test_rejects_graphql_partial_results_and_truncated_contributions(self):
        with patch.dict(update_profile.os.environ, {"GITHUB_TOKEN": "test-token"}), patch.object(
            update_profile, "request_json", return_value={"data": {"user": None}, "errors": [{"message": "Unavailable"}]}
        ):
            with self.assertRaises(RuntimeError):
                update_profile.request_graphql("query { user { id } }", {})
        groups = [{"repository": {"isPrivate": False}, "contributions": {"totalCount": 1}}] * 100
        response = {"user": {"contributionsCollection": {"commitContributionsByRepository": groups}}}
        with patch.object(update_profile, "request_graphql", return_value=response), patch.object(
            update_profile, "fetch_calendar_total", return_value=100
        ):
            with self.assertRaises(RuntimeError):
                update_profile.fetch_contributions(2025)

    def test_api_failure_keeps_previous_cards(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from urllib.error import URLError

        with TemporaryDirectory() as directory:
            folder = Path(directory)
            filenames = ("dark_mode.svg", "light_mode.svg")
            for name in filenames:
                (folder / name).write_text(f"previous {name}", encoding="utf-8")
            with patch.object(update_profile, "ROOT", folder), patch.object(
                update_profile, "fetch_stats", side_effect=URLError("unavailable")
            ):
                with self.assertRaises(URLError):
                    update_profile.main()
            for name in filenames:
                self.assertEqual((folder / name).read_text(), f"previous {name}")

    def test_keeps_portrait_profile_and_all_metrics_in_both_themes(self):
        stats = {
            "name": "Code & <friends>", "repos": 34, "stars": 115, "followers": 9, "since": "2025",
            "contributions": 4390, "commits": 4000, "additions": 100005, "deletions": 456,
        }
        for theme in ("dark", "light"):
            with self.subTest(theme=theme):
                root = ElementTree.fromstring(update_profile.render_card(stats, ["<&>"], theme))
                visible = " ".join(root.itertext())
                self.assertEqual(root.attrib["viewBox"], "0 0 840 500")
                self.assertEqual(root.attrib["role"], "img")
                self.assertIn("Code & <friends>", visible)
                self.assertIn("<&>", visible)
                self.assertIn("4,390", visible)
                self.assertIn("99,549", visible)
                self.assertIn("100,005++", visible)
                self.assertIn("456--", visible)
                self.assertIn("Contributions:", visible)
                self.assertIn("Commits:", visible)
                self.assertIn("Uptime:", visible)
                self.assertIn("Systems and Network Administrator", visible)
                self.assertIn("Claude-Code, Cursor, VS Code", visible)
                self.assertIn("ohmycodedev@gmail.com", visible)
                for removed in ("Focus:", "Tools:", "Projects:", "Repository:", "Updated on", "$ git commit"):
                    self.assertNotIn(removed, visible)
                self.assertNotIn("<image", ElementTree.tostring(root, encoding="unicode"))
                self.assertNotIn("$ whoami", visible)
                self.assertEqual(len(root.findall("{http://www.w3.org/2000/svg}rect")), 1)
                self.assertEqual(len(root.findall("{http://www.w3.org/2000/svg}circle")), 0)

    def test_main_generates_only_the_two_canonical_cards(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        stats = {
            "name": "OhMyCode", "repos": 34, "stars": 115, "followers": 9, "since": "2025",
            "contributions": 4390, "commits": 4000, "additions": 100005, "deletions": 456,
        }
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "assets").mkdir()
            (folder / "assets/portrait-original.txt").write_text("@\n", encoding="ascii")
            with patch.object(update_profile, "ROOT", folder), patch.object(
                update_profile, "fetch_stats", return_value=stats
            ) as fetch:
                update_profile.main()
            fetch.assert_called_once()
            self.assertEqual({path.name for path in folder.glob("*.svg")}, {"dark_mode.svg", "light_mode.svg"})
            for theme in ("dark", "light"):
                path = folder / f"{theme}_mode.svg"
                root = ElementTree.parse(path).getroot()
                self.assertEqual(root.attrib["viewBox"], "0 0 840 500")
                self.assertIn("4,390", " ".join(root.itertext()))


if __name__ == "__main__":
    unittest.main()
