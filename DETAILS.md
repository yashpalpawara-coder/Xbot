# XBot

XBot is a local operator app for one product account at a time. It reads a product from a name and a URL, finds public X posts in that audience's conversations, and writes one reply under each saved post. A person approves a reply before it is sent.

The dashboard is a Next.js app. The API is FastAPI. Chrome is driven by Playwright, using one saved Chrome profile for X and ChatGPT. Grok is used inside X. ChatGPT writes the product analysis, the original posts, and the replies.

## Processes

| Process | Where | Address |
| --- | --- | --- |
| Dashboard | `frontend` | http://127.0.0.1:3000 |
| API and browser | `backend` | http://127.0.0.1:8787 |

The browser starts with `next dev --port 3000`. The API starts with `python -m app` from `backend` and listens on port 8787. The dashboard calls `/api/...` on itself. `frontend/app/api/[...path]/route.ts` forwards those calls to `API_URL`, which defaults to `http://127.0.0.1:8787`.

Only one browser job runs at a time. `JobManager` holds a lock. A second job is rejected with `browser_busy` until the current job finishes or is stopped.

## Folder structure

```text
twitter-main/
├── DETAILS.md                 this file
├── README.md                  setup, run, and deploy
├── .python-version            3.12
├── .gitignore
├── cloudbuild.yaml            Cloud Build for GCP
├── deploy/gcp.sh              Cloud Run deploy script
├── supabase/schema.sql        Postgres tables used when Supabase is on
├── backend/
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── Dockerfile
│   ├── .env.example
│   ├── app/
│   │   ├── __main__.py        starts uvicorn
│   │   ├── main.py            FastAPI app, CORS, error shape
│   │   ├── config.py          environment and paths
│   │   ├── state.py           one Store, JobManager, and Service
│   │   ├── routes.py          HTTP API
│   │   ├── schemas.py         request and ChatGPT JSON shapes
│   │   ├── services.py        workflows
│   │   ├── jobs.py            background jobs and the browser lock
│   │   ├── store.py           local JSON store and optional Supabase
│   │   ├── browser_session.py Playwright: X, Grok, ChatGPT
│   │   ├── grok_find.py       Grok prompts, limit detection, query parsing
│   │   ├── prompts.py         analysis, studio comments, original posts
│   │   ├── reply_rules.py     coming-list reply prompt and checks
│   │   ├── writer.py          product analysis and the daily post
│   │   ├── activity.py        scan of the signed-in account
│   │   ├── scoring.py         relevance score for manual search
│   │   ├── validation.py      comment length and JSON parsing
│   │   ├── x_account.py       which X login belongs to which product
│   │   └── errors.py
│   ├── tests/
│   └── data/                  local only, not committed
│       ├── store.json
│       ├── daily_post.json
│       ├── activity.json
│       ├── activity-<product-id>.json
│       └── chrome-profile/
└── frontend/
    ├── package.json
    ├── .env.example
    ├── app/
    │   ├── page.tsx                 Overview
    │   ├── finder/page.tsx          product find and manual search
    │   ├── coming/page.tsx          reply queue
    │   ├── analytics/page.tsx       product analysis
    │   ├── ranks/page.tsx           top posts and comments by views, overall and today
    │   ├── settings/page.tsx        X login and limits
    │   ├── saved/page.tsx           library
    │   ├── posts/[id]/page.tsx      one collected post
    │   ├── studio/[id]/page.tsx     analyse, draft, approve, publish
    │   ├── api/[...path]/route.ts   proxy to the Python API
    │   └── layout.tsx
    ├── components/
    │   ├── shell.tsx                nav and product switcher
    │   ├── settings-screen.tsx
    │   └── post-card.tsx
    └── lib/
        ├── api.ts
        ├── types.ts
        └── use-job.ts               polls a job until it finishes
```

## Screens

The shell lists every saved product and switches the X login when the selection changes.

| Route | Screen | What it does |
| --- | --- | --- |
| `/` | Overview | Session checks for X and ChatGPT, recent posts, recent jobs |
| `/analytics` | Analysis | Ask ChatGPT to read the product page, revise the writeup, verify it |
| `/ranks` | Ranks | Top 5 posts and comments by views, plus a day-by-day view comparison, why posts show no likes, and how today's comments differ from yesterday's |
| `/finder` | Finder | Verify the product for finding, run Grok find, or search X directly |
| `/coming` | Coming | The selected product's saved posts, then one reply each |
| `/drafts` | Drafts | Replies that are written and still waiting for approval. The nav shows how many are waiting. |
| `/saved` | Library | Bookmarked posts, searches, comments, and creators |
| `/posts/[id]` | Post | One collected post, its metrics, analysis, and comments |
| `/studio/[id]` | Studio | Analyse a post, draft three replies, approve one, publish it |
| `/settings` | Settings | Product fields, X username and password, search limits |

Analysis in the nav is the product writeup. It is not the analytics overview endpoint. The overview counts live on the home screen and at `GET /api/analytics/overview`.

## How a job runs

Long work does not stay inside the HTTP request. `Service` calls `JobManager.start(kind, work)`. That returns a job id immediately. A daemon thread runs the work.

The dashboard polls `GET /api/jobs/{id}` about once a second through `useJob`. There is also `GET /api/jobs/{id}/events`, a server-sent stream every 0.8 seconds. `POST /api/jobs/{id}/stop` sets a cancel event. The worker checks that event between steps.

A public job looks like this:

| Field | Meaning |
| --- | --- |
| `id` | UUID |
| `type` | Workflow name, listed below |
| `status` | `running`, `done`, `error`, or `stopped` |
| `progress` | 0 to 100 |
| `message` | Latest line |
| `error` | Set when status is `error` |
| `results_count` | Posts collected, replies posted, or a similar count |
| `logs` | Recent lines, capped at 200 |
| `result` | Whatever the worker returned |

Job types:

| Type | Started by |
| --- | --- |
| `x_check` | Check the X session |
| `chatgpt_check` | Check the ChatGPT session |
| `x_switch` | Select another product and sign into its X account |
| `post_details` | Analyse a product, or revise that analysis |
| `daily_post` | Write an original post in the product chat |
| `publish_post` | Publish that original post |
| `grok_find` | Find audience posts with Grok, with a ChatGPT fallback |
| `finder_search` | Search X with a typed query or open one status URL |
| `coming_comments` | Reply to each saved post, then like it |
| `analyse` | Analyse one collected post |
| `comments` | Draft three studio replies |
| `regenerate` | Rewrite one studio reply |
| `publish` | Send an approved studio reply |
| `publish_check` | Look again when a reply was not confirmed |
| `activity` | Scan the signed-in account |
| `activity_draft` | Draft a reply to one item from that scan |

## Workflow

### 1. Save the X login

Settings stores the username and password on the selected product. The password is written to the local store and is removed from API responses and from export. `x_password_set` tells the dashboard that a password exists.

Each product keeps its own `x_username`, `x_password`, and `x_handle`. Choosing another product opens that product's X page and leaves it for 5 minutes. The saved username and password are submitted only if that account is still signed out when the 5 minutes end.

### 2. Analyse the product

On Analysis, the operator enters a name and a URL.

1. `POST /api/post/details` opens ChatGPT.
2. The first message is `product_prompt`: read the page and write one analysis with the sections What it is, Features, Target users, Audience type, and what to use when promoting it.
3. If that reply is still incomplete, a second message in the same chat asks for the full analysis again.
4. The writeup is stored in `backend/data/daily_post.json` under `details`, with the ChatGPT chat URL.
5. The operator can revise it with `POST /api/post/details/revise`. That stays in the same chat.
6. Verify with `POST /api/post/details/verify`. The text on the page must match the saved writeup. Verification creates or updates the product row and selects it.

Finder has its own verify step with `for_finder: true`. Find stays disabled until `finder_verified` is true and the on-screen detail still matches the saved detail.

### 3. Find posts

Finder has two ways in.

**Manual search.** The form posts `SearchIn` to `POST /api/finder/search`. The query is built from topic, keywords, hashtags, handles, since, until, and language. A status URL opens that post instead of a search. Results are scored, filtered by the optional minimums, stored as posts, and shown on the page.

**Grok find.** `POST /api/finder/grok` with `count` from 1 to 100.

1. Open the selected product's X page and leave it. Sign in by hand in that window. The page stays open for 5 minutes. After 5 minutes, if that account is still signed out, the saved username and password are submitted. Stop cancels the wait.
2. Open Grok in that Chrome profile and send `product_message`. This pastes the verified name, URL, and detail, and tells Grok not to search yet.
3. Send `find_message`. Grok should return posts from the audience's content universe: problems, habits, and topics from the analysis. It should skip ads, giveaways, bare links, and unrelated trends. A smaller creator with the right audience is preferred over a huge unrelated account. Each kept post needs a real status URL and more than 500 views or impressions when a count is shown.
4. Up to seven follow-ups ask for `count` more posts. The loop stops when the job is cancelled, a batch adds nothing new, or Grok's limit is reached.

Posts are saved on the product's coming list. The same status id is not added twice for that product. Each product keeps its latest 200 coming rows.

### 4. When Grok's limit banner appears

The limit is the banner on the Grok page, not only a sentence inside the reply. The reader treats these as the limit: "limit reached", "daily limit", "free tier limit", "try again later", "upgrade to premium", and the same ideas in a slightly different word order. It checks the page text, including open shadow roots, and looks for the visible banner.

If that banner is up, or Grok never finishes the post reply, Find stops asking Grok and continues in the same browser:

1. Open ChatGPT. An existing product chat at `https://chatgpt.com/c/...` is reused.
2. Run the same product analysis as Analysis: read the page and write What it is, Features, Target users, Audience type, and the promotion facts.
3. In that same chat, ask for 10 creators whose audience already discusses the product's problems, then 30 X search queries. The queries use the audience's words. They do not search for the product name, "download", or a URL.
4. Save both lists on the product, as `creators` and `search_queries`, and save each search as a search row. Finder shows them under Saved creators and Saved search queries.
5. Search X on Latest with each query, then with `from:handle` for each creator. Latest is why a new match can appear before an old one. Each search tries for 5 posts. A post is kept only when it shows more than 500 views or the view count is missing. A shown count of 500 or fewer is skipped. The posts that clear that line are ordered by the better reply: overlap with the saved analysis or the search query, a real question or a specific situation, a recent reply when the time is known, and how recently the post was published. Views are not the ranking. An older post stays eligible. A weak match is left out. A post this account already replied to is not drafted again. If fewer than 5 posts clear that line, the search keeps the ones that do.
6. For each kept post, wait 30 seconds, open the author's account, then open About this account and read the country. That country is saved on the post and shows in the country list. The same author is not opened again during that find. If About this account does not load, the profile location is used when it names a country. Otherwise the post is Unknown. Stop cancels the wait.
7. Wait 30 seconds after each search before the next. Stop cancels the wait. Finder shows a country table and a country dropdown. Choosing a country shows only that country's posts.

### 5. Draft coming replies

Coming lists the selected product's saved posts. A country dropdown and table filter that list. Draft replies and Post replies use the country selected there. All countries uses the whole list. The page also shows the last X login and an X login button. That button only opens the X page. Sign in by hand in that window. After 5 minutes, the saved username and password are submitted if the account is still signed out. `POST /api/coming/comment` walks rows whose status is `saved`. A list that already has drafts and nothing new returns a review message. A reply that passes is posted immediately and is not saved as a draft.

For each post:

Draft replies waits 30 seconds before each post after the first. Stop cancels the wait.

1. Open the status and load the comments under it. Each comment keeps its text, length, likes, replies, and views. A post with no comments can still be drafted.
2. Skip a post unless it shows more than 500 views, or likes, replies, and reposts together are more than 500. A missing view count does not pass on its own. Then skip a personal health disclosure, a conversation this account already replied to, and a post that shares no topic with the saved analysis. A question mark by itself is not enough to keep it. A specific situation, such as a missed workout or a routine, can still be drafted. Age does not remove it. A post that cannot be opened is skipped.
3. Ask ChatGPT, in the product chat, for one reply or a `SKIP` line. The reply starts from what the post is asking or describing. Comments already under the post stay loaded. When comments are there, they are reference only. When none are there, the reply comes from the post itself. A direct ask for an app or a solution gets one short reply in the post's language, with the product URL. An indirect problem gets two lines: the first answers the post, and the second mentions the product as the solution with the URL, only when a saved fact does that. A GIF line is kept only when the draft asks for one, and it is attached when the reply is posted. The full saved analysis is passed, including limitations.
4. `reply_problems` rejects an empty reply, a hashtag, generic praise, generic motivation, a link on a post that is not asking for a solution, a missing product URL on a direct ask, an indirect reply that puts the product on the first line, a download pitch, a reply written as a user of the product, a reply that does not address the post, a question that misses the point, a claim the saved features do not support, a reply that repeats a recent reply, a reply under about 20 letters, a reply over 280 weighted characters, or a reply whose quality score is under 70. The score is specific relevance up to 30, a useful addition up to 25, naturalness up to 18, originality 15, and accuracy up to 8. Sharing a keyword, including a number, or using a contraction is not enough to pass. A skip or a failed check sets that score to 0. One rewrite is attempted. A reply that still fails, or a `SKIP`, is marked `skipped`.
5. A reply that passes is posted on that X post from the signed-in account. It is stored as `published`, or `uncertain` when the reply cannot be confirmed. It does not appear on Drafts. The job does not like the post. Then it waits 30 seconds before the next post. Stop cancels the wait.

`POST /api/coming/post` is the Post replies button on Coming and on Drafts. It sends every drafted reply for the selected product from the signed-in account. That click is the approval, so the drafts do not need a separate approve step. It waits 2 minutes after each reply before the next one. Stop cancels the wait. A reply that fails to send stays `drafted`. A reply that cannot be confirmed is `uncertain` and is not sent again. The job does not like the post. The studio page can still approve and publish one reply on its own.

### 6. Studio path for one post

This is separate from the coming queue.

1. `POST /api/posts/{id}/analyse` asks ChatGPT for a JSON analysis: topic, message, audience, context, engagement, opportunity, risks, reply angle, relevance from 0 to 100, product fit, author intention, and a REPLY or SKIP decision.
2. `POST /api/posts/{id}/comments` asks for one reply in `natural`. `should_reply` is false when the right outcome is to skip. The old `insightful` and `question` fields stay in the response so earlier drafts still load. Promotion follows the setting. A link is allowed only when the author asked for one and promotion is on.
3. The operator can edit, regenerate one variant, and approve.
4. Publish needs an `idempotency_key`. The same key does not send the reply twice. The publish event is `published` or `uncertain`.

### 7. Original daily post

After the product analysis is verified, `POST /api/post/today` writes one original post in the same ChatGPT chat. It picks a different kind from the recent posts: a relatable moment, a practical tip, or a demonstration of one supported feature. The product name and the URL stay off the first two kinds. A demonstration can include the URL once, and the owner attaches a real screen recording before approving it. A draft is rejected when it repeats an opening, repeats the same kind twice in a row, falls back to small steps or stay consistent, or claims an award or a testimonial the saved analysis does not contain. `POST /api/post/approve` publishes the approved text from the X composer. Nothing is posted until that approval.

### 8. Activity scan

`POST /api/activity/scan` reads the signed-in account and stores a snapshot. The file is `backend/data/activity.json`, or `activity-<product-id>.json` when a product is selected. A later draft for one scanned reply uses `POST /api/activity/responses/{id}/draft`. Nothing is posted by the scan itself.

## Data

`DATABASE_MODE=auto` uses Supabase when the URL and publishable key are set and `supabase/schema.sql` has been applied. Otherwise the API uses `backend/data/store.json`. Products, the coming list, and post ownership always stay in that local file, including when other tables are in Supabase.

Passwords are stored locally. They are not returned by the product or settings endpoints, and they are stripped from export.

### Local store file

`store.json` is one JSON object. List keys match `TABLES` in `store.py`. `settings` is one object, not a list. `post_owners` is added when posts are tied to a product.

| Key | Role |
| --- | --- |
| `settings` | Selected product and the public settings fields |
| `products` | Up to 20 saved products |
| `coming` | Posts waiting for a reply, latest 200 per product |
| `post_owners` | Map of post id to product id |
| `searches` | Manual searches and the 30 fallback queries |
| `posts` | Collected status rows, unique by `external_id` |
| `metrics` | A metric snapshot each time a post is collected |
| `analyses` | Studio JSON analyses |
| `comments` | Drafts and sent replies |
| `jobs` | Last copy of each job |
| `saved` | Library bookmarks |
| `events` | Publish attempts, unique by idempotency key |
| `logs` | Last 500 action lines |
| `accounts` | Last X and ChatGPT session check |

### Product

| Field | Meaning |
| --- | --- |
| `id` | UUID |
| `name`, `url` | Display name and product page |
| `detail` | Verified analysis text |
| `finder_verified` | Find is allowed |
| `verified_at` | When it was verified |
| `x_username`, `x_password`, `x_handle` | That product's X login |
| `chat_url` | `https://chatgpt.com/c/...` for this product |
| `search_queries` | The 30 queries saved after a Grok limit |
| `creators` | The 10 handles saved in that same fallback |

The public product replaces `x_password` with `x_password_set`.

### Coming row

| Field | Meaning |
| --- | --- |
| `id` | UUID for the queue row |
| `product_id` | Owner |
| `external_id` | X status id |
| `post_id` | Row in `posts` |
| `url`, `handle`, `note`, `views` | What Finder shows |
| `status` | `saved`, `drafted`, `posting`, `published`, `uncertain`, or `skipped` |
| `reply` | The text that was drafted or posted |
| `comment_id` | Studio-style comment row when one was stored |
| `added_at` | When it joined the list |

### Post

| Field | Meaning |
| --- | --- |
| `id` | Local UUID |
| `external_id` | X status id, unique |
| `author`, `handle`, `text`, `url` | The public post |
| `language` | Guessed language |
| `relevance` | Score for manual search, or empty when Grok found it |
| `search_id` | Search that collected it, when there was one |
| `created_at` | Time on the post, when X showed it |
| `collected_at` | When XBot stored it |
| `latest_views`, `latest_likes`, `latest_reposts`, `latest_replies` | Last seen counts |

### Other local rows

A search stores `id`, `query`, `filters`, and `created_at`. Fallback queries set `filters.source` to `chatgpt` and `filters.limit` to 2.

A metric stores `id`, `post_id`, `views`, `likes`, `reposts`, `replies`, and `collected_at`.

An analysis stores `id`, `post_id`, `payload` (the JSON object), `raw_response`, and `created_at`.

A comment stores `id`, `post_id`, `text`, `variant`, `version`, `status`, `idempotency_key`, `created_at`, and `updated_at`. Status moves from `draft` to `approved`, then `published` or `uncertain`.

A publish event stores `id`, `comment_id`, `status`, `idempotency_key`, `detail`, and `published_at`.

A saved item stores `id`, `item_type` (`post`, `creator`, `search`, or `comment`), `item_id`, `tags`, and `created_at`.

An account check stores `platform` (`x` or `chatgpt`), `handle`, `session_status`, `detail`, and `checked_at`.

### Files beside the store

| File | Contents |
| --- | --- |
| `daily_post.json` | `details` for the current analysis, plus drafted and posted original posts |
| `activity.json` | Account scan when no product is selected |
| `activity-<product-id>.json` | Account scan for that product |
| `chrome-profile/` | Cookies and site data for X and ChatGPT |

`details` keeps `writeup`, `summary`, `name`, `url`, `verified`, `verified_at`, and `chat_url`.

### Supabase tables

`supabase/schema.sql` creates `searches`, `posts`, `post_metrics`, `analyses`, `comments`, `jobs`, `saved_items`, `publish_events`, `activity_logs`, `accounts`, and a single `settings` row. Posts are unique on `external_id`. Publish events are unique on `idempotency_key`. Saved items are unique on `(item_type, item_id)`.

Row level security is enabled with an operator policy that allows the publishable key to read and write. That matches a single-operator app. It is not an access model for more than one user.

The local names and the SQL names differ in three places: local `metrics` is SQL `post_metrics`, local `saved` is SQL `saved_items`, local `events` is SQL `publish_events`, and local `logs` is SQL `activity_logs`.

## HTTP API

All routes are under `/api`. Errors use `{ "error": { "code": "...", "message": "..." } }` with an HTTP status. Validation failures are 422 with code `invalid`.

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| GET | `/health` | | database mode and profile path |
| GET | `/sessions/status` | | X, ChatGPT, and database |
| POST | `/sessions/x/check` | | starts `x_check` |
| POST | `/sessions/chatgpt/check` | | starts `chatgpt_check` |
| GET | `/sessions/screen` | | latest browser PNG, or 204 |
| POST | `/sessions/input` | `{x, y, text, key}` | click, type, or key in that browser |
| GET | `/products` | | products and `selected_id` |
| POST | `/products/select` | `{id}` | starts `x_switch` |
| GET | `/product` | | selected product |
| POST | `/post/details` | `{name, url}` | starts analysis |
| POST | `/post/details/revise` | `{text}` | starts a revision |
| POST | `/post/details/verify` | `{name, url, detail, for_finder}` | saves the product |
| GET | `/post/today` | | analysis and today's draft |
| POST | `/post/today` | `{text}` | starts the original post |
| POST | `/post/approve` | `{text}` | publishes that post |
| POST | `/finder/grok` | `{count}` | starts Grok find |
| POST | `/finder/search` | `SearchIn` | starts a manual search |
| GET | `/finder/coming` | | coming rows for the selected product |
| GET | `/finder/posts` | `limit` | posts for the selected product |
| POST | `/coming/comment` | | starts the reply queue |
| POST | `/coming/post` | | posts every drafted reply without a separate approval |
| GET | `/posts/{id}` | | post, metrics, analysis, comments |
| POST | `/posts/{id}/analyse` | | starts analysis |
| POST | `/posts/{id}/comments` | `{allow_promotion?}` | starts three drafts |
| PATCH | `/comments/{id}` | `{text}` | edits a draft |
| POST | `/comments/{id}/regenerate` | | rewrites one variant |
| POST | `/comments/{id}/approve` | `{text?}` | approves |
| POST | `/comments/{id}/publish` | `{idempotency_key}` | sends one reply |
| POST | `/comments/{id}/confirm` | | checks an uncertain reply |
| GET | `/jobs` | `limit` | recent jobs |
| GET | `/jobs/{id}` | | one job |
| GET | `/jobs/{id}/events` | | server-sent events |
| POST | `/jobs/{id}/stop` | | cancel |
| GET | `/saved` | | library |
| POST | `/saved` | `{item_type, item_id, tags}` | bookmark |
| DELETE | `/saved/{id}` | | remove a bookmark |
| GET | `/settings` | | public settings |
| PUT | `/settings` | `SettingsIn` | save settings and the X login |
| GET | `/analytics/overview` | | counts, recent logs, database, product name |
| GET | `/activity` | | latest scan |
| POST | `/activity/scan` | | starts a scan |
| POST | `/activity/responses/{id}/draft` | | drafts one scanned reply |
| GET | `/export` | | posts, analyses, comments, saved, jobs, logs, without passwords |

### Request shapes that matter

`SearchIn`: `topic`, `keywords`, `hashtags`, `handles`, `url`, `since`, `until`, `min_views`, `min_likes`, `min_replies`, `language` (`en`, `fr`, `ja`, or empty), `sort` (`relevant`, `likes`, `views`, `recent`), `limit` from 1 to 40.

`GrokFindIn`: `count` from 1 to 100, default 20.

`SettingsIn`: `product_name`, `product_url`, `product_pitch`, `allow_promotion`, `search_limit`, `min_views`, `min_likes`, `x_username`, `x_password`, `selected_product_id`. An empty password on save keeps the password already stored.

`AnalysisBody` is what ChatGPT must return for a studio analysis. `CommentBody` is the three-reply object with `should_reply` and `reason`.

## Browser

`BrowserSession` opens Google Chrome through Playwright and keeps the profile in `BROWSER_PROFILE_DIR`. Locally that is `backend/data/chrome-profile`. `HEADLESS=false` leaves the window open so an X or ChatGPT check can be finished by hand.

X search uses `https://x.com/search` with the live tab. Posts are read from `article[data-testid="tweet"]`. Replies use the status page, the reply box, and one click. The coming flow likes the same post after the reply.

Grok uses `https://x.com/i/grok` and stays on the conversation id in the URL. ChatGPT stays on `https://chatgpt.com/c/<id>` once that id exists. A later message for the same product is typed into that chat instead of opening a new one.

The limit banner is read from the page, including frames and open shadow roots. When it is visible, `ask_grok` returns immediately and Find switches to ChatGPT.

## Configuration

Backend values come from the environment and `backend/.env`.

| Variable | Role |
| --- | --- |
| `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | Optional remote database |
| `DATABASE_MODE` | `auto` or `local` |
| `X_USERNAME`, `X_PASSWORD`, `X_HANDLE` | Fallback login when the product has none |
| `CHATGPT_EMAIL`, `CHATGPT_PASSWORD` | Optional ChatGPT sign-in |
| `BROWSER_PROFILE_DIR` | Chrome profile |
| `X_HOME_URL`, `CHATGPT_HOME_URL` | Start pages |
| `HEADLESS` | `false` shows Chrome |
| `CORS_ORIGINS` | Extra dashboard origins, comma-separated |
| `LOCAL_STORE_PATH` | Override for `store.json` |
| `HOST`, `PORT` | API bind, default `127.0.0.1:8787` |

Frontend:

| Variable | Role |
| --- | --- |
| `API_URL` | Python API, default `http://127.0.0.1:8787` |
| `NEXT_PUBLIC_SUPABASE_URL` | Present for the dashboard Supabase client |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Same |

These files stay off git: `backend/.env`, `frontend/.env.local`, `backend/data/store.json`, the activity files, `daily_post.json`, and `backend/data/chrome-profile/`.

## Run and test

From `backend`:

```bash
../.venv/bin/python -m app
../.venv/bin/python -m pytest
```

From `frontend`:

```bash
npm install
npm run dev
```

Open http://127.0.0.1:3000. Tests use a temporary local store and do not open Chrome. Browser workflows are covered by fakes that return Grok text, ChatGPT text, and search results.

## Deploy shape

`deploy/gcp.sh` builds two Cloud Run services. The dashboard is Next.js. The API image includes Playwright Chromium, with 2 CPU, 4 GiB, and a minimum of 1 instance so the in-memory job and the browser stay alive. A bucket can hold the Chrome profile between restarts. Supabase is the durable database on that path. X may still ask for an extra login check from a Google IP. That check is completed in the open browser, or the local `chrome-profile` directory is copied into the bucket after a local login.
