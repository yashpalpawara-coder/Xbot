export type SessionInfo = {
  platform: string;
  status: string;
  handle: string;
  detail: string;
  checked_at?: string;
};

export type Health = {
  status: string;
  database: string;
  database_detail: string;
  browser_profile: string;
};

export type Post = {
  id: string;
  external_id: string;
  author: string;
  handle: string;
  text: string;
  url: string;
  language: string;
  relevance: number | null;
  search_id?: string | null;
  created_at?: string | null;
  collected_at?: string;
  latest_views?: number | null;
  latest_likes?: number | null;
  latest_reposts?: number | null;
  latest_replies?: number | null;
};

export type Analysis = {
  id: string;
  post_id: string;
  payload: {
    main_topic?: string;
    key_message?: string;
    audience?: string;
    discussion_context?: string;
    engagement_quality?: string;
    opportunity?: string;
    risks?: string;
    reply_angle?: string;
    relevance?: number;
    discussion?: { handle?: string; text?: string }[];
  };
  raw_response?: string;
  created_at?: string;
};

export type Comment = {
  id: string;
  post_id: string;
  text: string;
  variant: string;
  version: number;
  status: string;
  idempotency_key?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type Job = {
  id: string;
  type: string;
  status: "running" | "done" | "error" | "stopped" | string;
  progress: number;
  message: string;
  error?: string | null;
  results_count: number;
  logs?: string[];
  result?: {
    posts?: Post[];
    query?: string;
    analysis?: Analysis;
    comments?: Comment[];
    warnings?: string[];
    reason?: string;
    comment?: Comment;
    event?: { status?: string; detail?: string };
    session?: SessionInfo;
    text?: string;
    posted?: boolean;
  } | null;
};

export type Settings = {
  product_name: string;
  product_url: string;
  product_pitch: string;
  allow_promotion: boolean;
  search_limit: number;
  min_views: number | null;
  min_likes: number | null;
  x_username: string;
  x_password_set: boolean;
};

export type ActivityCard = {
  id: string;
  handle: string;
  text: string;
  url: string;
  reply_to?: string;
  created_at?: string;
  views?: number;
  likes?: number;
  replies?: number;
  reposts?: number;
  parent_text?: string;
  category?: string;
  why?: string;
  draft?: { status?: string; reason?: string; text?: string; posted?: boolean } | null;
};

export type ActivityCategory = { name: string; count: number };

export type DailyDraft = {
  status?: string;
  posted?: boolean;
  posted_url?: string;
  date?: string;
  written_at?: string;
  text: string;
  hashtags?: string[];
  why?: string;
  best_time?: string;
  audience?: string;
  length?: number;
  intent_url?: string;
  analysis?: { summary?: string; used?: string };
};

export type PostDetails = {
  writeup?: string;
  summary?: string;
  name?: string;
  url?: string;
  verified?: boolean;
  angle?: string;
  audience?: string;
  shape?: string;
  facts?: string[];
  avoid?: string;
};

export type VerifiedProduct = {
  id: string;
  name: string;
  url: string;
  detail: string;
  finder_verified?: boolean;
  verified_at?: string;
};

export type ComingPost = {
  id: string;
  url: string;
  handle?: string;
  note?: string;
  views?: number | null;
  external_id?: string;
  post_id?: string;
  comment_id?: string | null;
  reply?: string;
  status?: string;
};

export type DailyPostState = {
  post: DailyDraft | null;
  is_today: boolean;
  count: number;
  pending: DailyDraft | null;
  details?: PostDetails | null;
};

export type ActivitySnapshot = {
  handle: string;
  collected_at: string;
  posts_created: number;
  comments_dropped: number;
  views: number;
  likes: number;
  replies_received: number;
  posts: ActivityCard[];
  comments: ActivityCard[];
  responses: ActivityCard[];
  categories: ActivityCategory[];
  summary: string;
  note: string;
  stopped: boolean;
};

export type Overview = {
  counts: {
    posts: number;
    analyses: number;
    comments: number;
    approved: number;
    published: number;
    failed: number;
    uncertain: number;
  };
  logs: { id: string; action: string; status: string; detail: string; created_at?: string }[];
  database: string;
};

export type SavedItem = {
  id: string;
  item_type: string;
  item_id: string;
  tags: string[];
  created_at?: string;
  post?: Post | null;
};
