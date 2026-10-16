"""Generate the GitHub profile cards using public GitHub data and ASCII text."""

import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
USER = "devohmycode"
GITHUB_SINCE = "2022"
BIRTHDAY = date(1985, 9, 21)
JOB_TITLE = "Systems and Network Administrator"
IDE = "Claude-Code, Cursor, VS Code"
EMAIL = "ohmycodedev@gmail.com"
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


def request_json(path, payload=None):
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


def request_graphql(query, variables):
    payload = {"query": query, "variables": variables}
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        response = request_json("/graphql", payload)
    else:
        try:
            result = subprocess.run(
                ["gh", "api", "graphql", "--input", "-"], input=json.dumps(payload),
                capture_output=True, text=True, timeout=30, check=True,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as error:
            raise RuntimeError("GitHub GraphQL requires GITHUB_TOKEN, GH_TOKEN or an authenticated gh CLI.") from error
        response = json.loads(result.stdout)
    if response.get("errors"):
        raise RuntimeError("GitHub GraphQL: " + "; ".join(error["message"] for error in response["errors"]))
    return response["data"]


def fetch_calendar_total(year):
    url = f"https://github.com/users/{USER}/contributions?from={year}-01-01&to={year}-12-31"
    # shortcut: public totals use GitHub's calendar HTML; update the selector if its markup changes.
    request = Request(url, headers={"User-Agent": f"{USER}-profile", "Accept-Language": "en-US"})
    with urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8")
    heading = re.search(r'<h2\b[^>]*\bid="js-contribution-activity-description"[^>]*>(.*?)</h2>', html, re.S)
    count = re.search(rf'([\d,]+)\s+contributions?\s+in\s+{year}\b', heading[1]) if heading else None
    if count is None:
        raise ValueError(f"Cannot read the public contribution total for {year}; existing cards were kept.")
    return int(count[1].replace(",", ""))


def fetch_contributions(since):
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
    contributions = commits = 0
    for year in range(since, datetime.now(timezone.utc).year + 1):
        data = request_graphql(query, {
            "login": USER, "from": f"{year}-01-01T00:00:00Z", "to": f"{year}-12-31T23:59:59Z",
        })
        groups = data["user"]["contributionsCollection"]["commitContributionsByRepository"]
        # shortcut: GitHub groups up to 100 repositories/year; require a complete source before exceeding this.
        if len(groups) >= 100:
            raise RuntimeError(f"The {year} commit collection may be truncated; existing cards were kept.")
        commits += sum(group["contributions"]["totalCount"] for group in groups if not group["repository"]["isPrivate"])
        contributions += fetch_calendar_total(year)
    return contributions, commits


def fetch_line_changes(repositories, author_id):
    query = """query($owner: String!, $name: String!, $author: ID!, $after: String) {
      repository(owner: $owner, name: $name) {
        isPrivate isFork
        defaultBranchRef { target { ... on Commit {
          history(first: 100, after: $after, author: {id: $author}) {
            nodes { additions deletions }
            pageInfo { hasNextPage endCursor }
          }
        } } }
      }
    }"""
    additions = deletions = 0
    for repository in repositories:
        if repository["fork"] or repository["private"]:
            continue
        cursor = None
        while True:
            data = request_graphql(query, {
                "owner": USER, "name": repository["name"], "author": author_id, "after": cursor,
            })["repository"]
            if data["isPrivate"] or data["isFork"] or data["defaultBranchRef"] is None:
                break
            history = data["defaultBranchRef"]["target"]["history"]
            additions += sum(commit["additions"] for commit in history["nodes"])
            deletions += sum(commit["deletions"] for commit in history["nodes"])
            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]
    return additions, deletions


def fetch_stats():
    profile = request_json(f"/users/{USER}")
    repositories = []
    page = 1
    while True:
        batch = request_json(f"/users/{USER}/repos?type=owner&per_page=100&page={page}")
        repositories.extend(repo for repo in batch if not repo["private"])
        if len(batch) < 100:
            break
        page += 1
    contributions, commits = fetch_contributions(int(profile["created_at"][:4]))
    additions, deletions = fetch_line_changes(repositories, profile["node_id"])
    return {
        "name": profile["name"] or USER,
        "since": GITHUB_SINCE,
        "repos": len(repositories),
        "stars": sum(repo["stargazers_count"] for repo in repositories if not repo["fork"]),
        "followers": profile["followers"],
        "contributions": contributions,
        "commits": commits,
        "additions": additions,
        "deletions": deletions,
    }


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


def render_card(stats, portrait, theme):
    palette = THEMES[theme]
    age = uptime()

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
        f'Uptime: {age}. IDE: {escape(IDE)}. Email: {escape(EMAIL)}. GitHub statistics: '
        f'{stats["repos"]} repositories, {stats["stars"]} stars, {stats["followers"]} followers, '
        f'{stats["contributions"]} contributions, {stats["commits"]} commits, '
        f'{stats["additions"] - stats["deletions"]} net lines added.</desc>',
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
        heading(297, "─ Contact "),
        line(318, item("Email", EMAIL)),
        line(339, item("GitHub", f"github.com/{USER}")),
        heading(381, "─ GitHub Stats "),
        line(402, item("Repos", f"{stats['repos']:,}", 30) + span(" | ", "dots") + item("Stars", f"{stats['stars']:,}", 23)),
        line(423, item("Commits", f"{stats['commits']:,}", 30) + span(" | ", "dots") + item("Followers", f"{stats['followers']:,}", 23)),
        line(444, item("Contributions", f"{stats['contributions']:,}")),
        line(465, span("Lines of Code: ", "key") + span(f"{stats['additions'] - stats['deletions']:,}", "value")
             + span(" ( ", "dots") + span(f"{stats['additions']:,}++", "added")
             + span(", ", "dots") + span(f"{stats['deletions']:,}--", "deleted") + span(" )", "dots")),
        '</svg>',
    ])
    return "\n".join(svg) + "\n"


def main():
    stats = fetch_stats()
    portrait = (ROOT / "assets" / "portrait-original.txt").read_text(encoding="ascii").splitlines()
    cards = {f"{theme}_mode.svg": render_card(stats, portrait, theme) for theme in THEMES}
    for filename, svg in cards.items():
        destination = ROOT / filename
        temporary = destination.with_suffix(".svg.tmp")
        temporary.write_text(svg, encoding="utf-8")
        temporary.replace(destination)
    print(
        f"Cards generated: {stats['repos']} public repos, {stats['stars']} stars, {stats['followers']} followers, "
        f"{stats['contributions']} contributions, {stats['commits']} commits, "
        f"{stats['additions'] - stats['deletions']} net lines (+{stats['additions']} / -{stats['deletions']})."
    )


if __name__ == "__main__":
    main()
