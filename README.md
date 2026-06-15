# Hardcover Sync

Calibre plugin for [Hardcover.app](https://hardcover.app/). It adds a single
**Hardcover Sync** entry to the book context menu (right-click a book) that
two-way syncs your library with Hardcover: ratings, reviews, reading status,
notes, quotes, tags, and list membership — plus an ISBN-culling helper.

Most actions work on a multi-selection, so you can push or pull for one book or
your whole library at once.

## Requirements (read this first)

> **The official Hardcover metadata plugin is REQUIRED, not optional.**

This plugin syncs by matching each Calibre book to a Hardcover edition. That
match comes from the Hardcover identifiers stored on the book (the
`hardcover` / `hardcover-edition` identifiers), which are written by the
official **Hardcover** metadata plugin when you download metadata. Without
those identifiers a book cannot be matched, and every push/pull will skip it as
"without a Hardcover match".

So you need **both** plugins:

1. The official [**Hardcover** metadata
   plugin](https://www.mobileread.com/forums/showthread.php?t=364041) —
   installed from Calibre's plugin panel. Use it to download metadata for your
   books so they carry Hardcover identifiers.
2. **Hardcover Sync** (this plugin) — does the actual syncing.

You only need to enter your Hardcover API key once: if you leave the key blank
in **Hardcover Sync**, it reuses the key from the metadata plugin. API rate
limits are shared between the two plugins via a lock file.

## Installation & first-time setup

1. **Install the plugin.** In Calibre, go to **Preferences → Plugins → Load
   plugin from file** and select `hardcover-list-<version>.zip` (from the
   [Releases](../../releases) page, or build it yourself — see Development).
2. **Restart Calibre — first time (for the plugin).** This loads Hardcover Sync
   and registers it in the context menu.
3. **Restart Calibre — second time (for the custom columns).** On first run the
   plugin creates several custom columns (see
   [Custom columns](#custom-columns)). Calibre only shows newly created columns
   after a restart, so a **second restart is required** before the synced
   columns appear in your library.
4. **Configure your API key.** Open **Preferences → Plugins → Hardcover Sync →
   Customize plugin** and paste your Hardcover API key (skip this if the
   metadata plugin already has one).
5. **Choose your auto-push preference.** Decide whether edits to the synced
   columns should push to Hardcover automatically — see
   [Auto-push](#auto-push). The first time you edit a synced column the plugin
   asks once; you can change it later in the same preferences screen.

> **Tip:** download metadata with the Hardcover metadata plugin *before*
> syncing, so your books carry Hardcover identifiers.

## Menu options

Right-click one or more books and open **Hardcover Sync**. In every submenu,
**Push** sends Calibre → Hardcover and **Pull** brings Hardcover → Calibre.
Books that fail an operation are flagged with a red pin in the library view.

### Cull ISBN

Scans the selected book's files for ISBN-10 / ISBN-13 numbers and opens a table
of candidate editions — **cover, title, author, publisher, and format** (format
is simplified to *Book*, *Audio Book*, or *E-Book*). Pick the row that matches
your copy and the plugin saves that ISBN to the book's identifiers. Works on a
single selected book.

### Lists

- **Hardcover Lists column** — a custom column that shows which of your
  Hardcover lists each book belongs to.
- **Manage Lists** — one entry per list, each with **Add to List** /
  **Remove from List** for the current selection.
- **Create New** — create a new Hardcover list.
- **Refresh** — re-fetch list membership for the selected books.

### Ratings

Syncs the **Rating** (`#hc_rating`) column with your Hardcover rating.

- **Push Rating** — writes Calibre's rating to Hardcover. Blank ratings are
  skipped. A brand-new entry also marks the book **Read** (see
  [Read dates](#read-dates)).
- **Pull Rating** — copies your Hardcover rating into the column; books you
  haven't rated are left blank.

### Reviews

Syncs the **Review** (`#review`) column with your Hardcover review.

- **Push Review** — converts the column's HTML into Hardcover's review format.
  A new entry marks the book **Read** (see [Read dates](#read-dates)).
- **Pull Review** — brings your Hardcover review text into the column.

If you already have a custom column named `#review` it is used as-is; otherwise
it is created for you.

### Status

Syncs the **Status** (`#hc_status`) column with your Hardcover shelf. Values:
*Want to Read, Currently Reading, Read, Paused, Did Not Finish, Ignored*.

- **Push Status** — sets your Hardcover shelf from the column. Setting a book to
  **Read** for the first time prompts for a read date.
- **Pull Status** — brings your current Hardcover shelf into the column.

### Notes

Syncs the **Notes** (`#hc_notes`) column with your Hardcover journal **note**
entries. Multiple notes live in one column, separated by a line of `---`.

- **Push Notes** — full sync: notes new to Hardcover are added, notes removed
  from the column are deleted on Hardcover.
- **Pull Notes** — brings all your note entries into the column.

### Quotes

Syncs the **Quotes** (`#hc_quotes`) column with your Hardcover journal **quote**
entries, separated by `---` like notes. A quote may start with a page prefix
such as `p123:` to record the page position (e.g. `p123: a memorable line`).

- **Push Quotes** — full sync of quotes (and their page numbers).
- **Pull Quotes** — brings your quotes (with page prefixes) into the column.

### Tags

Syncs Calibre's **native Tags** field with Hardcover's free-form **"Tag"**
category. (No extra column is created — your existing tags are used.)

- **Push Tags** — writes your Calibre tags to Hardcover. Your structured
  Hardcover tag categories (Genre, Mood, Pace, Content Warning, …) are
  **preserved**; only the free-form "Tag" category is updated.
- **Pull Tags** — replaces the book's Calibre tags with your free-form
  Hardcover tags (structured categories are not pulled).

> Hardcover stores tags in a canonical form, so a push-then-pull may slightly
> change a tag's casing (e.g. `sci-fi` → `Sci-fi`).

## Read dates

When a push creates a **new "Read"** entry on Hardcover (a fresh rating,
review, or Read status), the plugin asks which read date to record:

- **Today** — uses today's date (Hardcover's default).
- **I don't know** — leaves the date blank; Hardcover shows it as "?".
- **Specific date** — pick the date you finished the book.

The dialog lists the affected book title(s) so you know what you're dating.

## Auto-push

Auto-push automatically sends a change to Hardcover whenever you edit a synced
column (Rating, Review, Status, Notes, Quotes). The first time you edit one of
these columns the plugin asks whether you want auto-push on or to keep pushing
manually from the menu; you can change this any time under **Preferences →
Plugins → Hardcover Sync → Customize plugin**.

> **Tags are intentionally NOT included in auto-push.** Tags change often and a
> tag push rewrites your Hardcover "Tag" set, so tag syncing is always manual
> via **Tags → Push Tags / Pull Tags**.

## Best practices

- **Use Cull ISBN to ensure each file has an appropriate ISBN.** A correct ISBN
  helps the metadata plugin match your copy to the right Hardcover edition,
  which in turn keeps every sync accurate.
- **Download Hardcover metadata before syncing.** Books need Hardcover
  identifiers to match — sync skips any book without them.
- **Know which side is more up to date before syncing.** Push and pull
  overwrite the destination, so neither is "safer" by default. If one side is
  fully up to date, push or pull from it accordingly. If your data is mixed
  (some books newer in Calibre, others newer on Hardcover), avoid whole-library
  syncs and instead do targeted syncs on a smaller selection.
- **Sync tags manually.** Tags aren't auto-pushed, and a push replaces your
  Hardcover "Tag" set — review your tags before pushing.

## Custom columns

On first run (after the second restart) the plugin creates these columns if
they don't already exist:

| Column | Key | Type | Used by |
| --- | --- | --- | --- |
| Hardcover Lists | `#hardcover_lists` | Text | Lists |
| Rating | `#hc_rating` | Rating (half-stars) | Ratings |
| Review | `#review` | Long text (HTML) | Reviews |
| Status | `#hc_status` | Enumeration | Status |
| Notes | `#hc_notes` | Long text | Notes |
| Quotes | `#hc_quotes` | Long text | Quotes |

Tags use Calibre's built-in **Tags** field — no column is created.

## Project layout

```text
src/hardcover_list/     Calibre plugin package (sync UI, config, cull ISBN)
lib/graphql/            Bundled Hardcover GraphQL client (module: hcl_graphql)
scripts/bundle.sh       Builds dist/hardcover-list-<version>.zip
```

The bundled GraphQL client (`hcl_graphql/`) is included in the plugin zip at
build time.

## Development

Requires [mise](https://mise.jdx.dev/), [uv](https://docs.astral.sh/uv/), and
[just](https://just.systems). Calibre is installed externally (or via
`just .calibre/source` during `just install`).

```bash
just install        # set up the dev environment
just build          # build dist/hardcover-list-<version>.zip
just install-plugin # build and add the plugin to Calibre
just test           # lib/graphql unit tests
just lint
just bump           # creates a hardcover-list-x.y.z git tag
```

Release tags use the prefix `hardcover-list-` (for example `hardcover-list-0.1.0`).

## License

GPL-3.0 — see [LICENSE](LICENSE). Derived from the upstream Hardcover metadata
plugin work by Rob Brazier; Hardcover Sync plugin by Juan York.
