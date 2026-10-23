import json
import unittest
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree

import update_profile

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=timezone.utc)
SVG = "{http://www.w3.org/2000/svg}"


def sample_stats(**overrides):
    stats = {
        "schema_version": 1, "generated_at": "2026-10-23T08:30:00Z", "login": "devohmycode",
        "name": "OhMyCode", "since": "2022", "account_created": "2025-11-22", "statistics_since": "2025-01-01",
        "repos": 34, "original_repos": 20, "fork_repos": 14, "archived_repos": 2,
        "stars": 115, "forks_received": 7,
        "top_repos": [{"name": "app", "url": "https://github.com/devohmycode/app", "stars": 40}],
        "languages": {"repositories": 20, "top": [{"name": "C#", "repos": 10}], "other": 6, "unspecified": 4},
        "followers": 9, "contributions": 4390, "commits": 4000,
        "years": [
            {"year": 2025, "contributions": 390, "commits": 400, "complete": True},
            {"year": 2026, "contributions": 4000, "commits": 3600, "complete": False},
        ],
        "recent": {"start": "2026-09-23", "end": "2026-10-22", "contributions": 321},
        "active_days": {"start": "2025-10-23", "end": "2026-10-22", "days": 200, "window_days": 365},
        "streaks": {"start": "2025-10-23", "end": "2026-10-22", "current": 5, "longest": 40},
        "collaboration": {"start": "2026-01-01", "end": "2026-10-23T08:30:00Z", "pull_requests": 8, "issues": 3, "reviews": 2},
        "additions": 100005, "deletions": 456,
        "line_cache": {"oldest_calculation": "2026-10-20T04:17:00Z", "max_age_days": 7},
        "trend": {"reference_date": "2026-09-23", "current_date": "2026-10-23", "available": False},
    }
    stats.update(overrides)
    return stats


def calendar_html(year, counts=(), total=None, skip=None):
    counts = dict(counts)
    cells, tips, day, index = [], [], date(year, 1, 1), 0
    while day.year == year:
        if day != skip:
            count = counts.get(day, 0)
            label = "No contributions" if not count else f"{count:,} contribution{'s' if count != 1 else ''}"
            cells.append(f'<td data-date="{day}" id="day-{index}" class="ContributionCalendar-day"></td>')
            tips.append(f'<tool-tip for="day-{index}" class="sr-only">{label} on a day.</tool-tip>')
        day += timedelta(days=1)
        index += 1
    total = sum(counts.values()) if total is None else total
    return (f'<h2 class="f4" id="js-contribution-activity-description">\n{total:,}\ncontributions in {year}</h2>'
            + "".join(cells) + "".join(tips))


def http_error(code, headers=None):
    return HTTPError("https://api.github.com/x", code, "error", headers or {}, None)


class RetryTests(unittest.TestCase):
    def setUp(self):
        update_profile.API.update(calls=0, waited=0.0)

    def test_retries_timeouts_and_server_errors_with_bounded_attempts(self):
        responses = [URLError("timeout"), http_error(502), BytesIO(b'{"ok": true}')]
        with patch.object(update_profile, "urlopen", side_effect=responses) as opened, patch.object(
            update_profile, "sleep"
        ) as sleep:
            self.assertEqual(update_profile.request_json("/users/x"), {"ok": True})
        self.assertEqual(opened.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 4.0])
        with patch.object(update_profile, "urlopen", side_effect=http_error(503)) as opened, patch.object(
            update_profile, "sleep"
        ):
            with self.assertRaises(HTTPError):
                update_profile.request_json("/users/x")
        self.assertEqual(opened.call_count, update_profile.MAX_ATTEMPTS)

    def test_respects_retry_after_and_rate_limit_reset(self):
        responses = [http_error(429, {"Retry-After": "7"}), BytesIO(b"[]")]
        with patch.object(update_profile, "urlopen", side_effect=responses), patch.object(update_profile, "sleep") as sleep:
            update_profile.request_json("/users/x")
        sleep.assert_called_once_with(7.0)
        reset = http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010"})
        with patch.object(update_profile, "urlopen", side_effect=[reset, BytesIO(b"[]")]), patch.object(
            update_profile, "sleep"
        ) as sleep, patch.object(update_profile.time, "time", return_value=1000):
            update_profile.request_json("/users/x")
        sleep.assert_called_once_with(11.0)

    def test_stops_when_the_wait_exceeds_the_budget(self):
        late = http_error(403, {"Retry-After": str(update_profile.MAX_RETRY_WAIT + 1)})
        with patch.object(update_profile, "urlopen", side_effect=[late]), patch.object(update_profile, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "budget"):
                update_profile.request_json("/users/x")
        sleep.assert_not_called()
        update_profile.API["waited"] = update_profile.RETRY_BUDGET - 1
        with patch.object(update_profile, "urlopen", side_effect=[http_error(500)]), patch.object(update_profile, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "budget"):
                update_profile.request_json("/users/x")

    def test_does_not_retry_authentication_validation_or_graphql_errors(self):
        for code in (401, 403, 404, 422):
            with self.subTest(code=code), patch.object(
                update_profile, "urlopen", side_effect=[http_error(code)]
            ) as opened, patch.object(update_profile, "sleep") as sleep:
                with self.assertRaises(HTTPError):
                    update_profile.request_json("/users/x")
                self.assertEqual(opened.call_count, 1)
                sleep.assert_not_called()
        with patch.dict(update_profile.os.environ, {"GITHUB_TOKEN": "test-token"}), patch.object(
            update_profile, "send_json", return_value={"data": {"user": None}, "errors": [{"message": "Unavailable"}]}
        ) as send:
            with self.assertRaisesRegex(RuntimeError, "Unavailable"):
                update_profile.request_graphql("query { user { id } }", {})
        self.assertEqual(send.call_count, 1)

    def test_retries_graphql_rate_limits(self):
        limited = {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]}
        with patch.dict(update_profile.os.environ, {"GITHUB_TOKEN": "test-token"}), patch.object(
            update_profile, "send_json", side_effect=[limited, {"data": {"ok": 1}}]
        ), patch.object(update_profile, "sleep") as sleep:
            self.assertEqual(update_profile.request_graphql("query", {}), {"ok": 1})
        sleep.assert_called_once_with(60.0)

    def test_gh_errors_are_explicit_without_exposing_tokens(self):
        failed = update_profile.subprocess.CompletedProcess([], 1, stdout="", stderr="gh: To use GitHub CLI, run gh auth login")
        with patch.dict(update_profile.os.environ, {}, clear=True), patch.object(
            update_profile.subprocess, "run", return_value=failed
        ):
            with self.assertRaisesRegex(RuntimeError, "gh auth login"):
                update_profile.request_graphql("query", {})
        with patch.dict(update_profile.os.environ, {}, clear=True), patch.object(
            update_profile.subprocess, "run", side_effect=FileNotFoundError
        ):
            with self.assertRaisesRegex(RuntimeError, "GITHUB_TOKEN"):
                update_profile.request_graphql("query", {})


class CollectionTests(unittest.TestCase):
    def setUp(self):
        update_profile.API.update(calls=0, waited=0.0)

    def test_paginate_rejects_null_pages_and_missing_or_repeated_cursors(self):
        def pages(*responses):
            iterator = iter(responses)
            return lambda after: next(iterator)

        more = {"nodes": [1], "pageInfo": {"hasNextPage": True, "endCursor": "a"}}
        for responses in ([None], [{**more, "pageInfo": {"hasNextPage": True, "endCursor": None}}], [more, more]):
            with self.subTest(responses=responses), self.assertRaises(RuntimeError):
                list(update_profile.paginate(pages(*responses), "test"))

    def test_repositories_are_paginated_deduplicated_public_and_complete(self):
        def repository(identifier, **fields):
            return {"id": identifier, "name": f"r{identifier}", "private": False, **fields}

        first_page = [repository(index) for index in range(100)]
        last_page = [
            repository(99), repository(100),
            repository(101, private=True), repository(102, private=True, visibility="internal"),
        ]
        with patch.object(update_profile, "request_json", side_effect=[first_page, last_page]) as request:
            repositories = update_profile.fetch_repositories(101)
        self.assertEqual(len(repositories), 101)
        self.assertEqual(request.call_args_list[-1].args, ("/users/devohmycode/repos?type=owner&per_page=100&page=2",))
        with patch.object(update_profile, "request_json", side_effect=[first_page, last_page]):
            with self.assertRaisesRegex(RuntimeError, "instead of 102"):
                update_profile.fetch_repositories(102)

    def test_stats_decompose_repositories_projects_and_languages(self):
        def repository(identifier, name, stars=0, fork=False, **fields):
            return {
                "id": identifier, "name": name, "private": False, "fork": fork, "archived": False,
                "stargazers_count": stars, "forks_count": 1, "language": None,
                "html_url": f"https://github.com/devohmycode/{name}", **fields,
            }

        repositories = [
            repository(1, "beta", 10, language="Python"), repository(2, "alpha", 10, language="C#", archived=True),
            repository(3, "gamma", 3, language="Python"), repository(4, "delta", 5, language="Rust"),
            repository(5, "eta", 0, language="Go"), repository(6, "zeta", 0),
            repository(7, "fork", 500, fork=True, language="C"),
        ]
        profile = {"name": None, "created_at": "2025-11-22T04:21:25Z", "followers": 9, "node_id": "U", "public_repos": 7}
        years = [{"year": 2025, "contributions": 12, "commits": 3, "complete": True},
                 {"year": 2026, "contributions": 7, "commits": 5, "complete": False}]
        days = {NOW.date() - timedelta(days=offset): 1 for offset in range(1, 366)}
        lines = {"additions": 25, "deletions": 10, "oldest_calculation": None}
        with patch.object(update_profile, "request_json", side_effect=[profile, repositories]), patch.object(
            update_profile, "fetch_contributions", return_value=(years, days)
        ) as contributions, patch.object(update_profile, "fetch_line_changes", return_value=lines), patch.object(
            update_profile, "fetch_collaboration", return_value={"pull_requests": 1}
        ):
            stats = update_profile.fetch_stats(NOW)
        contributions.assert_called_once_with(2025, NOW)
        self.assertEqual(stats["name"], "devohmycode")
        self.assertEqual(stats["generated_at"], "2026-10-23T08:30:00Z")
        self.assertEqual((stats["repos"], stats["original_repos"], stats["fork_repos"], stats["archived_repos"]), (7, 6, 1, 1))
        self.assertEqual(stats["original_repos"] + stats["fork_repos"], stats["repos"])
        self.assertEqual((stats["stars"], stats["forks_received"]), (28, 6))
        self.assertEqual([entry["name"] for entry in stats["top_repos"]], ["alpha", "beta", "delta"])
        self.assertEqual(stats["top_repos"][0]["url"], "https://github.com/devohmycode/alpha")
        self.assertEqual(stats["languages"], {
            "repositories": 6, "top": [{"name": "Python", "repos": 2}, {"name": "C#", "repos": 1}, {"name": "Go", "repos": 1}],
            "other": 1, "unspecified": 1,
        })
        self.assertEqual((stats["contributions"], stats["commits"]), (19, 8))
        self.assertEqual(stats["statistics_since"], "2025-01-01")
        self.assertEqual(stats["active_days"]["days"], 365)
        self.assertEqual(stats["additions"] - stats["deletions"], 15)

    def test_languages_and_popular_projects_handle_empty_profiles(self):
        self.assertEqual(update_profile.languages([]), {"repositories": 0, "top": [], "other": 0, "unspecified": 0})
        self.assertEqual(update_profile.share(0, 0), "n/a")

    def test_reads_public_calendar_days_and_rejects_partial_or_unknown_markup(self):
        counts = {date(2024, 2, 29): 4, date(2024, 12, 31): 1234}
        total, days = update_profile.parse_calendar(calendar_html(2024, counts), 2024)
        self.assertEqual(total, 1238)
        self.assertEqual(len(days), 366)
        self.assertEqual(days[date(2024, 12, 31)], 1234)
        for html in (
            "Service unavailable",
            calendar_html(2024, counts, total=1239),
            calendar_html(2024, counts, skip=date(2024, 6, 1)),
            calendar_html(2024, counts) + '<td data-date="2024-01-01" id="day-0"></td>',
            calendar_html(2024, counts).replace("contributions in 2024", "contributions in the last year"),
        ):
            with self.subTest(html=html[-80:]), self.assertRaises(ValueError):
                update_profile.parse_calendar(html, 2024)
        with patch.object(update_profile, "urlopen", return_value=BytesIO(calendar_html(2026, {date(2026, 1, 4): 4}).encode())):
            self.assertEqual(update_profile.fetch_calendar(2026)[0], 4)

    def test_keeps_yearly_detail_and_excludes_private_commits(self):
        def collection(public_commits):
            return {"user": {"contributionsCollection": {"commitContributionsByRepository": [
                {"repository": {"isPrivate": False}, "contributions": {"totalCount": public_commits}},
                {"repository": {"isPrivate": True}, "contributions": {"totalCount": 999}},
            ]}}}

        calendars = [(12, {date(2025, 1, 1): 12}), (7, {date(2026, 1, 1): 7})]
        new_year = datetime(2026, 1, 2, tzinfo=timezone.utc)
        with patch.object(update_profile, "request_graphql", side_effect=[collection(3), collection(5)]) as query, patch.object(
            update_profile, "fetch_calendar", side_effect=calendars
        ):
            years, days = update_profile.fetch_contributions(2025, new_year)
        self.assertEqual(years, [
            {"year": 2025, "contributions": 12, "commits": 3, "complete": True},
            {"year": 2026, "contributions": 7, "commits": 5, "complete": False},
        ])
        self.assertEqual(set(days), {date(2025, 1, 1), date(2026, 1, 1)})
        self.assertEqual(query.call_args_list[0].args[1]["from"], "2025-01-01T00:00:00Z")
        self.assertEqual(query.call_args_list[0].args[1]["to"], "2025-12-31T23:59:59Z")
        # An account created this year still needs last year's calendar for the 365-day window.
        with patch.object(update_profile, "request_graphql", side_effect=[collection(5)]), patch.object(
            update_profile, "fetch_calendar", side_effect=calendars
        ) as calendar:
            years, days = update_profile.fetch_contributions(2026, new_year)
        self.assertEqual([call.args[0] for call in calendar.call_args_list], [2025, 2026])
        self.assertEqual([row["year"] for row in years], [2026])
        self.assertIn(date(2025, 1, 1), days)

    def test_rejects_truncated_commit_collections(self):
        groups = [{"repository": {"isPrivate": False}, "contributions": {"totalCount": 1}}] * 100
        response = {"user": {"contributionsCollection": {"commitContributionsByRepository": groups}}}
        with patch.object(update_profile, "request_graphql", return_value=response), patch.object(
            update_profile, "fetch_calendar", return_value=(100, {})
        ):
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                update_profile.fetch_contributions(2026, NOW)

    def test_counts_public_collaboration_over_pages(self):
        def page(connection, nodes, more=False, cursor="next"):
            return {"user": {"contributionsCollection": {connection: {
                "nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": cursor},
            }}}}

        public, private = {"repository": {"isPrivate": False}}, {"repository": {"isPrivate": True}}
        responses = [
            page("pullRequestContributions", [{"pullRequest": public}] * 100, True),
            page("pullRequestContributions", [{"pullRequest": public}, {"pullRequest": private}]),
            page("issueContributions", [{"issue": public}, {"issue": private}]),
            page("pullRequestReviewContributions", []),
        ]
        with patch.object(update_profile, "request_graphql", side_effect=responses) as query:
            result = update_profile.fetch_collaboration(NOW)
        self.assertEqual((result["pull_requests"], result["issues"], result["reviews"]), (101, 1, 0))
        self.assertEqual(query.call_args_list[0].args[1]["from"], "2026-01-01T00:00:00Z")
        self.assertEqual(query.call_args_list[0].args[1]["to"], "2026-10-23T08:30:00Z")
        self.assertEqual(query.call_args_list[1].args[1]["after"], "next")
        with patch.object(update_profile, "request_graphql", return_value=page("pullRequestContributions", [{"pullRequest": None}])):
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                update_profile.fetch_collaboration(NOW)


class ActivityTests(unittest.TestCase):
    def days(self, end, active=(), length=400):
        return {end - timedelta(days=offset): int(end - timedelta(days=offset) in active) for offset in range(length)}

    def test_windows_end_yesterday_and_include_their_first_day(self):
        today = date(2026, 10, 23)
        yesterday = today - timedelta(days=1)
        active = {today, yesterday, today - timedelta(days=30), today - timedelta(days=31), today - timedelta(days=365)}
        days = self.days(today, active)
        result = update_profile.activity(days, today)
        self.assertEqual(result["recent"], {"start": "2026-09-23", "end": "2026-10-22", "contributions": 2})
        self.assertEqual(result["active_days"]["days"], 4)
        self.assertEqual(result["active_days"]["start"], "2025-10-23")
        self.assertEqual(update_profile.activity(self.days(today), today)["active_days"]["days"], 0)

    def test_handles_leap_days_and_new_year(self):
        result = update_profile.activity(self.days(date(2028, 3, 1), {date(2028, 2, 29)}), date(2028, 3, 1))
        self.assertEqual(result["recent"]["start"], "2028-01-31")
        self.assertEqual(result["recent"]["contributions"], 1)
        result = update_profile.activity(self.days(date(2027, 1, 1), {date(2026, 12, 31)}), date(2027, 1, 1))
        self.assertEqual(result["recent"]["end"], "2026-12-31")
        self.assertEqual(result["streaks"]["current"], 1)

    def test_streaks_stay_within_the_window(self):
        today = date(2027, 1, 3)
        across_years = {today - timedelta(days=offset) for offset in range(1, 6)}
        result = update_profile.activity(self.days(today, across_years | {today - timedelta(days=9)}), today)
        self.assertEqual((result["streaks"]["current"], result["streaks"]["longest"]), (5, 5))
        interrupted = update_profile.activity(self.days(today, {today - timedelta(days=2), today}), today)
        self.assertEqual((interrupted["streaks"]["current"], interrupted["streaks"]["longest"]), (0, 1))
        edge = {today - timedelta(days=offset) for offset in range(360, 380)}
        result = update_profile.activity(self.days(today, edge), today)
        self.assertEqual(result["streaks"]["longest"], 6)
        self.assertEqual(update_profile.activity(self.days(today), today)["streaks"], {
            "start": "2026-01-03", "end": "2027-01-02", "current": 0, "longest": 0,
        })

    def test_rejects_an_incomplete_calendar(self):
        days = self.days(date(2026, 10, 23))
        del days[date(2026, 5, 1)]
        with self.assertRaises(ValueError):
            update_profile.activity(days, date(2026, 10, 23))


class LineChangeTests(unittest.TestCase):
    repositories = [
        {"name": "app", "private": False, "fork": False},
        {"name": "fork", "private": False, "fork": True},
        {"name": "private", "private": True, "fork": False},
        {"name": "empty", "private": False, "fork": False},
    ]

    @staticmethod
    def head(identifier, oid="sha1", branch="main", private=False):
        return {"repository": {"id": identifier, "isPrivate": private, "isFork": False,
                               "defaultBranchRef": {"name": branch, "target": {"oid": oid}}}}

    @staticmethod
    def history(nodes, more=False):
        return {"repository": {"object": {"history": {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": "next"}}}}}

    def collect(self, responses, cache=None, now=NOW):
        with patch.object(update_profile, "request_graphql", side_effect=responses) as query:
            return update_profile.fetch_line_changes(self.repositories, "USER_ID", now, cache), query

    def test_paginates_authored_history_and_skips_forks_private_and_empty_repos(self):
        empty = {"repository": {"id": "R2", "isPrivate": False, "isFork": False, "defaultBranchRef": None}}
        cache = {"stale": {}}
        result, query = self.collect([
            self.head("R1"), self.history([{"additions": 20, "deletions": 7}], True),
            self.history([{"additions": 5, "deletions": 3}]), empty,
        ], cache)
        self.assertEqual((result["additions"], result["deletions"]), (25, 10))
        self.assertEqual(result["oldest_calculation"], "2026-10-23T08:30:00Z")
        self.assertEqual(query.call_args_list[1].args[1]["author"], "USER_ID")
        self.assertEqual(query.call_args_list[1].args[1]["oid"], "sha1")
        self.assertEqual(query.call_args_list[2].args[1]["after"], "next")
        self.assertEqual(query.call_args_list[3].args[1]["name"], "empty")
        self.assertEqual(list(cache), ["R1"])
        self.assertEqual(cache["R1"]["additions"], 25)

    def test_rejects_repositories_that_disappear_or_become_private(self):
        for response in ({"repository": None}, self.head("R1", private=True)):
            with self.subTest(response=response), self.assertRaisesRegex(RuntimeError, "visibility"):
                self.collect([response])
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.collect([self.head("R1"), {"repository": {"object": None}}])

    def test_reuses_unchanged_heads_for_seven_days_only(self):
        empty = {"repository": {"id": "R2", "isPrivate": False, "isFork": False, "defaultBranchRef": None}}
        entry = {"version": 1, "author": "USER_ID", "branch": "main", "head": "sha1",
                 "additions": 40, "deletions": 4, "computed_at": "2026-10-16T08:30:00Z"}
        warm, query = self.collect([self.head("R1"), empty], {"R1": dict(entry)})
        self.assertEqual((warm["additions"], warm["deletions"], query.call_count), (40, 4, 2))
        self.assertEqual(warm["oldest_calculation"], "2026-10-16T08:30:00Z")
        recomputed = [self.head("R1"), self.history([{"additions": 41, "deletions": 4}]), empty]
        for change in ({"head": "sha2"}, {"branch": "dev"}, {"author": "OTHER"}, {"computed_at": "2026-10-16T08:29:59Z"}):
            with self.subTest(change=change):
                cache = {"R1": {**entry, **change}}
                result, query = self.collect(list(recomputed), cache)
                self.assertEqual((result["additions"], query.call_count), (41, 3))
                self.assertEqual(cache["R1"]["computed_at"], "2026-10-23T08:30:00Z")

    def test_cache_files_are_ignored_when_absent_or_corrupt(self):
        with TemporaryDirectory() as directory, patch.object(update_profile, "ROOT", Path(directory)):
            self.assertEqual(update_profile.load_line_cache(), {})
            entry = {"version": 1, "author": "U", "branch": "main", "head": "s", "additions": 1, "deletions": 2,
                     "computed_at": "2026-10-20T00:00:00Z"}
            update_profile.save_line_cache({"R1": entry})
            self.assertEqual(update_profile.load_line_cache(), {"R1": entry})
            path = Path(directory) / ".cache" / "profile" / "line-changes.json"
            for content in ("{not json", json.dumps({"version": 0, "entries": {}}),
                            json.dumps({"version": 1, "entries": {"R1": {**entry, "additions": "1"}}}),
                            json.dumps({"version": 1, "entries": {"R1": {**entry, "computed_at": "yesterday"}}})):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    self.assertEqual(update_profile.load_line_cache(), {})


class HistoryTests(unittest.TestCase):
    def write(self, folder, day, **fields):
        snapshot = {"schema_version": 1, "login": "devohmycode", "generated_at": f"{day}T04:17:00Z",
                    "date": str(day), "stars": 100, "followers": 10, "repos": 30, **fields}
        (folder / f"{day}.json").write_text(json.dumps(snapshot), encoding="utf-8")

    def test_first_run_reports_insufficient_history(self):
        with TemporaryDirectory() as directory, patch.object(update_profile, "ROOT", Path(directory)):
            changes, trend = update_profile.update_history(sample_stats(), NOW)
        self.assertEqual(trend, {"reference_date": "2026-09-23", "current_date": "2026-10-23", "available": False})
        self.assertEqual([path.name for path in changes], ["2026-10-23.json"])
        self.assertEqual(json.loads(next(iter(changes.values())))["stars"], 115)

    def test_computes_signed_changes_replaces_today_and_purges_after_400_days(self):
        with TemporaryDirectory() as directory, patch.object(update_profile, "ROOT", Path(directory)):
            folder = Path(directory) / "data" / "history"
            folder.mkdir(parents=True)
            self.write(folder, date(2026, 9, 23), stars=100, followers=10, repos=34)
            self.write(folder, date(2026, 10, 23), stars=1)
            self.write(folder, date(2025, 9, 19))
            self.write(folder, date(2025, 9, 18), login="someone-else")
            changes, trend = update_profile.update_history(sample_stats(), NOW)
        self.assertEqual(trend["changes"], {"stars": 15, "followers": -1, "repos": 0})
        self.assertIsNone(changes[folder / "2025-09-18.json"])
        self.assertNotIn(folder / "2025-09-19.json", changes)
        self.assertEqual(json.loads(changes[folder / "2026-10-23.json"])["stars"], 115)

    def test_rejects_incompatible_history(self):
        for fields in ({"schema_version": 2}, {"login": "someone-else"}, {"stars": "many"}):
            with self.subTest(fields=fields), TemporaryDirectory() as directory, patch.object(
                update_profile, "ROOT", Path(directory)
            ):
                folder = Path(directory) / "data" / "history"
                folder.mkdir(parents=True)
                self.write(folder, date(2026, 9, 23), **fields)
                with self.assertRaisesRegex(ValueError, "Incompatible"):
                    update_profile.update_history(sample_stats(), NOW)


class RenderTests(unittest.TestCase):
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

    def test_keeps_portrait_profile_and_all_metrics_in_both_themes(self):
        stats = sample_stats(name="Code & <friends>")
        texts = {}
        for theme in ("dark", "light"):
            with self.subTest(theme=theme):
                root = ElementTree.fromstring(update_profile.render_card(stats, ["<&>"], theme))
                visible = " ".join(root.itertext())
                texts[theme] = visible
                self.assertEqual(root.attrib["viewBox"], "0 0 840 500")
                self.assertEqual(root.attrib["role"], "img")
                for expected in (
                    "Code & <friends>", "<&>", "4,390", "99,549", "100,005++", "456--", "Contributions:",
                    "Commits:", "Last 30 days:", "321", "Active days (365d):", "200 (55%)", "Net line changes:",
                    "Uptime:", "41 years, 1 month, 2 days", "Since 2022", "Systems and Network Administrator",
                    "Claude-Code, Cursor, VS Code", "ohmycodedev@gmail.com",
                ):
                    self.assertIn(expected, visible)
                self.assertNotIn("Lines of Code", visible)
                description = root.find(f"{SVG}desc").text
                for expected in ("34 public repositories (20 original, 14 forks)", "4390 contributions",
                                 "321 contributions in the 30 days to 2026-10-22", "200 active days out of 365",
                                 "net line changes 99549 (100005 added, 456 deleted)"):
                    self.assertIn(expected, description)
                linkedin = root.find(f"{SVG}a")
                self.assertEqual(linkedin.attrib["href"], "https://www.linkedin.com/in/g-decker")
                self.assertIn("linkedin.com/in/g-decker", "".join(linkedin.itertext()))
                for removed in ("Focus:", "Tools:", "Projects:", "Repository:", "Updated on", "$ git commit", "$ whoami"):
                    self.assertNotIn(removed, visible)
                self.assertNotIn("<image", ElementTree.tostring(root, encoding="unicode"))
                self.assertEqual(len(root.findall(f"{SVG}rect")), 1)
                self.assertEqual(len(root.findall(f"{SVG}circle")), 0)
        self.assertEqual(texts["dark"], texts["light"])

    def test_large_negative_and_empty_values_stay_readable(self):
        stats = sample_stats(
            contributions=12_345_678, additions=10, deletions=2_000_010,
            recent={"start": "2026-09-23", "end": "2026-10-22", "contributions": 0},
            active_days={"start": "2025-10-23", "end": "2026-10-22", "days": 0, "window_days": 365},
        )
        root = ElementTree.fromstring(update_profile.render_card(stats, [], "dark"))
        visible = " ".join(root.itertext())
        for expected in ("12.35M", "-2.00M", "2.00M--", "0 (0%)"):
            self.assertIn(expected, visible)
        self.assertIn("12345678 contributions", root.find(f"{SVG}desc").text)
        self.assertIn("-2000000", root.find(f"{SVG}desc").text)

    def test_report_lists_every_metric_with_links_and_limits(self):
        stats = sample_stats(name="A|B *bold*", top_repos=[
            {"name": "app_one", "url": "https://github.com/devohmycode/app_one", "stars": 1},
        ])
        report = update_profile.render_report(stats)
        for expected in (
            "# GitHub statistics — A\\|B \\*bold\\*", "[app\\_one](https://github.com/devohmycode/app_one) — 1 star\n",
            "| Public repositories | 34 |", "| Archived original repositories | 2 |", "| C\\# | 10 | 50% |",
            "| Other | 6 | 30% |", "| Not specified | 4 | 20% |", "| 2026 | 4,000 | 3,600 | in progress |",
            "| **Total** | **4,390** | **4,000** | |", "(2026-09-23 to 2026-10-22): 321",
            "200 of 365 (55%)", "**Current streak** (ending 2026-10-22): 5 days", "not an all-time record",
            "**Pull requests opened:** 8", "**Issues opened:** 3", "review contributions:** 2",
            "Insufficient history: a snapshot from 2026-09-23", "**Net:** 99,549",
            "dates from 2026-10-20T04:17:00Z", "up to 7 days", "custom profile year",
            "created on 2025-11-22", "at most 100 repositories", "[data/stats.json](data/stats.json)",
        ):
            self.assertIn(expected, report)
        trend = {"reference_date": "2026-09-23", "current_date": "2026-10-23", "available": True,
                 "changes": {"stars": 15, "followers": -1, "repos": 0}}
        report = update_profile.render_report(sample_stats(trend=trend, top_repos=[], line_cache={
            "oldest_calculation": None, "max_age_days": 7}))
        for expected in ("| Stars | +15 |", "| Followers | -1 |", "| Public repositories | +0 |",
                         "changes in stock", "No original public repository yet."):
            self.assertIn(expected, report)
        self.assertNotIn("Insufficient history", report)


class PublicationTests(unittest.TestCase):
    outputs = ("dark_mode.svg", "light_mode.svg", "STATS.md", "data/stats.json")

    def prepare(self, folder):
        (folder / "assets").mkdir()
        (folder / "assets/portrait-original.txt").write_text("@\n", encoding="ascii")
        (folder / "data").mkdir()
        for name in self.outputs:
            (folder / name).write_text(f"previous {name}", encoding="utf-8")

    def assert_previous(self, folder):
        for name in self.outputs:
            self.assertEqual((folder / name).read_text(encoding="utf-8"), f"previous {name}")
        self.assertEqual(list(folder.rglob("*.tmp")), [])
        self.assertEqual(list((folder / "data" / "history").glob("*")), [])

    def test_failures_keep_the_previous_consistent_outputs(self):
        failures = [
            ("collection", patch.object(update_profile, "fetch_stats", side_effect=URLError("unavailable"))),
            ("rendering", patch.object(update_profile, "render_report", side_effect=KeyError("trend"))),
        ]
        for label, failure in failures:
            with self.subTest(label=label), TemporaryDirectory() as directory:
                folder = Path(directory)
                self.prepare(folder)
                with patch.object(update_profile, "ROOT", folder), patch.object(
                    update_profile, "fetch_stats", return_value=sample_stats()
                ), failure:
                    with self.assertRaises((URLError, KeyError)):
                        update_profile.main(NOW)
                self.assert_previous(folder)

    def test_write_error_on_second_file_restores_every_output(self):
        original = Path.replace
        calls = []

        def replace(self, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("disk full")
            return original(self, target)

        with TemporaryDirectory() as directory:
            folder = Path(directory)
            self.prepare(folder)
            with patch.object(update_profile, "ROOT", folder), patch.object(
                update_profile, "fetch_stats", return_value=sample_stats()
            ), patch.object(Path, "replace", replace):
                with self.assertRaisesRegex(OSError, "disk full"):
                    update_profile.main(NOW)
            self.assert_previous(folder)

    def test_publish_restores_deleted_files(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "old.json").write_text("old", encoding="utf-8")
            changes = {folder / "old.json": None, folder / "bad.svg": "<svg"}
            with self.assertRaises(ElementTree.ParseError):
                update_profile.publish(changes)
            self.assertEqual((folder / "old.json").read_text(encoding="utf-8"), "old")
            self.assertFalse((folder / "bad.svg").exists())

    def test_main_publishes_a_consistent_snapshot_and_is_repeatable(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "assets").mkdir()
            (folder / "assets/portrait-original.txt").write_text("@\n", encoding="ascii")
            stats = sample_stats()
            stats.pop("trend")

            def run():
                with patch.object(update_profile, "ROOT", folder), patch.object(
                    update_profile, "fetch_stats", side_effect=lambda now, cache: json.loads(json.dumps(stats))
                ) as fetch:
                    update_profile.main(NOW)
                fetch.assert_called_once()
                return {path.relative_to(folder).as_posix(): path.read_bytes()
                        for path in folder.rglob("*") if path.is_file() and ".cache" not in path.parts}

            first = run()
            self.assertEqual(set(first), {
                "assets/portrait-original.txt", "dark_mode.svg", "light_mode.svg", "STATS.md",
                "data/stats.json", "data/history/2026-10-23.json",
            })
            snapshot = json.loads(first["data/stats.json"])
            self.assertEqual(snapshot["generated_at"], "2026-10-23T08:30:00Z")
            self.assertFalse(snapshot["trend"]["available"])
            for theme in ("dark", "light"):
                root = ElementTree.fromstring(first[f"{theme}_mode.svg"])
                self.assertIn("4,390", " ".join(root.itertext()))
            self.assertIn(b"4,390", first["STATS.md"])
            self.assertNotIn(b"\r\n", first["STATS.md"])
            self.assertEqual(run(), first)


if __name__ == "__main__":
    unittest.main()
