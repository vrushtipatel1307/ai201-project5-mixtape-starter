# Project 5: Mixtape Bug Hunt — Submission

## AI Usage

**Codebase orientation (Milestone 1).** I had the assistant read through the `services/`
and `routes/` layers and summarize each module's responsibility, and asked it to trace
the request → route → service call chains (e.g. "how does listening to a song update a
streak?"). This was the strongest use: it's fast and reliable for explaining code that
already exists. I checked every summary against the source before writing it into the
codebase map — the map reflects what I confirmed, not what I was told.

**Reproduction (Milestone 2).** I wrote small harness scripts (in a scratch directory,
never committed) that call the service functions directly with controlled inputs, and
used the assistant to help scaffold them quickly. The *decisions* came from running the
code, not from the AI's predictions.

- **Where AI would have led me wrong — Issue #3.** Both my initial plan and the obvious
  "read the code" reasoning said the `outerjoin(song_tags)` in `search_songs` should
  produce duplicate rows for multi-tag songs (the test file's own comment even says "bug
  causes it to be 3"). Running it proved the opposite: SQLAlchemy 2.0's legacy
  `Query.all()` auto-deduplicates full-entity rows, so no duplicates appear in this
  environment and all search tests pass. This is the clearest example in the project of
  why the "reproduce before fixing" discipline matters — the plausible diagnosis was
  wrong until I ran it, and I dropped #3 from my fix set as a result.

**Investigation and fixing (Milestone 3).** For each bug I found the suspect code by
tracing the call chain myself, then used the AI narrowly:

- **Issue #1:** after I had localized the failure to the `today.weekday() != 6` clause, I
  asked the assistant to confirm the difference between `datetime.weekday()` (Sunday = 6)
  and `isoweekday()` (Sunday = 7). I re-checked the return values in a REPL before
  trusting them. I did *not* ask the AI "where is the streak bug?" — that kind of blind
  question is exactly where it tends to sound confident and be wrong.
- **Issues #2 and #5:** essentially no AI involvement in the diagnosis — the 24-hour
  threshold and the `[:-1]` slice were self-evident once I read the relevant lines. I
  used the assistant only to help write the verification scripts that confirmed both
  sides of each boundary.

**Verification I did myself.** Every fix was validated by running controlled inputs
through the service and by running the `pytest` suite (15 tests, all passing — including
The two I added for Issue #4, not added by asking the AI whether the fix looked correct.

---

## Milestone 1 — Codebase Map

### Overview

Mixtape is a Flask + SQLAlchemy social music app. It follows a clean **route → service →
model** layering:

- **Routes** (`routes/`) do only HTTP concerns: parse the request, call one service
  function, format the JSON response, and translate `ValueError` into a 4xx status.
- **Services** (`services/`) hold all business logic and are the only layer that reads
  and writes the database (via the `db` session).
- **Models** (`models.py`) define the SQLAlchemy schema.

Every route delegates immediately to a service — there is essentially no business logic
in the route layer. The five known bugs all live in the `services/` layer.

### Main files and their roles

| File | Responsibility |
|------|----------------|
| `app.py` | Flask **application factory** (`create_app`). Creates the `SQLAlchemy` instance (`db`), configures the SQLite URI, registers the four blueprints under their URL prefixes (`/songs`, `/playlists`, `/users`, `/feed`), and calls `db.create_all()`. |
| `models.py` | All SQLAlchemy models and association tables (see data model below). |
| `seed_data.py` | Drops and recreates all tables, then populates 5 users, 13 songs (with 0 / 1 / 3+ tags), friendships, listening events (recent + old), streaks, 3 playlists, and one sample notification. Run with `python seed_data.py`. |
| `routes/songs.py` | Endpoints for song **search**, **detail**, **rate**, and **listen**. |
| `routes/playlists.py` | Endpoints to **create** a playlist, **get** it, **list its songs**, and **add a song** to it. |
| `routes/users.py` | Endpoints for a user profile, **streak**, **notifications list**, and **mark notification read**. |
| `routes/feed.py` | Endpoints for **"Friends Listening Now"** and the general **activity feed**. |
| `services/streak_service.py` | Records listening events and updates the consecutive-day listening streak. |
| `services/feed_service.py` | Builds the "Friends Listening Now" feed (recency-filtered, deduplicated per friend) and the activity feed. |
| `services/search_service.py` | Searches songs by title/artist (case-insensitive) and fetches a single song. |
| `services/notification_service.py` | Creates/retrieves notifications; also handles adding a song to a playlist and rating a song (both of which should notify the song's original sharer). |
| `services/playlist_service.py` | Creates playlists and returns a playlist's songs ordered by position. |
| `tests/` | `test_streaks.py`, `test_search.py`, `test_playlists.py`. |

### Data model (`models.py`)

Six persisted entities plus three association tables:

- **`User`** — `username`, `email`, `listening_streak`, `last_listened_at`. Has a
  self-referential many-to-many `friends` relationship via the `friendships` table.
- **`Song`** — `title`, `artist`, `album`, `genre`, `shared_by` (FK → `User`),
  `shared_at`, `share_note`. `shared_by` is the key field for notifications: the
  "original sharer" is who gets notified when others interact with the song.
- **`ListeningEvent`** — one row per listen (`user_id`, `song_id`, `listened_at`). This
  is the source of truth for both feeds and (indirectly) streaks.
- **`Rating`** — a user's 1–5 score for a song, with a unique constraint on
  `(user_id, song_id)` so a user can only have one rating per song.
- **`Playlist`** — `name`, `created_by`, `is_collaborative`.
- **`Notification`** — `user_id` (recipient), `notification_type`, `body`, `read`.

Association tables:

- **`friendships`** — symmetric user↔user (seed inserts both directions).
- **`song_tags`** — song↔tag many-to-many.
- **`playlist_entries`** — playlist↔song join table that **adds a `position` column**
  (plus `added_by`, `added_at`). Songs in a playlist have an explicit position, not just
  insertion order — this is what `playlist_service` orders by.

### Data flow trace #1 — a user listens to a song (streak update)

1. `POST /songs/<song_id>/listen` → `routes/songs.py::listen()` parses `user_id` from the
   JSON body.
2. It calls `streak_service.record_listening_event(user_id, song_id)`.
3. That function loads the `User`, creates a `ListeningEvent` with `listened_at = now`,
   then calls `update_listening_streak(user, now)`.
4. `update_listening_streak` compares `now.date()` to `user.last_listened_at.date()`:
   - never listened before → streak = 1
   - same day → no change
   - exactly one day gap → streak += 1
   - larger gap → streak reset to 1
   Then it stores `now` back into `last_listened_at`.
5. The session is committed and the new `ListeningEvent` is returned as JSON.

### Data flow trace #2 — a friend adds your song to a playlist (notification)

1. `POST /playlists/<playlist_id>/songs` → `routes/playlists.py::add_song()` parses
   `song_id` and `added_by`.
2. It calls `notification_service.add_to_playlist(playlist_id, song_id, added_by)`.
3. That function loads the `Song`, adder `User`, and `Playlist`; appends the song to
   `playlist.songs` (which writes a `playlist_entries` row) if not already present.
4. If the adder is **not** the song's original sharer (`song.shared_by != added_by`), it
   calls `create_notification(...)` with type `song_added_to_playlist`, addressed to
   `song.shared_by`.
5. `create_notification` inserts a `Notification` row and commits.

The parallel path — rating a song (`POST /songs/<id>/rate` → `rate_song`) — is meant to
work the same way (notify `song.shared_by`), which is the comparison point for Issue #4.

### Patterns I noticed

- **Thin routes, fat services.** Routes never touch `db` directly (except `users.py`
  reading a `User` for the profile endpoint); they parse input, call one service, and
  map `ValueError` → HTTP 4xx.
- **`ValueError` as the not-found / bad-input signal.** Services raise `ValueError` with
  a message; routes catch it and return 400/404.
- **Time is always UTC.** Timestamps use `datetime.now(timezone.utc)`; naive datetimes
  read back from SQLite are re-tagged as UTC before comparison (see `streak_service`).
- **`playlist_entries.position` drives ordering**, not insertion order — relevant to
  Issue #5.
- **The "original sharer" (`Song.shared_by`) is the notification recipient** for both
  playlist-adds and ratings — relevant to Issue #4.

---

## The Five Open Issues (read all before choosing)

| # | Title | Affected service |
|---|-------|-----------------|
| 1 | My listening streak keeps resetting | `streak_service.py` |
| 2 | Friends Listening Now shows people from yesterday | `feed_service.py` |
| 3 | The same song keeps showing up twice in search | `search_service.py` |
| 4 | Notified when a friend added my song to a playlist but not when they rated it | `notification_service.py` |
| 5 | The last song in a playlist never shows up | `playlist_service.py` |

_Reproduction notes, root cause analysis, and fixes follow in the milestones below._

---

## Milestone 2 — Reproduction Notes

Before touching any fix code, I reproduced each candidate bug by calling the service
functions directly (faster and more controllable than firing HTTP requests, as the brief
suggests). Environment: Python 3.13.14, Flask 3.x, SQLAlchemy 2.0.51, seeded DB
(`python seed_data.py` -> 5 users, 13 songs, 3 playlists). Today's date during
reproduction: 2026-07-07 (a Tuesday).

### Issue #1 — listening streak keeps resetting (reproduced)

**Setup / trigger:** Called `streak_service.update_listening_streak(user, now)` with a
user whose `last_listened_at` was a **Saturday** and `now` set to the following
**Sunday** (2026-07-05, a real Sunday) — i.e. a legitimate consecutive-day listen.

**Observed:** streak went `5 -> 1` (reset) instead of `5 -> 6`. Control run with the same
one-day gap but `now` on a **Monday** correctly produced `5 -> 6`. So the reset is
specific to Sundays. `sunday.weekday()` is `6`; `isoweekday()` is `7`.

### Issue #2 — "Friends Listening Now" shows people from yesterday (reproduced)

**Setup / trigger:** Called `feed_service.get_friends_listening_now(nova.id)`. The seed
creates "recent" events (10–20 min ago) and "older" events (hours-to-days ago).

**Observed:** nova's *listening-now* feed returned 3 friends, two of whom last listened
**31 and 36 minutes ago** — clearly not "now." `RECENT_THRESHOLD` is `timedelta(hours=24)`,
so anything from the last full day counts as "listening now."

### Issue #4 — rating a song doesn't notify the sharer (reproduced)

**Setup / trigger:** simone shares "Crown Heights Anthem." darius rated it 5 stars via
`notification_service.rate_song(darius.id, song.id, 5)`, then I counted simone's
notifications before vs. after.

**Observed:** notification count stayed at **0** (expected 1). By contrast,
`add_to_playlist` *does* call `create_notification` — that's the working reference path
the issue description compares against.

### Issue #5 — the last song in a playlist never shows up (reproduced)

**Setup / trigger:** Called `playlist_service.get_playlist_songs(playlist_id)` for all
three seeded playlists and compared to the raw `playlist_entries` row counts.

**Observed:** every playlist has **7** entries but `get_playlist_songs` returns **6**.
The song at the highest `position` (7) is always the one missing (e.g. "Free Throws" in
"Late Night Vibes").

### Issue #3 — same song shows up twice in search (could NOT reproduce in this environment)

**Attempt:** Called `search_songs()` for queries matching songs with 0, 1, and 3 tags
(e.g. "Crown Heights Anthem", "Borough Kings"), and ran the full `tests/test_search.py`
suite.

**Observed:** every search returned each song **exactly once**; all 5 search tests
(including `test_search_no_duplicates_multi_tag_song`, whose comment says "bug causes it
to be 3") **pass**.

**Why it doesn't reproduce:** `search_songs` uses the legacy SQLAlchemy Query API
(`db.session.query(Song).outerjoin(song_tags)...all()`). In SQLAlchemy 2.0, the legacy
`Query.all()` path **auto-deduplicates full-entity rows by primary key**, so the row
multiplication caused by the `outerjoin` against `song_tags` (one row per tag) is
collapsed before results reach the caller. The bug is real in intent — the query relies
on join behavior it never asked for and would produce duplicates under
`session.execute(select(...))` (which requires an explicit `.unique()`), or if the code
were ported to that 2.0-style API — but it is **masked** by the legacy dedup in this
version, so I cannot honestly claim to have reproduced the visible symptom here.

**Decision:** Because Milestone 2 requires reproducing before fixing, and #3 cannot be
triggered in this environment, my chosen fix set is the four reproducible bugs
**#1, #2, #4, #5** (each documented with a full RCA entry below). #3 remains a documented
latent-but-masked issue — described here for completeness but deliberately left unfixed,
since I could not honestly reproduce its visible symptom in this environment.

## Milestone 3 — Root Cause Analysis Entries

### Issue #1 — My listening streak keeps resetting

**How I reproduced it.** Called `streak_service.update_listening_streak(user, now)`
directly with `user.last_listened_at` on a Saturday (2026-07-04) and `now` on the
following Sunday (2026-07-05) — a legitimate consecutive-day listen. The streak dropped
from `5` to `1` instead of rising to `6`. A control with the same one-day gap but `now`
on a Monday correctly produced `6`, isolating the failure to Sundays specifically.

**How I found the root cause.** Traced the call chain from `POST /songs/<id>/listen`
(`routes/songs.py`) → `record_listening_event` → `update_listening_streak` in
`services/streak_service.py`. Reading the branch logic, line 73 read
`elif days_since_last == 1 and today.weekday() != 6:`. The `and today.weekday() != 6`
clause had no counterpart anywhere in the documented streak rules (the docstring only
describes same-day / consecutive-day / gap cases). I confirmed the diagnosis by printing
`weekday()` for a known Sunday and seeing it return `6`.

**The root cause.** Python's `datetime.weekday()` returns `6` for Sunday (Monday=0 …
Sunday=6). The increment branch was gated on `days_since_last == 1 and today.weekday() != 6`,
so whenever a consecutive-day listen fell on a Sunday the condition was `False`, execution
fell through to the `else`, and the streak was reset to `1`. Every user who listened on a
Sunday lost their streak even though they had listened the day before. The weekday had no
legitimate role in the logic — a consecutive calendar day should always continue the
streak regardless of which day of the week it is.

**My fix and side-effect check.** Removed the spurious `and today.weekday() != 6` clause
so the branch is simply `elif days_since_last == 1:`. I verified all four rule paths on
both sides of the boundary: consecutive listens on Sat→Sun, Sun→Mon, and Mon→Tue all
increment (6); a same-day repeat is a no-op (stays 5); a 3-day gap still resets (1); and a
first-ever listen starts at 1. The full `tests/test_streaks.py` suite (5 tests) passes.

**AI use:** I asked the assistant to confirm the difference between `datetime.weekday()`
and `isoweekday()` after I had already localized the bug to the weekday comparison; I
verified the return values myself in a REPL before committing.

_Fix committed separately on `bugfix/mixtape` (see `git log`)._

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it.** Called `feed_service.get_friends_listening_now(nova.id)` against
the seeded DB. nova's *listening-now* feed returned friends whose most recent listen was
**31 and 36 minutes ago** — not "now." The seed script explicitly comments that its
"recent" events (10–20 min ago) should appear and its "older" events (hours-to-days ago)
should not, so surfacing half-hour-plus-old activity confirmed the bug.

**How I found the root cause.** Traced `GET /feed/<user_id>/listening-now`
(`routes/feed.py`) → `get_friends_listening_now` in `services/feed_service.py`. The query
filters `ListeningEvent.listened_at >= cutoff`, where `cutoff = now - RECENT_THRESHOLD`.
The module-level constant read `RECENT_THRESHOLD = timedelta(hours=24)`. That single line
is the whole cause: a feed labelled "listening **now**" was admitting a full day of
history.

**The root cause.** `RECENT_THRESHOLD` was `timedelta(hours=24)`. "Friends Listening Now"
is meant to show who is *currently* listening, but a 24-hour cutoff includes everything
from the previous day — hence "people from yesterday." The recency window was simply set
far too wide for the feature's meaning.

**My fix and side-effect check.** Changed `RECENT_THRESHOLD` to `timedelta(minutes=30)`,
matching the seed data's own definition of "recent." Verified with controlled events for
nova's friends at 5, 25, and 90 minutes ago: the 5- and 25-minute friends appear and the
90-minute friend is excluded — correct on both sides of the boundary. I also confirmed
`get_activity_feed` still returns all three friends, since it intentionally applies no
recency filter and shares no code with the threshold — so the general activity feed is
unaffected.

**AI use:** none specific to this bug beyond the general orientation pass; the fix was a
direct reading of the constant and the feature's intent.

_Fix committed separately on `bugfix/mixtape` (see `git log`)._

### Issue #4 — Notified when a friend adds my song to a playlist, but not when they rate it

**How I reproduced it.** simone shares "Crown Heights Anthem." I called
`notification_service.rate_song(darius.id, song.id, 5)` and counted simone's notifications
before and after. The count stayed at **0** when it should have been **1**. As a control I
ran the sibling interaction — `add_to_playlist` on the same song by darius — and simone
*did* receive a `song_added_to_playlist` notification. So the notification machinery works;
only the rating path is silent.

**How I found the root cause.** Traced `POST /songs/<id>/rate` (`routes/songs.py`) →
`rate_song` in `services/notification_service.py`, and read it side-by-side with its
sibling `add_to_playlist` in the same module. `add_to_playlist` ends with a guarded
`create_notification(...)` call (notify `song.shared_by` unless the actor *is* the sharer).
`rate_song` persisted the `Rating`, committed, and jumped straight to `return rating` — the
matching `create_notification` step was simply absent. The two paths were meant to be
symmetric; the asymmetry between them was the giveaway.

**The root cause.** The notification is a *side effect* of the interaction, generated by an
explicit `create_notification` call — it is not derived from the `Rating` row. `rate_song`
saved the rating correctly but never made that call, so `song.shared_by` was never told the
song had been rated. The bug wasn't a wrong condition; it was a **missing** notify step that
its counterpart function had and this one didn't.

**My fix and side-effect check.** Added the guarded notify block to `rate_song`, mirroring
`add_to_playlist`: `if song.shared_by != user_id: create_notification(..., "song_rated", ...)`.
Deliberate checks: (a) **self-rating** — the `song.shared_by != user_id` guard means a user
rating their own song creates no notification (verified by a dedicated test); (b) the
**re-rating branch** — when a user updates an existing rating, `rate_song` reaches the same
tail, so it still notifies once and does not duplicate the `Rating` row (the unique
`(user_id, song_id)` constraint is respected); (c) I did not touch `add_to_playlist`, so the
already-working playlist notification path is unchanged. Covered both notify and
no-self-notify with the regression test below; full suite (15 tests) passes.

**AI use:** minimal — I used the assistant only to confirm that the two interaction paths
were *intended* to be symmetric (both notify `song.shared_by`) by comparing their
docstrings; I confirmed the missing call myself by reading `add_to_playlist`.

_Fix committed separately on `bugfix/mixtape` (see `git log`)._

### Issue #5 — The last song in a playlist never shows up

**How I reproduced it.** Called `playlist_service.get_playlist_songs(playlist_id)` for
all three seeded playlists and compared the result length to the raw `playlist_entries`
row counts. Every playlist had **7** entries but the function returned **6**, and the
missing one was always the song at the highest `position` (e.g. "Free Throws" at position
7 in "Late Night Vibes").

**How I found the root cause.** Traced `GET /playlists/<id>/songs`
(`routes/playlists.py`) → `get_playlist_songs` in `services/playlist_service.py`. The
query correctly selects the songs `order_by(asc(playlist_entries.c.position))`, so
ordering was fine. The final line was
`return [song.to_dict() for song in songs[:-1]]`. The `[:-1]` slice was the giveaway —
it drops the last element of an already-correct, position-ordered list.

**The root cause.** The list comprehension sliced the ordered result with `songs[:-1]`,
which excludes the final element. Because the list is ordered by ascending playlist
position, "the final element" is always the song with the highest position — so the last
song added to any playlist was silently omitted from the response. There was no
off-by-one in the query itself; the truncation was purely the stray slice.

**My fix and side-effect check.** Changed the return to
`[song.to_dict() for song in songs]` (removed the `[:-1]`). Verified all three playlists
now return 7 songs, the previously-missing last song is present, and the ordering still
matches ascending `position`. I also checked the empty-playlist edge case: with no
entries the function returns `[]` and does not error (previously `[][:-1]` also gave `[]`,
so that path is unchanged). `tests/test_playlists.py` (3 tests) passes.

**AI use:** none; the `[:-1]` slice was self-evident once I read the return statement.

_Fix committed separately on `bugfix/mixtape` (see `git log`)._

### Regression Test — rating a song should notify the original sharer (covers Issue #4)

I added [tests/test_notifications.py](tests/test_notifications.py) to lock in the Issue #4
fix. The test checks that when one user rates another user's shared song, the song's
original sharer receives exactly one `song_rated` notification, and that a user rating
their own song does not create a self-notification. This would have failed against the
pre-fix code because `rate_song()` committed the `Rating` row and returned immediately,
never calling `create_notification()` for the rating branch — so the sharer's notification
count stayed at 0. Against the fixed code both assertions pass.

---

## Commit History

All bug work lives on the `bugfix/mixtape` branch, one fix per commit, in conventional
`fix:` format. The docs commits capture the milestone writeups.

```
976502f fix: notify the song's original sharer when their song is rated
23e4bc7 docs: write AI usage section for final review (Milestone 4)
23128c8 fix: stop dropping the last song in a playlist
5c7cad4 fix: narrow listening-now window from 24h to 30m
8e9628d fix: continue listening streak across the Sunday boundary
5b9c331 docs: add codebase map, reproduction notes, and RCA writeup
2dfdeaa Add .gitignore file and update README with setup instructions
7b64551 initial commit
```

Four `fix:` commits, each corresponding to one bug (#1 streak, #2 listening-now window,
#5 playlist last-song, #4 rating notification), each message specific enough to identify
the bug without reading the diff.
