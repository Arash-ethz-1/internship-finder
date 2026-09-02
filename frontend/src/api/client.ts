/**
 * The only module that knows about API URLs.
 *
 * Every type here mirrors a Pydantic model in `backend/src/agent_app/api/schemas.py`.
 * When one changes, both change.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // A non-JSON error body is still an error; keep the status text.
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

// --- types -----------------------------------------------------------------

/**
 * `ready_to_submit` was removed on 2026-09-02: it described intent rather than
 * a state of the world, and "interested, not yet sent" already covered it.
 * The letter-is-written case it stood for is a filter, not a status.
 */
export const STATUSES = [
  "found",
  "not_relevant",
  "interested",
  "applied",
  "rejected",
  "interviewing",
  "offer",
  "declined",
] as const;

export type Status = (typeof STATUSES)[number];
/** A posting with no application row is untriaged, which is not a status. */
export type StatusOrUntriaged = Status | "untriaged";

export type Level = "intern" | "newgrad" | "unknown";
/** `manual` is not a board: it is a posting typed in by hand. */
export type Source =
  | "greenhouse"
  | "lever"
  | "ashby"
  | "personio"
  | "manual";

export const REGIONS = [
  "europe",
  "north_america",
  "south_america",
  "asia",
  "middle_east",
  "africa",
  "oceania",
] as const;

export type Region = (typeof REGIONS)[number];

/**
 * One resolved location on a posting. `raw` is always the board's own words,
 * so a place the parser could not resolve is still shown rather than missing.
 */
export interface Place {
  raw: string;
  city: string | null;
  country: string | null;
  region: string | null;
}

/** How to label a place: the most specific thing known about it. */
export function placeLabel(place: Place): string {
  if (place.city && place.country) return `${place.city}, ${place.country}`;
  return place.city ?? place.country ?? place.raw;
}

export interface PostingSummary {
  id: string;
  source: string;
  company: string;
  title: string;
  location: string | null;
  remote: boolean;
  url: string;
  posted_at: string | null;
  deadline: string | null;
  level: string;
  first_seen: string;
  last_seen: string;
  /** When the board stopped listing it. Null means still open. Never deleted. */
  closed_at: string | null;
  status: StatusOrUntriaged;
  places: Place[];
}

export interface StatusChange {
  from_status: string | null;
  to_status: string;
  note: string;
  changed_at: string;
}

export interface PostingDetail extends PostingSummary {
  body: string;
  note: string;
  letter_path: string | null;
  history: StatusChange[];
}

export interface PostingPage {
  items: PostingSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface RegionOption {
  id: string;
  name: string;
  count: number;
}

export interface CountryOption {
  code: string;
  name: string;
  region: string;
  count: number;
}

export interface FilterOptions {
  companies: string[];
  levels: string[];
  sources: string[];
  statuses: string[];
  /** Counted, so a country with two postings does not look like one with 900. */
  regions: RegionOption[];
  countries: CountryOption[];
}

export interface ApplicationState {
  posting_id: string;
  from_status: string | null;
  status: string;
  note: string;
  updated_at: string;
}

export interface Stats {
  total: number;
  by_status: Record<string, number>;
  by_company: { company: string; count: number; intern: number }[];
  by_source: Record<string, number>;
  by_level: Record<string, number>;
  recent: { date: string; count: number }[];
}

/**
 * A retrieval hit. `component_scores` values sum to `score` — that is what
 * makes the stacked bar in the trace honest rather than decorative.
 */
export interface SearchHit {
  chunk_id: number;
  posting_id: string | null;
  profile_doc: string | null;
  ordinal: number;
  text: string;
  score: number;
  rank: number;
  component_scores: Record<string, number>;
}

/**
 * One row of the agent's result list. A whole posting, not a chunk excerpt —
 * `find_postings` returns these so the list can be acted on rather than read.
 */
export interface FoundPosting {
  posting_id: string;
  company: string;
  title: string;
  location: string | null;
  remote: boolean;
  level: string;
  url: string;
  posted_at: string | null;
  deadline: string | null;
  source: string;
  rank: number;
  score: number;
  component_scores: Record<string, number>;
  excerpt: string;
  status: StatusOrUntriaged;
}

export interface BulkStatusResult {
  updated: ApplicationState[];
  failed: Record<string, string>;
}

export interface LetterResponse {
  posting_id: string;
  text: string;
  path: string;
  grounding: SearchHit[];
  todos: string[];
}

/**
 * One row of the email review queue.
 *
 * `posting_id` is null when the matcher declined to guess which application
 * an email is about — several open applications at one company is common, and
 * picking one would be a guess. `company` and `title` come from the join and
 * are null with it.
 */
export interface InboxSuggestion {
  id: number;
  message_id: string;
  posting_id: string | null;
  company_guess: string | null;
  sender: string;
  received_at: string | null;
  subject: string;
  snippet: string;
  classification: string | null;
  confidence: number | null;
  suggested_status: Status | null;
  applied: boolean;
  dismissed: boolean;
  created_at: string;
  company: string | null;
  title: string | null;
  url: string | null;
  current_status: string | null;
}

export interface InboxPage {
  items: InboxSuggestion[];
  pending: number;
}

export interface PostingQuery {
  q?: string;
  company?: string;
  level?: string;
  location?: string;
  remote?: boolean;
  source?: string;
  /** Any number of statuses, OR-ed. Empty or absent means no constraint. */
  status?: string[];
  posted_after?: string;
  /** ISO 3166-1 alpha-2. Matches if *any* of a posting's places match. */
  country?: string;
  region?: string;
  /** Closed postings are hidden by default: the grid is what you can apply to. */
  include_closed?: boolean;
  only_closed?: boolean;
  sort?: string;
  descending?: boolean;
  limit?: number;
  offset?: number;
}

/** Creating or editing a posting entered by hand. */
export interface ManualPostingBody {
  company: string;
  title: string;
  url: string;
  body?: string;
  location?: string | null;
  posted_at?: string | null;
  deadline?: string | null;
  level?: Level | null;
  remote?: boolean | null;
}

export interface ProfileSummary {
  slug: string;
  title: string;
  bytes: number;
  chunks: number;
  /** Chunking is immediate; embedding waits for `cli embed`. Both are shown so
   *  the person can tell keyword-searchable from meaning-searchable. */
  embedded: number;
  ingested: boolean;
}

export interface ProfileDoc extends ProfileSummary {
  text: string;
}

export interface ProfileList {
  documents: ProfileSummary[];
  pending_embedding: number;
}

export interface SyncStatus {
  status: "idle" | "running" | "done" | "error";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  report: {
    since: string | null;
    found: number;
    already_seen: number;
    fetched: number;
    matched: number;
    unmatched: number;
    suggestions: number;
    by_label: Record<string, number>;
    skipped_reason: string | null;
  } | null;
  /** Whether Gmail has been connected at all. Without it, "nothing happened"
   *  has a setup answer rather than being a failure. */
  authorised: boolean;
}

// --- calls -----------------------------------------------------------------

function toQueryString(query: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    // An array becomes a repeated key — ?status=applied&status=interviewing —
    // which is how FastAPI reads a list-valued query parameter.
    if (Array.isArray(value)) {
      for (const item of value)
        if (item !== undefined && item !== "") params.append(key, String(item));
    } else if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export function getPostings(query: PostingQuery = {}): Promise<PostingPage> {
  return request<PostingPage>(`/api/postings${toQueryString(query)}`);
}

export function getPosting(id: string): Promise<PostingDetail> {
  return request<PostingDetail>(`/api/postings/${encodeURIComponent(id)}`);
}

export function getFilters(): Promise<FilterOptions> {
  return request<FilterOptions>("/api/filters");
}

export function getStats(): Promise<Stats> {
  return request<Stats>("/api/stats");
}

/** Put a posting back in the pool with no status at all, so a later search
 *  can surface it again. Not the same as any status: untriaged is the absence
 *  of an application row. */
export function clearStatus(id: string): Promise<ApplicationState> {
  return request<ApplicationState>(
    `/api/applications/${encodeURIComponent(id)}`,
    {
      method: "DELETE",
    },
  );
}

export function setStatus(
  id: string,
  status: Status,
  note = "",
): Promise<ApplicationState> {
  return request<ApplicationState>(
    `/api/applications/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ status, note }),
    },
  );
}

/** Set one status on many postings in a single request. */
export function setStatusBulk(
  postingIds: string[],
  status: Status,
  note = "",
): Promise<BulkStatusResult> {
  return request<BulkStatusResult>("/api/applications", {
    method: "PATCH",
    body: JSON.stringify({ posting_ids: postingIds, status, note }),
  });
}

export function draftLetter(id: string): Promise<LetterResponse> {
  return request<LetterResponse>(`/api/letters/${encodeURIComponent(id)}`, {
    method: "POST",
  });
}

/**
 * Apply one instruction to an existing draft.
 *
 * `letter` is the editor's current contents rather than what is on disk: the
 * person may have edited the draft by hand before asking for a change, and
 * revising the saved copy would throw that away without saying so.
 */
export function reviseLetter(
  id: string,
  instruction: string,
  letter: string,
): Promise<LetterResponse> {
  return request<LetterResponse>(
    `/api/letters/${encodeURIComponent(id)}/revise`,
    { method: "POST", body: JSON.stringify({ instruction, letter }) },
  );
}

// --- postings you enter yourself -------------------------------------------

export function createPosting(
  body: ManualPostingBody,
): Promise<PostingDetail> {
  return request<PostingDetail>("/api/postings", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updatePosting(
  id: string,
  body: ManualPostingBody,
): Promise<PostingDetail> {
  return request<PostingDetail>(`/api/postings/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deletePosting(id: string): Promise<void> {
  const response = await fetch(
    `${BASE_URL}/api/postings/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
  // 204, so there is no body to parse — `request` would choke on it.
  if (!response.ok) throw new ApiError(response.status, response.statusText);
}

// --- the profile corpus ----------------------------------------------------

export function getProfile(): Promise<ProfileList> {
  return request<ProfileList>("/api/profile");
}

export function getProfileDoc(slug: string): Promise<ProfileDoc> {
  return request<ProfileDoc>(`/api/profile/${encodeURIComponent(slug)}`);
}

/** Saving rewrites the file and re-chunks it in the same request, so the
 *  letters can never be grounded in text that has since been edited. */
export function saveProfileDoc(
  slug: string,
  text: string,
): Promise<ProfileDoc> {
  return request<ProfileDoc>(`/api/profile/${encodeURIComponent(slug)}`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });
}

export async function deleteProfileDoc(slug: string): Promise<void> {
  const response = await fetch(
    `${BASE_URL}/api/profile/${encodeURIComponent(slug)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new ApiError(response.status, response.statusText);
}

// --- the mailbox sync ------------------------------------------------------

export function getSyncStatus(): Promise<SyncStatus> {
  return request<SyncStatus>("/api/inbox/sync");
}

/** Start a sync. Returns immediately with 202 — poll `getSyncStatus` for the
 *  rest. Still only ever writes suggestions; accepting one stays separate. */
export function startSync(includeSent = false): Promise<SyncStatus> {
  return request<SyncStatus>("/api/inbox/sync", {
    method: "POST",
    body: JSON.stringify({ include_sent: includeSent }),
  });
}

export interface InboxFilters {
  pendingOnly?: boolean;
  /** rejection | interview | offer | other */
  classification?: string;
  /** 0 to 1. Drops suggestions the classifier was unsure about. */
  minConfidence?: number;
}

export function getInbox(filters: InboxFilters = {}): Promise<InboxPage> {
  const query = toQueryString({
    pending_only: filters.pendingOnly ?? true,
    classification: filters.classification || undefined,
    min_confidence: filters.minConfidence || undefined,
  });
  return request<InboxPage>(`/api/inbox${query}`);
}

/**
 * Apply one suggestion. Both arguments are overrides: `postingId` attaches an
 * unmatched email to a posting the user picked, and `status` overrides what
 * the classifier suggested.
 */
export function acceptSuggestion(
  id: number,
  postingId?: string,
  status?: Status,
): Promise<ApplicationState> {
  return request<ApplicationState>(`/api/inbox/${id}/accept`, {
    method: "POST",
    body: JSON.stringify({
      posting_id: postingId ?? null,
      status: status ?? null,
    }),
  });
}

export function dismissSuggestion(id: number): Promise<InboxSuggestion> {
  return request<InboxSuggestion>(`/api/inbox/${id}/dismiss`, {
    method: "POST",
  });
}

// --- the agent stream ------------------------------------------------------

export interface ToolCallEvent {
  kind: "tool_call";
  name: string;
  input: Record<string, unknown>;
}
export interface ToolResultEvent {
  kind: "tool_result";
  name: string;
  output: unknown;
  ms: number;
}
export interface TextEvent {
  kind: "text";
  delta: string;
}
export interface DoneEvent {
  kind: "done";
  iters: number;
  text: string;
  /** The full message thread including this turn. Pass it back as `history`
   *  on the next call so follow-up questions have something to refer to. */
  history: unknown[];
}
export interface ErrorEvent {
  kind: "error";
  detail: string;
  status: number;
}

export type AgentEvent =
  ToolCallEvent | ToolResultEvent | TextEvent | DoneEvent | ErrorEvent;

/**
 * POST a chat turn and yield Server-Sent Events as they arrive.
 *
 * `EventSource` cannot be used because it only issues GET requests and this
 * endpoint needs a JSON body, so the SSE framing is parsed by hand off the
 * fetch stream. Events are separated by a blank line.
 */
export async function* streamChat(
  message: string,
  history: unknown[] = [],
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(`${BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
    signal,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Keep the status text.
    }
    yield { kind: "error", detail, status: response.status };
    return;
  }

  if (!response.body) {
    yield { kind: "error", detail: "The response had no body", status: 500 };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const parsed = parseFrame(frame);
      if (parsed) yield parsed;
      split = buffer.indexOf("\n\n");
    }
  }
}

function parseFrame(frame: string): AgentEvent | null {
  let event = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!event || !data) return null;
  try {
    return { kind: event, ...JSON.parse(data) } as AgentEvent;
  } catch {
    return null;
  }
}
