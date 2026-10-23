"""Generate the GitHub profile cards, report and data snapshot from public GitHub data."""

import json
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from html import escape
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent
USER = "devohmycode"
GITHUB_SINCE = "2022"
BIRTHDAY = date(1985, 9, 21)
JOB_TITLE = "Systems and Network Administrator"
IDE = "Claude-Code, Cursor, VS Code"
EMAIL = "ohmycodedev@gmail.com"
LINKEDIN = "https://www.linkedin.com/in/g-decker"
THEMES = {
    "dark": {
        "background": "#0d1117", "border": "#30363d", "portrait": "#8b949e",
        "heading": "#58a6ff", "key": "#ffa657", "value": "#c9d1d9",
        "dots": "#484f58", "added": "#3fb950", "deleted": "#f85149",
    },
    "light": {
        "background": "#ffffff", "border": "#d0d7de", "portrait": "#57606a",
        "heading": "#0969da", "key": "#953800", "value": "#24292f",
        "dots": "#afb8c1", "added": "#1a7f37", "deleted": "#cf222e",
    },
}
SCHEMA_VERSION = 1
RECENT_DAYS = 30
ACTIVE_WINDOW_DAYS = 365
TREND_DAYS = 30
HISTORY_DAYS = 400
TREND_FIELDS = ("stars", "followers", "repos")
MAX_ATTEMPTS = 4
MAX_RETRY_WAIT = 60
RETRY_BUDGET = 120
LINE_CACHE_VERSION = 1
LINE_CACHE_MAX_AGE = timedelta(days=7)
API = {"calls": 0, "waited": 0.0}
sleep = time.sleep


class TransientError(RuntimeError):
    """A failure that GitHub asks us to retry, optionally after `wait` seconds."""

    def __init__(self, message, wait=None):
        super().__init__(message)
        self.wait = wait


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def retry_delay(error, attempt):
    """Return the seconds to wait before retrying `error`, or None when it is permanent."""
    if isinstance(error, HTTPError):
        headers = error.headers or {}
        if error.code in (403, 429):
            if (retry_after := headers.get("Retry-After")) is not None:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    return 60.0
            if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
                return max(0.0, float(headers["X-RateLimit-Reset"]) - time.time()) + 1
            if error.code == 429:
                return 60.0
            return None
        return float(2 ** attempt) if error.code >= 500 else None
    if isinstance(error, TransientError):
        return float(2 ** attempt) if error.wait is None else error.wait
    if isinstance(error, (URLError, TimeoutError, ConnectionError, HTTPException)):
        return float(2 ** attempt)
    return None


def with_retries(action, description):
    """Run `action`, retrying temporary GitHub failures within the workflow's waiting budget."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        API["calls"] += 1
        try:
            return action()
        except Exception as error:
            delay = retry_delay(error, attempt)
            if delay is None or attempt == MAX_ATTEMPTS:
                raise
            if delay > MAX_RETRY_WAIT or API["waited"] + delay > RETRY_BUDGET:
                raise RuntimeError(
                    f"{description}: waiting {delay:.0f}s for GitHub exceeds the update budget; "
                    "existing outputs were kept."
                ) from error
            API["waited"] += delay
            sleep(delay)


def send_json(path, payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": f"{USER}-profile",
    }
    if token := os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(f"https://api.github.com{path}", data=data, headers=headers), timeout=30) as response:
        return json.load(response)


def request_json(path, payload=None):
    return with_retries(lambda: send_json(path, payload), f"GitHub API {path.split('?')[0]}")


def run_gh(payload):
    try:
        result = subprocess.run(
            ["gh", "api", "graphql", "--input", "-"], input=json.dumps(payload),
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError as error:
        raise RuntimeError("GitHub GraphQL requires GITHUB_TOKEN, GH_TOKEN or an authenticated gh CLI.") from error
    except subprocess.TimeoutExpired as error:
        raise TransientError("gh api graphql timed out.") from error
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        response = None
    if isinstance(response, dict) and ("data" in response or "errors" in response):
        return response
    message = f"gh api graphql failed (exit {result.returncode}): {result.stderr.strip()[:300]}"
    if re.search(r"\bHTTP 5\d\d\b", result.stderr):
        raise TransientError(message)
    raise RuntimeError(message)


def request_graphql(query, variables):
    payload = {"query": query, "variables": variables}

    def send():
        if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
            response = send_json("/graphql", payload)
        else:
            response = run_gh(payload)
        if errors := response.get("errors"):
            message = "GitHub GraphQL: " + "; ".join(error.get("message", "unknown error") for error in errors)
            if any(error.get("type") == "RATE_LIMITED" for error in errors):
                raise TransientError(message, 60.0)
            raise RuntimeError(message)
        if response.get("data") is None:
            raise RuntimeError("GitHub GraphQL returned no data; existing outputs were kept.")
        return response["data"]

    return with_retries(send, "GitHub GraphQL")


def paginate(fetch_page, description):
    """Yield each page's nodes, rejecting null pages and missing or repeated cursors."""
    cursor, seen = None, set()
    while True:
        page = fetch_page(cursor)
        if page is None or page.get("nodes") is None or page.get("pageInfo") is None:
            raise RuntimeError(f"{description}: incomplete GitHub response; existing outputs were kept.")
        yield page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return
        cursor = page["pageInfo"]["endCursor"]
        if not cursor or cursor in seen:
            raise RuntimeError(f"{description}: missing or repeated page cursor; existing outputs were kept.")
        seen.add(cursor)


def parse_calendar(html, year):
    """Return the heading total and daily counts of GitHub's public calendar for `year`."""
    heading = re.search(r'<h2\b[^>]*\bid="js-contribution-activity-description"[^>]*>(.*?)</h2>', html, re.S)
    count = re.search(rf'([\d,]+)\s+contributions?\s+in\s+{year}\b', heading[1]) if heading else None
    if count is None:
        raise ValueError(f"Cannot read the public contribution total for {year}; existing outputs were kept.")
    total = int(count[1].replace(",", ""))
    tooltips = {}
    for attributes, text in re.findall(r'<tool-tip\b([^>]*)>([^<]*)</tool-tip>', html):
        target = re.search(r'\bfor="([^"]+)"', attributes)
        number = re.fullmatch(r'\s*(No|[\d,]+) contributions? on [^<]*', text)
        if target and number:
            tooltips[target[1]] = 0 if number[1] == "No" else int(number[1].replace(",", ""))
    days = {}
    for cell in re.findall(r'<td\b[^>]*\bdata-date="[^"]*"[^>]*>', html):
        day = date.fromisoformat(re.search(r'\bdata-date="([^"]+)"', cell)[1])
        identifier = re.search(r'\bid="([^"]+)"', cell)
        if day in days or day.year != year or identifier is None or identifier[1] not in tooltips:
            raise ValueError(f"Unrecognized public calendar day {day} in {year}; existing outputs were kept.")
        days[day] = tooltips[identifier[1]]
    expected = (date(year, 12, 31) - date(year, 1, 1)).days + 1
    if len(days) != expected or sum(days.values()) != total:
        raise ValueError(f"The public calendar for {year} is partial or inconsistent; existing outputs were kept.")
    return total, days


def fetch_calendar(year):
    url = f"https://github.com/users/{USER}/contributions?from={year}-01-01&to={year}-12-31"
    # shortcut: public calendars use GitHub's HTML; update the selectors if its markup changes.
    request = Request(url, headers={"User-Agent": f"{USER}-profile", "Accept-Language": "en-US"})

    def send():
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")

    return parse_calendar(with_retries(send, f"Public contribution calendar {year}"), year)


def fetch_contributions(since, now):
    """Return yearly public contributions and commits from `since`, plus the calendar's daily counts."""
    query = """query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          commitContributionsByRepository(maxRepositories: 100) {
            repository { isPrivate }
            contributions { totalCount }
          }
        }
      }
    }"""
    window_start = now.date() - timedelta(days=ACTIVE_WINDOW_DAYS)
    years, days = [], {}
    for year in range(min(since, window_start.year), now.year + 1):
        total, daily = fetch_calendar(year)
        days.update(daily)
        if year < since:
            continue
        data = request_graphql(query, {
            "login": USER, "from": f"{year}-01-01T00:00:00Z", "to": f"{year}-12-31T23:59:59Z",
        })
        if data.get("user") is None:
            raise RuntimeError(f"The {year} commit collection is missing; existing outputs were kept.")
        groups = data["user"]["contributionsCollection"]["commitContributionsByRepository"]
        # shortcut: GitHub groups up to 100 repositories/year; require a complete source before exceeding this.
        if len(groups) >= 100:
            raise RuntimeError(f"The {year} commit collection may be truncated; existing outputs were kept.")
        if any(group.get("repository") is None for group in groups):
            raise RuntimeError(f"The {year} commit collection has an unreadable repository; existing outputs were kept.")
        commits = sum(group["contributions"]["totalCount"] for group in groups if not group["repository"]["isPrivate"])
        years.append({"year": year, "contributions": total, "commits": commits, "complete": year < now.year})
    return years, days


def activity(days, today):
    """Summarize complete days ending yesterday: 30-day total, active days and streaks over 365 days."""
    end = today - timedelta(days=1)

    def window(length):
        start = end - timedelta(days=length - 1)
        counts = [days.get(start + timedelta(days=offset)) for offset in range(length)]
        if None in counts:
            raise ValueError(f"The daily calendar does not cover {start} to {end}; existing outputs were kept.")
        return start.isoformat(), counts

    recent_start, recent = window(RECENT_DAYS)
    year_start, counts = window(ACTIVE_WINDOW_DAYS)
    current = longest = run = 0
    for count in counts:
        run = run + 1 if count else 0
        longest = max(longest, run)
    for count in reversed(counts):
        if not count:
            break
        current += 1
    return {
        "recent": {"start": recent_start, "end": end.isoformat(), "contributions": sum(recent)},
        "active_days": {
            "start": year_start, "end": end.isoformat(), "days": sum(1 for count in counts if count),
            "window_days": ACTIVE_WINDOW_DAYS,
        },
        "streaks": {"start": year_start, "end": end.isoformat(), "current": current, "longest": longest},
    }


COLLABORATION = {
    "pull_requests": ("pullRequestContributions", "pullRequest { repository { isPrivate } }", ("pullRequest",)),
    "issues": ("issueContributions", "issue { repository { isPrivate } }", ("issue",)),
    "reviews": ("pullRequestReviewContributions", "repository { isPrivate }", ()),
}


def fetch_collaboration(now):
    """Count this year's public pull requests and issues opened, and review contributions."""
    variables = {"login": USER, "from": f"{now.year}-01-01T00:00:00Z", "to": iso(now)}
    result = {"start": f"{now.year}-01-01", "end": iso(now)}
    for key, (connection, fields, path) in COLLABORATION.items():
        query = f"""query($login: String!, $from: DateTime!, $to: DateTime!, $after: String) {{
          user(login: $login) {{
            contributionsCollection(from: $from, to: $to) {{
              {connection}(first: 100, after: $after) {{
                nodes {{ {fields} }}
                pageInfo {{ hasNextPage endCursor }}
              }}
            }}
          }}
        }}"""

        def page(after, query=query, connection=connection):
            user = request_graphql(query, {**variables, "after": after}).get("user")
            return user and user["contributionsCollection"][connection]

        result[key] = 0
        for nodes in paginate(page, f"GitHub {connection}"):
            for node in nodes:
                for step in path:
                    node = node and node.get(step)
                repository = node and node.get("repository")
                if repository is None:
                    raise RuntimeError(f"GitHub {connection} has an unreadable repository; existing outputs were kept.")
                result[key] += not repository["isPrivate"]
    return result


def load_line_cache():
    """Return valid cached line totals by repository ID, or nothing when the cache is absent or corrupt."""
    try:
        data = json.loads((ROOT / ".cache" / "profile" / "line-changes.json").read_text(encoding="utf-8"))
        entries = data["entries"]
        valid = data["version"] == LINE_CACHE_VERSION and isinstance(entries, dict) and all(
            all(isinstance(entry[key], str) for key in ("author", "branch", "head", "computed_at"))
            and all(type(entry[key]) is int and entry[key] >= 0 for key in ("additions", "deletions"))
            and datetime.fromisoformat(entry["computed_at"]).tzinfo is not None
            for entry in entries.values()
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return entries if valid else {}


def save_line_cache(entries):
    path = ROOT / ".cache" / "profile" / "line-changes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"version": LINE_CACHE_VERSION, "entries": entries}, indent=1), encoding="utf-8")
    temporary.replace(path)


def fetch_line_changes(repositories, author_id, now, cache=None):
    """Return authored additions and deletions on default branches, reusing recent cache entries.

    When `cache` is given, it is replaced with the entries used for this collection.
    """
    head_query = """query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        id isPrivate isFork
        defaultBranchRef { name target { oid } }
      }
    }"""
    history_query = """query($owner: String!, $name: String!, $oid: GitObjectID!, $author: ID!, $after: String) {
      repository(owner: $owner, name: $name) {
        object(oid: $oid) { ... on Commit {
          history(first: 100, after: $after, author: {id: $author}) {
            nodes { additions deletions }
            pageInfo { hasNextPage endCursor }
          }
        } }
      }
    }"""
    entries = {}
    for repository in repositories:
        if repository["fork"] or repository["private"]:
            continue
        name = repository["name"]
        head = request_graphql(head_query, {"owner": USER, "name": name})["repository"]
        if head is None or head["isPrivate"] or head["isFork"]:
            raise RuntimeError(f"Repository {name} changed visibility or disappeared during collection; existing outputs were kept.")
        branch = head["defaultBranchRef"]
        if branch is None:
            continue
        signature = {"version": LINE_CACHE_VERSION, "author": author_id, "branch": branch["name"], "head": branch["target"]["oid"]}
        cached = (cache or {}).get(head["id"])
        # shortcut: an unchanged head reuses its totals for up to 7 days, so a reattribution of
        # old commits can take that long to appear; then the history is collected again in full.
        if cached and all(cached.get(key) == value for key, value in signature.items()) and (
            timedelta(0) <= now - datetime.fromisoformat(cached["computed_at"]) <= LINE_CACHE_MAX_AGE
        ):
            entries[head["id"]] = cached
            continue

        def page(after, name=name, oid=signature["head"]):
            target = request_graphql(history_query, {
                "owner": USER, "name": name, "oid": oid, "author": author_id, "after": after,
            })["repository"]
            return target and target["object"] and target["object"]["history"]

        additions = deletions = 0
        for nodes in paginate(page, f"Commit history of {name}"):
            additions += sum(commit["additions"] for commit in nodes)
            deletions += sum(commit["deletions"] for commit in nodes)
        entries[head["id"]] = {**signature, "additions": additions, "deletions": deletions, "computed_at": iso(now)}
    if cache is not None:
        cache.clear()
        cache.update(entries)
    return {
        "additions": sum(entry["additions"] for entry in entries.values()),
        "deletions": sum(entry["deletions"] for entry in entries.values()),
        "oldest_calculation": min((entry["computed_at"] for entry in entries.values()), default=None),
    }


def fetch_repositories(expected):
    """Return the owner's public repositories, deduplicated by ID and checked against the profile count."""
    repositories, page = {}, 1
    while True:
        batch = request_json(f"/users/{USER}/repos?type=owner&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise RuntimeError("The repository list is unreadable; existing outputs were kept.")
        for repository in batch:
            if not repository["private"] and repository.get("visibility", "public") == "public":
                repositories.setdefault(repository["id"], repository)
        if len(batch) < 100:
            break
        page += 1
    if len(repositories) != expected:
        raise RuntimeError(
            f"Found {len(repositories)} public repositories instead of {expected}; "
            "the list changed during collection and existing outputs were kept."
        )
    return list(repositories.values())


def languages(originals):
    counts, unspecified = {}, 0
    for repository in originals:
        if language := repository.get("language"):
            counts[language] = counts.get(language, 0) + 1
        else:
            unspecified += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    return {
        "repositories": len(originals),
        "top": [{"name": name, "repos": count} for name, count in ranked[:3]],
        "other": sum(count for _, count in ranked[3:]),
        "unspecified": unspecified,
    }


def fetch_stats(now, line_cache=None):
    """Collect every public statistic for the collection instant `now` (UTC)."""
    profile = request_json(f"/users/{USER}")
    repositories = fetch_repositories(profile["public_repos"])
    originals = [repository for repository in repositories if not repository["fork"]]
    years, days = fetch_contributions(int(profile["created_at"][:4]), now)
    lines = fetch_line_changes(repositories, profile["node_id"], now, line_cache)
    popular = sorted(originals, key=lambda repository: (-repository["stargazers_count"], repository["name"].lower()))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "login": USER,
        "name": profile["name"] or USER,
        "since": GITHUB_SINCE,
        "account_created": profile["created_at"][:10],
        "statistics_since": f"{years[0]['year']}-01-01",
        "repos": len(repositories),
        "original_repos": len(originals),
        "fork_repos": len(repositories) - len(originals),
        "archived_repos": sum(1 for repository in originals if repository.get("archived")),
        "stars": sum(repository["stargazers_count"] for repository in originals),
        "forks_received": sum(repository["forks_count"] for repository in originals),
        "top_repos": [
            {"name": repository["name"], "url": repository["html_url"], "stars": repository["stargazers_count"]}
            for repository in popular[:3]
        ],
        "languages": languages(originals),
        "followers": profile["followers"],
        "contributions": sum(year["contributions"] for year in years),
        "commits": sum(year["commits"] for year in years),
        "years": years,
        **activity(days, now.date()),
        "collaboration": fetch_collaboration(now),
        "additions": lines["additions"],
        "deletions": lines["deletions"],
        "line_cache": {"oldest_calculation": lines["oldest_calculation"], "max_age_days": LINE_CACHE_MAX_AGE.days},
    }


def read_history(path):
    """Return a history snapshot, rejecting files from another account or schema."""
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        valid = (
            snapshot["schema_version"] == SCHEMA_VERSION and snapshot["login"] == USER
            and snapshot["date"] == path.stem and all(isinstance(snapshot[key], int) for key in TREND_FIELDS)
        )
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError(f"Incompatible history snapshot {path.name}; fix or remove it before generating.")
    return snapshot


def update_history(stats, now):
    """Return today's history file changes, including purges, and the 30-day trend."""
    folder = ROOT / "data" / "history"
    today = now.date()
    snapshot = {
        "schema_version": SCHEMA_VERSION, "login": USER, "generated_at": stats["generated_at"],
        "date": today.isoformat(), **{key: stats[key] for key in TREND_FIELDS},
    }
    changes = {folder / f"{today}.json": json.dumps(snapshot, indent=2) + "\n"}
    oldest = today - timedelta(days=HISTORY_DAYS - 1)
    snapshots = {}
    for path in sorted(folder.glob("*.json")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", path.name):
            raise ValueError(f"Unexpected history file {path.name}; fix or remove it before generating.")
        if date.fromisoformat(path.stem) < oldest:
            changes[path] = None
        elif path.stem != today.isoformat():
            snapshots[path.stem] = read_history(path)
    reference_day = (today - timedelta(days=TREND_DAYS)).isoformat()
    trend = {"reference_date": reference_day, "current_date": today.isoformat(), "available": reference_day in snapshots}
    if trend["available"]:
        trend["changes"] = {key: snapshot[key] - snapshots[reference_day][key] for key in TREND_FIELDS}
    return changes, trend


def uptime(today=None):
    today = today or date.today()
    years = today.year - BIRTHDAY.year
    months = today.month - BIRTHDAY.month
    days = today.day - BIRTHDAY.day
    if days < 0:
        months -= 1
        days += (today.replace(day=1) - timedelta(days=1)).day
    if months < 0:
        years -= 1
        months += 12
    return ", ".join(
        f"{value} {unit}{'s' if value != 1 else ''}"
        for value, unit in ((years, "year"), (months, "month"), (days, "day"))
    )


def readable(value):
    """Format `value` with separators, abbreviating millions so it fits the card."""
    return f"{value:,}" if abs(value) < 1_000_000 else f"{value / 1_000_000:,.2f}M"


def share(part, whole):
    return f"{round(part * 100 / whole)}%" if whole else "n/a"


def render_card(stats, portrait, theme):
    palette = THEMES[theme]
    age = uptime(date.fromisoformat(stats["generated_at"][:10]))
    net = stats["additions"] - stats["deletions"]
    recent, active = stats["recent"], stats["active_days"]

    def span(value, color):
        return f'<tspan fill="{palette[color]}">{escape(str(value))}</tspan>'

    def item(label, value, width=56):
        value = str(value)
        dots = "." * max(1, width - len(label) - len(value) - 3)
        return span(f"{label}: ", "key") + span(f"{dots} ", "dots") + span(value, "value")

    def line(y, content):
        return f'<text x="390" y="{y}" xml:space="preserve">{content}</text>'

    def heading(y, label):
        return line(y, span(label, "heading") + span("─" * (56 - len(label)), "dots"))

    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="840" height="500" viewBox="0 0 840 500" '
        'font-family="Consolas, Menlo, monospace" font-size="13px" '
        'role="img" aria-labelledby="title description">',
        f'<title id="title">{escape(stats["name"])} GitHub profile</title>',
        f'<desc id="description">Monochrome ASCII portrait. {escape(JOB_TITLE)}. '
        f'Uptime: {age}. IDE: {escape(IDE)}. Email: {escape(EMAIL)}. '
        f'LinkedIn: {escape(LINKEDIN)}. GitHub statistics on {stats["generated_at"][:10]}: '
        f'{stats["repos"]} public repositories ({stats["original_repos"]} original, {stats["fork_repos"]} forks), '
        f'{stats["stars"]} stars, {stats["followers"]} followers, '
        f'{stats["contributions"]} contributions and {stats["commits"]} commits since {stats["statistics_since"][:4]}, '
        f'{recent["contributions"]} contributions in the 30 days to {recent["end"]}, '
        f'{active["days"]} active days out of {active["window_days"]}, '
        f'net line changes {net} ({stats["additions"]} added, {stats["deletions"]} deleted).</desc>',
        '<rect x="0.5" y="0.5" width="839" height="499" rx="10" '
        f'fill="{palette["background"]}" stroke="{palette["border"]}"/>',
    ]
    for index, row in enumerate(portrait):
        svg.append(
            f'<text x="25" y="{40 + index * 15}" fill="{palette["portrait"]}" '
            f'xml:space="preserve">{escape(row)}</text>'
        )
    svg.append(heading(45, f"{USER}@github "))
    for y, label, value in [
        (87, "OS", "Windows, Linux"),
        (108, "Uptime", age),
        (129, "GitHub", f"Since {stats['since']}"),
        (150, "Kernel", JOB_TITLE),
        (171, "IDE", IDE),
        (213, "Languages", "C#, TypeScript, Python, PowerShell, Shell"),
        (234, "Frameworks", "WinUI 3, React, Next.js"),
    ]:
        svg.append(line(y, item(label, value)))
    svg.extend([
        heading(276, "─ Contact "),
        line(297, item("Email", EMAIL)),
        line(318, item("GitHub", f"github.com/{USER}")),
        f'<a href="{escape(LINKEDIN)}">'
        + line(339, item("LinkedIn", LINKEDIN.removeprefix("https://www."))) + '</a>',
        heading(360, "─ GitHub Stats "),
        line(381, item("Repos", readable(stats["repos"]), 30) + span(" | ", "dots") + item("Stars", readable(stats["stars"]), 23)),
        line(402, item("Commits", readable(stats["commits"]), 30) + span(" | ", "dots") + item("Followers", readable(stats["followers"]), 23)),
        line(423, item("Contributions", readable(stats["contributions"]), 30) + span(" | ", "dots")
             + item("Last 30 days", readable(recent["contributions"]), 23)),
        line(444, item(f"Active days ({active['window_days']}d)",
                       f"{active['days']} ({share(active['days'], active['window_days'])})")),
        line(465, span("Net line changes: ", "key") + span(readable(net), "value")
             + span(" ( ", "dots") + span(f"{readable(stats['additions'])}++", "added")
             + span(", ", "dots") + span(f"{readable(stats['deletions'])}--", "deleted") + span(" )", "dots")),
        '</svg>',
    ])
    return "\n".join(svg) + "\n"


def markdown(value):
    return re.sub(r"([\\`*_{}\[\]<>()#|~!&])", r"\\\1", str(value))


def render_report(stats):
    """Render STATS.md with every metric, its period, source and limits."""
    year = stats["collaboration"]["start"][:4]
    recent, active, streaks = stats["recent"], stats["active_days"], stats["streaks"]
    trend, lines = stats["trend"], stats["line_cache"]
    net = stats["additions"] - stats["deletions"]
    report = [
        f"# GitHub statistics — {markdown(stats['name'])}",
        "",
        f"Generated on {stats['generated_at']} (UTC) from public data of "
        f"[github.com/{USER}](https://github.com/{USER}). Daily windows end on {recent['end']}, the day "
        "before collection; the collection day is never counted. These figures describe activity, not "
        "quality, productivity or expertise. Raw values: [data/stats.json](data/stats.json).",
        "",
        "## Overview",
        "",
        "| Statistic | Value | Period | Source |",
        "| --- | ---: | --- | --- |",
        f"| Public repositories | {stats['repos']:,} | {stats['generated_at'][:10]} | REST, owned public repositories |",
        f"| Original repositories (not forks) | {stats['original_repos']:,} | {stats['generated_at'][:10]} | REST |",
        f"| Forks owned | {stats['fork_repos']:,} | {stats['generated_at'][:10]} | REST |",
        f"| Archived original repositories | {stats['archived_repos']:,} | {stats['generated_at'][:10]} | REST, included in originals |",
        f"| Stars on original repositories | {stats['stars']:,} | {stats['generated_at'][:10]} | REST |",
        f"| Forks received by original repositories | {stats['forks_received']:,} | {stats['generated_at'][:10]} | REST |",
        f"| Followers | {stats['followers']:,} | {stats['generated_at'][:10]} | REST |",
        f"| Contributions | {stats['contributions']:,} | {stats['statistics_since']} to {stats['generated_at'][:10]} | Public contribution calendar |",
        f"| Commits | {stats['commits']:,} | {stats['statistics_since']} to {stats['generated_at'][:10]} | GraphQL, public repositories |",
        f"| Net line changes | {net:,} | Full history | GraphQL, default branches of original repositories |",
        "",
        "## Popular projects",
        "",
    ]
    report += [
        f"{rank}. [{markdown(repository['name'])}]({repository['url']}) — {repository['stars']:,} "
        f"star{'s' if repository['stars'] != 1 else ''}"
        for rank, repository in enumerate(stats["top_repos"], 1)
    ] or ["No original public repository yet."]
    portfolio = stats["languages"]
    report += [
        "",
        "Original public repositories ranked by stars, then name.",
        "",
        "## Primary languages of original repositories",
        "",
        "| Language | Repositories | Share |",
        "| --- | ---: | ---: |",
    ]
    rows = [(entry["name"], entry["repos"]) for entry in portfolio["top"]]
    rows += [("Other", portfolio["other"])] if portfolio["other"] else []
    rows += [("Not specified", portfolio["unspecified"])] if portfolio["unspecified"] else []
    report += [f"| {markdown(name)} | {count:,} | {share(count, portfolio['repositories'])} |" for name, count in rows]
    report += [
        "",
        "Share of original public repositories, archives included, by the primary language GitHub detects. "
        "It counts repositories, not lines, time spent or expertise.",
        "",
        "## Yearly activity",
        "",
        "| Year | Contributions | Commits | Status |",
        "| --- | ---: | ---: | --- |",
    ]
    report += [
        f"| {row['year']} | {row['contributions']:,} | {row['commits']:,} | {'complete' if row['complete'] else 'in progress'} |"
        for row in stats["years"]
    ]
    report += [
        f"| **Total** | **{stats['contributions']:,}** | **{stats['commits']:,}** | |",
        "",
        "Contributions come from the public calendar, which can include anonymous private counts the user "
        "chose to share. Commits only count public repositories. The two columns have different scopes; "
        "a year in progress is not comparable with a complete year.",
        "",
        "## Recent activity",
        "",
        f"- **Contributions, last {RECENT_DAYS} days** ({recent['start']} to {recent['end']}): {recent['contributions']:,}",
        f"- **Active days, last {active['window_days']} days** ({active['start']} to {active['end']}): "
        f"{active['days']:,} of {active['window_days']} ({share(active['days'], active['window_days'])})",
        f"- **Current streak** (ending {streaks['end']}): {streaks['current']:,} day{'s' if streaks['current'] != 1 else ''}",
        f"- **Longest streak within {streaks['start']} to {streaks['end']}**: {streaks['longest']:,} "
        f"day{'s' if streaks['longest'] != 1 else ''} (this window only, not an all-time record)",
        "",
        "An active day has at least one contribution in the public calendar.",
        "",
        f"## Collaboration in {year}",
        "",
        f"From {stats['collaboration']['start']} to {stats['collaboration']['end']}, on public repositories, "
        "including those of other owners:",
        "",
        f"- **Pull requests opened:** {stats['collaboration']['pull_requests']:,} (whatever their current state)",
        f"- **Issues opened:** {stats['collaboration']['issues']:,} (whatever their current state)",
        f"- **Pull request review contributions:** {stats['collaboration']['reviews']:,} "
        "(as counted by GitHub; not a number of distinct or merged pull requests)",
        "",
        f"## Changes over {TREND_DAYS} days",
        "",
    ]
    if trend["available"]:
        report += [
            f"Change in each total between {trend['reference_date']} and {trend['current_date']}. These are "
            "changes in stock, not counts of new stars, followers or repositories.",
            "",
            "| Total | Change |",
            "| --- | ---: |",
        ]
        report += [f"| {label} | {trend['changes'][key]:+,} |" for key, label in (
            ("stars", "Stars"), ("followers", "Followers"), ("repos", "Public repositories"))]
    else:
        report.append(
            f"Insufficient history: a snapshot from {trend['reference_date']} is needed to compare with "
            f"{trend['current_date']}. Nothing is interpolated."
        )
    report += [
        "",
        "## Line changes",
        "",
        f"- **Added:** {stats['additions']:,}",
        f"- **Deleted:** {stats['deletions']:,}",
        f"- **Net:** {net:,}",
        "",
        "Additions and deletions of the account's authored commits on the default branches of original public "
        "repositories, over their whole history. They include text and documentation and do not measure the "
        "code currently present.",
    ]
    if lines["oldest_calculation"]:
        report += [
            "",
            f"Per-repository totals are cached while the branch head is unchanged. The oldest calculation used "
            f"dates from {lines['oldest_calculation']}; a reattribution of old commits can take up to "
            f"{lines['max_age_days']} days to appear.",
        ]
    report += [
        "",
        "## Definitions and limits",
        "",
        f"- The card's **GitHub: Since {stats['since']}** is a custom profile year. The account was created on "
        f"{stats['account_created']}; yearly statistics start on {stats['statistics_since']}.",
        "- Only public repositories are counted; forks are excluded from stars, forks received, languages, "
        "popular projects and line changes.",
        "- Commits per year are read from at most 100 repositories; generation stops rather than publish a "
        "possibly truncated total.",
        "- Any missing, truncated or inconsistent source stops generation and keeps the previous outputs.",
        "",
    ]
    return "\n".join(report)


def validate_output(path, content):
    if path.suffix == ".svg":
        ElementTree.fromstring(content)
    elif path.suffix == ".json":
        json.loads(content)


def publish(changes):
    """Write every output (None deletes it) together, restoring previous files if a replacement fails."""
    staged = {}
    try:
        for destination, content in changes.items():
            if content is None:
                continue
            validate_output(destination, content)
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged[destination] = destination.with_name(destination.name + ".tmp")
            staged[destination].write_text(content, encoding="utf-8", newline="\n")
        backups, replaced = {}, []
        try:
            for destination, content in changes.items():
                backups[destination] = destination.read_bytes() if destination.exists() else None
                if content is None:
                    destination.unlink(missing_ok=True)
                else:
                    staged[destination].replace(destination)
                replaced.append(destination)
        except BaseException:
            for destination in reversed(replaced):
                if backups[destination] is None:
                    destination.unlink(missing_ok=True)
                else:
                    destination.write_bytes(backups[destination])
            raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main(now=None):
    started = time.monotonic()
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    line_cache = load_line_cache()
    stats = fetch_stats(now, line_cache)
    save_line_cache(line_cache)
    history, stats["trend"] = update_history(stats, now)
    portrait = (ROOT / "assets" / "portrait-original.txt").read_text(encoding="ascii").splitlines()
    outputs = {ROOT / f"{theme}_mode.svg": render_card(stats, portrait, theme) for theme in THEMES}
    outputs[ROOT / "STATS.md"] = render_report(stats)
    outputs[ROOT / "data" / "stats.json"] = json.dumps(stats, indent=2, ensure_ascii=False) + "\n"
    outputs.update(history)
    publish(outputs)
    print(
        f"Profile generated: {stats['repos']} public repos, {stats['stars']} stars, {stats['followers']} followers, "
        f"{stats['contributions']} contributions, {stats['commits']} commits, "
        f"{stats['additions'] - stats['deletions']} net lines (+{stats['additions']} / -{stats['deletions']}); "
        f"{API['calls']} GitHub requests in {time.monotonic() - started:.1f}s."
    )


if __name__ == "__main__":
    main()
