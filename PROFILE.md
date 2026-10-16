# Customize the profile

The README displays an SVG card that matches GitHub's light or dark theme.
The portrait uses real monochrome ASCII characters converted from the image
provided by OhMyCode. The original image is not needed for updates.

## Card style

The generator creates `dark_mode.svg` and `light_mode.svg` from the same data.
Both use the reference's compact 840 × 500 layout, 13 px Consolas/Menlo text,
monochrome ASCII portrait, original theme colors, dot leaders and inline
statistics. The README displays this pair at its native width of 840 pixels.

## Information and statistics

Edit the profile details in `update_profile.py`, then run:

```sh
python -m unittest -v
python update_profile.py
```

Both themes share the same personal fields:

- **Uptime:** age in years, months and days, calculated by the shared age helper from
  `BIRTHDAY` (21 September 1985) whenever the cards are generated. The daily
  workflow keeps it current and advances it on the birthday.
- **Kernel:** Systems and Network Administrator.
- **IDE:** Claude-Code, Cursor, VS Code.
- **Email:** ohmycodedev@gmail.com.

Python 3.12 or later is sufficient, with no external Python dependencies. The
GitHub API provides the public repository count (including forks), followers,
and total stars on public repositories that are not forks. The name and join
year come from the profile.

- **Contributions:** yearly totals from GitHub's anonymous public contribution
  calendar, summed from the join year onward. These can include anonymous private
  contribution counts if the user has chosen to share them publicly.
- **Commits:** yearly totals from GraphQL
  `commitContributionsByRepository.contributions.totalCount`, with private and
  internal repositories excluded. If a year's repository group list reaches
  100, generation stops rather than publishing a potentially truncated total.
- **Lines of code:** additions minus deletions in the user's authored commit
  history on the default branches of all public, owned repositories that are
  not forks. The card also shows `+added / -removed`. History is fully paginated.
  These are historical line changes, including text and documentation, rather
  than an inventory of the code currently present.

GraphQL uses `GITHUB_TOKEN` or `GH_TOKEN` when available. Locally, the script can
instead call an already authenticated `gh` CLI through a subprocess. It does
not write credentials to files or export secrets.

The public calendar total depends on GitHub's HTML heading selector. If that
markup changes or an API request fails, generation stops before writing cards
and preserves the previous versions.

## Portrait

The cards read the 43 × 28 ASCII portrait in `assets/portrait-original.txt`.
Edit that text file to replace the portrait. The colored assets
`assets/portrait.txt` and `assets/portrait.json` are retained as archives and
are not used to generate the cards. Daily updates require no image library.

## GitHub Actions

After the files are published on `main`, the **Update profile** workflow
generates the cards daily at 04:17 UTC. You can also run it from
**Actions → Update profile → Run workflow**. The `GITHUB_TOKEN` provided by
GitHub is sufficient; no personal secret is needed.

The workflow commits only the two SVGs; the `contents: write` permission
allows this commit.
If your repository protects `main` against workflow commits, adjust its
protection rule to allow this update.

The layout is inspired by the profile of
[DietrichGebert](https://github.com/DietrichGebert/DietrichGebert).
The generator and cards were written for OhMyCode.
