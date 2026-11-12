-- Run this once in the Supabase SQL editor for project vbslcqgkyexrzeepoaml.
-- The publishable key can read and write these tables. That matches a single-operator
-- local app. Replace these policies with authenticated-user policies before anyone else
-- uses the project.

create extension if not exists pgcrypto;

create table if not exists searches (
  id uuid primary key,
  query text not null,
  filters jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists posts (
  id uuid primary key,
  external_id text not null unique,
  author text not null default '',
  handle text not null default '',
  text text not null default '',
  url text not null default '',
  language text not null default '',
  relevance double precision,
  search_id uuid references searches(id) on delete set null,
  created_at timestamptz,
  collected_at timestamptz not null default now(),
  latest_views integer,
  latest_likes integer,
  latest_reposts integer,
  latest_replies integer
);

create table if not exists post_metrics (
  id uuid primary key,
  post_id uuid not null references posts(id) on delete cascade,
  views integer,
  likes integer,
  reposts integer,
  replies integer,
  collected_at timestamptz not null default now()
);

create table if not exists analyses (
  id uuid primary key,
  post_id uuid not null references posts(id) on delete cascade,
  payload jsonb not null,
  raw_response text not null default '',
  created_at timestamptz not null default now()
);

create table if not exists comments (
  id uuid primary key,
  post_id uuid not null references posts(id) on delete cascade,
  text text not null,
  variant text not null,
  version integer not null default 1,
  status text not null default 'draft',
  idempotency_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists jobs (
  id uuid primary key,
  type text not null,
  status text not null,
  progress integer not null default 0,
  message text not null default '',
  error text,
  results_count integer not null default 0,
  logs jsonb not null default '[]'::jsonb,
  result jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists saved_items (
  id uuid primary key,
  item_type text not null,
  item_id uuid not null,
  tags text[] not null default '{}',
  created_at timestamptz not null default now(),
  unique (item_type, item_id)
);

create table if not exists publish_events (
  id uuid primary key,
  comment_id uuid not null references comments(id) on delete cascade,
  status text not null,
  idempotency_key text not null unique,
  detail text not null default '',
  published_at timestamptz
);

create table if not exists activity_logs (
  id uuid primary key,
  action text not null,
  status text not null,
  detail text not null default '',
  created_at timestamptz not null default now()
);

create table if not exists accounts (
  id uuid primary key,
  platform text not null unique,
  handle text not null default '',
  session_status text not null,
  detail text not null default '',
  checked_at timestamptz not null default now()
);

create table if not exists settings (
  id integer primary key,
  payload jsonb not null default '{}'::jsonb
);

insert into settings (id, payload) values (1, '{}'::jsonb)
on conflict (id) do nothing;

create index if not exists posts_collected_idx on posts (collected_at desc);
create index if not exists metrics_post_idx on post_metrics (post_id, collected_at desc);
create index if not exists comments_post_idx on comments (post_id, created_at desc);
create index if not exists analyses_post_idx on analyses (post_id, created_at desc);

grant usage on schema public to anon, authenticated;
grant all on all tables in schema public to anon, authenticated;

alter table searches enable row level security;
alter table posts enable row level security;
alter table post_metrics enable row level security;
alter table analyses enable row level security;
alter table comments enable row level security;
alter table jobs enable row level security;
alter table saved_items enable row level security;
alter table publish_events enable row level security;
alter table activity_logs enable row level security;
alter table accounts enable row level security;
alter table settings enable row level security;

do $$
declare
  tbl text;
begin
  foreach tbl in array array[
    'searches', 'posts', 'post_metrics', 'analyses', 'comments', 'jobs',
    'saved_items', 'publish_events', 'activity_logs', 'accounts', 'settings'
  ]
  loop
    execute format('drop policy if exists operator_all on %I', tbl);
    execute format(
      'create policy operator_all on %I for all to anon, authenticated using (true) with check (true)',
      tbl
    );
  end loop;
end $$;
