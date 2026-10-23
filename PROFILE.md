# Customize the profile

The README displays an SVG card that matches GitHub's light or dark theme,
followed by links to the detailed statistics, email and LinkedIn. The portrait
uses real monochrome ASCII characters converted from the image provided by
OhMyCode. The original image is not needed for updates.

## Outputs

One run of `update_profile.py` produces, from the same snapshot:

| File | Content |
| --- | --- |
| `dark_mode.svg`, `light_mode.svg` | Compact 840 × 500 cards, same values in both themes |
| `STATS.md` | Detailed report: every metric with its period, source, limits and links |
| `data/stats.json` | Raw public values of the last successful run (`schema_version`, `generated_at`) |
| `data/history/YYYY-MM-DD.json` | One daily snapshot of stars, followers and public repositories |

All outputs are rendered and validated (XML, JSON) before any file is replaced.
If a replacement fails, the files already replaced are restored, so a handled
error never leaves a mix of old and new outputs. An abrupt stop of the machine
is not covered; GitHub then receives the whole set in a single commit.

## Card style

Both cards use the reference's compact 840 × 500 layout, 13 px Consolas/Menlo
text, monochrome ASCII portrait, original theme colors, dot leaders and inline
statistics. The README displays this pair at its native width of 840 pixels.
Numbers of a million or more are abbreviated on the card (`1.23M`); the exact
values stay in the SVG description, `STATS.md` and `data/stats.json`.

## Information and statistics

Edit the profile details in `update_profile.py`, then run:

```sh
python -m unittest -v
python update_profile.py
```

Both themes share the same personal fields:

- **Uptime:** age in years, months and days, calculated by the shared age helper from
  `BIRTHDAY` (21 September 1985) for the collection date. The daily
  workflow keeps it current and advances it on the birthday.
- **Kernel:** Systems and Network Administrator.
- **IDE:** Claude-Code, Cursor, VS Code.
- **Email:** ohmycodedev@gmail.com.
- **LinkedIn:** [linkedin.com/in/g-decker](https://www.linkedin.com/in/g-decker).

The LinkedIn link works when the SVG is opened directly. When GitHub embeds
the SVG as an image in the README, links inside the image are not interactive;
the README therefore repeats them as text links.

**GitHub: Since 2022** is a custom profile year set with `GITHUB_SINCE`. It is
not the account creation date (shown in `STATS.md`), and the yearly statistics
start on 1 January of the account creation year.

Python 3.12 or later is sufficient, with no external Python dependencies.

### Collection date

Each run fixes one UTC instant and passes it to every collector, calculation and
renderer, including the uptime. The 30- and 365-day windows cover complete
calendar days up to the day before collection; the collection day is never
counted, even if it already has contributions. Dates come from GitHub's daily
calendar and are not rebuilt from commit times.

### Definitions

| Statistic | Source | Period | Filters |
| --- | --- | --- | --- |
| Public repositories | REST `/users/{user}/repos?type=owner` | Collection date | Public, owned; checked against the profile's `public_repos` |
| Original repositories / forks | Same list | Collection date | Original = owned and not a fork, which does not mean all its code was written by the account |
| Archived original repositories | Same list | Collection date | Subset of the originals, still counted in every total |
| Stars, forks received | Same list | Collection date | Original public repositories |
| Popular projects | Same list | Collection date | Three originals with most stars, then by name |
| Primary languages | Same list, `language` | Collection date | Originals, archives included; three first, other, not specified. A share of repositories, not of lines, time or expertise |
| Followers | REST `/users/{user}` | Collection date | — |
| Contributions | Anonymous public contribution calendar | Each year since account creation | Can include anonymous private counts the user chose to share |
| Commits | GraphQL `commitContributionsByRepository` | Each year since account creation | Public repositories only |
| Contributions, last 30 days | Public calendar, daily counts | 30 complete days | Same as contributions |
| Active days, streaks | Public calendar, daily counts | 365 complete days | A day with at least one contribution; the longest streak is only for this window |
| Pull requests, issues opened | GraphQL `contributionsCollection` | 1 January to collection | Public repositories of any owner, whatever their current state |
| Pull request review contributions | GraphQL `pullRequestReviewContributions` | 1 January to collection | As counted by GitHub; not distinct or merged pull requests |
| Net line changes | GraphQL commit history | Whole history | Additions minus deletions of the account's commits on default branches of original public repositories |
| 30-day changes | `data/history` | Same date 30 days earlier | Change of each total (stock), not a count of new events |

Contributions and commits have different scopes: the calendar can count
anonymous private contributions, while commits only count public repositories.
Net line changes include text and documentation; they do not measure the code
currently present. An empty public repository adds zero.

### Limits and safeguards

- **Commits:** GitHub groups at most 100 repositories per year. If a year's
  list reaches 100, generation stops rather than publish a possibly truncated
  total.
- **Public calendar:** daily counts are read from GitHub's calendar HTML, the
  same page as the yearly totals, because GraphQL with a privileged token would
  also count private contributions that the public profile hides. Each year must
  have one value per day and a sum equal to its heading, otherwise generation
  stops. Update the selectors in `parse_calendar` if GitHub changes its markup.
- **Public data only:** private and internal repositories are excluded from
  every collection, even with a local token that can read them.
- **Completeness:** a missing, null, truncated or inconsistent response, a
  repeated page cursor, or a repository that disappears or becomes private during
  collection stops generation and keeps the previous outputs. Nothing is
  silently replaced by zero.
- **Retries:** timeouts, 5xx responses and rate limits are retried up to four
  attempts, following `Retry-After` and the rate-limit reset. Authentication,
  validation and GraphQL errors are not retried. A wait over 60 seconds, or
  more than 120 seconds of waiting in one run, stops generation.

GraphQL uses `GITHUB_TOKEN` or `GH_TOKEN` when available. Locally, the script can
instead call an already authenticated `gh` CLI through a subprocess. It does
not write credentials to files or export secrets.

### History and 30-day changes

Each successful run writes `data/history/YYYY-MM-DD.json` for its UTC date,
replacing an earlier run of the same day. Snapshots only hold the schema
version, login, time, stars, followers and public repositories. Files older than
400 days are deleted with the same publication. The report compares today's
snapshot with the one exactly 30 days earlier; when it does not exist, it says
"Insufficient history" and nothing is interpolated. A snapshot of another
account or schema stops generation with an explicit error.

### Line-change cache

Line totals are cached per repository in `.cache/profile/line-changes.json`,
which is never committed. An entry is reused only for the same calculation
version, repository, author, default branch and head commit, for up to seven
days; the visibility of each repository is checked on every run. Because
GitHub can reattribute old commits without changing the head, such a change can
take up to seven days to appear; `STATS.md` gives the oldest calculation used. An
absent or corrupt cache triggers a full collection. Each run prints its number
of GitHub requests and duration.

## Portrait

The cards read the 43 × 28 ASCII portrait in `assets/portrait-original.txt`.
Edit that text file to replace the portrait. The colored assets
`assets/portrait.txt` and `assets/portrait.json` are retained as archives and
are not used to generate the cards. Daily updates require no image library.

## GitHub Actions

After the files are published on `main`, the **Update profile** workflow
generates the outputs daily at 04:17 UTC, and when the generator, its tests, the
portrait or the workflow change on `main`. You can also run it from
**Actions → Update profile → Run workflow**. The `GITHUB_TOKEN` provided by
GitHub is sufficient; no personal secret is needed.

- **Pull requests**, forks included, only run the offline tests with read
  permission; no secret is used and nothing is written.
- **Updates** test, restore the line-change cache, generate, save the cache,
  then commit only `dark_mode.svg`, `light_mode.svg`, `STATS.md` and `data/`.
  Nothing is committed when the outputs did not change. The cache only speeds
  up collection; it is never published and caches from pull requests are not used.

Generated files are not in the workflow's path filters, and commits pushed with
`GITHUB_TOKEN` do not start new runs, so publication cannot loop. If your
repository protects `main` against workflow commits, adjust its protection rule
to allow this update.

### Recovery

If a run fails, the previous outputs stay published; read the job log for the
failing source. Temporary GitHub problems resolve on the next run, or rerun the
job manually. If GitHub changes its calendar markup, update `parse_calendar`. If
a history snapshot is rejected, fix or delete that file in `data/history`. A
corrupt cache needs no action: it is ignored and rebuilt.

The layout is inspired by the profile of
[DietrichGebert](https://github.com/DietrichGebert/DietrichGebert).
The generator and cards were written for OhMyCode.
