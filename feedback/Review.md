# FinAlly specification review

## Prioritized summary

1. **Resolve correctness and safety rules before implementation.** The plan leaves trade atomicity, numeric validation, price availability, LLM action authorization, action failures, and duplicate-request handling undefined. These can produce incorrect cash/positions or trades the user did not clearly intend.
2. **Define the runtime contract for the completed market-data subsystem.** MARKET_DATA_SUMMARY.md describes a concrete cache/source/SSE implementation, but the plan does not specify its lifecycle, watchlist synchronization, startup prices, stale-data behavior, or how trade and portfolio code consume it.
3. **Make persistence and concurrency explicit.** SQLite initialization is described as lazy with no migrations, yet the app has background writers, HTTP trade/chat requests, SSE clients, and potentially multiple workers. Transaction boundaries, locking, cleanup, and schema evolution need decisions.
4. **Specify the API and UI data contracts.** Endpoints lack response schemas, error semantics, pagination/retention limits, ticker validation, and chart history definitions. Several UI metrics (“daily change,” “P&L,” “portfolio weight”) have no defined calculation.
5. **Constrain the deployment claim.** Static export + FastAPI + SQLite is feasible for a local single-process demo. The optional cloud deployment claim is not generally compatible with a local SQLite volume and in-memory market cache without platform-specific persistence and process constraints.
6. **Clarify LLM provider/structured-output behavior.** The OpenRouter model/provider routing and the named cerebras-inference skill are asserted rather than specified; provider support, fallback behavior, schema enforcement, prompt injection handling, and latency/error handling are absent.

## Findings

### P0 — Define transaction and validation invariants for trades

**References:** PLAN §2 “What the User Can Do” (lines 22–31); §7 positions/trades schema (lines 210–226); §8 portfolio trade endpoint (lines 256–261); §9 Auto-Execution (lines 321–328).

**Issue:** The plan says market orders fill instantly and checks sufficient cash/shares, but does not define the full invariant set or atomicity. Missing cases include zero/negative/NaN/infinite quantities, invalid sides/tickers, zero or stale prices, fractional precision, rounding, concurrent buys against the same cash balance, concurrent sells, and whether a failed multi-action chat partially commits. The trades table is called append-only, while positions and cash are mutable; no transaction boundary links those writes. A crash after changing cash but before recording a trade would corrupt state.

**Recommendation:** Specify a single transactional trade operation: validate normalized ticker, finite positive quantity and finite positive executable price; read cash/position and price; perform a conditional update and append trade atomically; rollback all changes on failure. Define money precision/rounding (prefer integer minor units or a documented decimal strategy), fractional-share precision, whether shorting/negative cash are prohibited, and status/error codes. Decide whether each LLM action is an independent transaction or the entire action batch is atomic, and return per-action outcomes.

### P0 — Clarify what “execute when asked or agrees” means for LLM actions

**References:** PLAN §2 chat capability (lines 22–31); §9 workflow steps 5–8 (lines 290–299); structured schema and Auto-Execution (lines 301–328); system prompt guidance (lines 330–338).

**Issue:** The schema permits the model to return executable trades and watchlist changes on any response. “Execute trades when the user asks or agrees” is underspecified, while the workflow auto-executes all returned trades without checking that the latest user message authorized them. It is unclear whether suggestions, hypothetical analysis, quoted text, or prompt-injection content can trigger actions. A malformed or overlong response can also contain many actions. The endpoint’s request identity/retry behavior is unspecified, so a browser/network retry could execute a trade twice.

**Recommendation:** Treat model output as an untrusted proposal. Define an explicit action policy tied to user intent (e.g. require explicit action language, or present a confirmation step), enforce server-side caps and validation, and never infer authorization from the model’s explanation alone. Add a request/idempotency key and persist action outcomes before returning. Define handling for refusals, invalid structured output, provider errors, and partial action failures. Since this is explicitly a fake-money local demo, confirmation can remain optional if the authorization rules are explicit and tested.

### P0 — Reconcile market-data summary with the plan’s integration contract

**References:** PLAN §4 Key Boundaries (lines 109–117); §6 (lines 144–180); §7 default watchlist (lines 242–245); §8 market/watchlist/portfolio endpoints (lines 251–278). MARKET_DATA_SUMMARY “Architecture” and “Modules” (lines 9–35), “Usage” (lines 83–103).

**Issue:** The high-level architecture is consistent: both documents describe a simulator/Massive source, a shared cache, SSE, and dynamic ticker operations. But the summary’s actual interface requires start(tickers), add_ticker, remove_ticker, and stop, and its stream router is a factory. The plan does not say who initializes these at application lifespan, how watchlist mutations invoke the source, whether removing a watched ticker is allowed when the user owns it, or how source/cache/DB state are reconciled after restart. It also says all known tickers are streamed, while the summary describes a cache with updates; initial seed prices and initial SSE payload behavior are not defined. Consumers may receive no price until the first source tick, making first-launch portfolio valuation/trading undefined.

**Recommendation:** Add a concrete integration contract: initialize DB, cache, source, and router once in FastAPI lifespan; seed source tickers from persisted watchlist plus held positions; update source membership on watchlist/position changes; retain a ticker while a position exists; specify initial snapshot and heartbeat/reconnect behavior; and define fallback behavior when a ticker has no cache entry. Document whether the summary subsystem is already merged/current and identify the package/API version consumed by the remaining backend work.

### P1 — Define source polling, rate-limit accounting, and stale-price behavior

**References:** PLAN §6 Massive API (lines 159–165) and Shared Price Cache (lines 167–172). MARKET_DATA_SUMMARY “Architecture” (lines 12–21), “Key Design Decisions” (lines 37–43), “MassiveDataSource” (line 33).

**Issue:** “Free tier (5 calls/min): poll every 15 seconds” may or may not comply depending on whether one request fetches all tickers or each ticker requires a separate request. The plan does not specify batching, rate-limit handling, retries/backoff, market-closed behavior, API errors, or when cached data becomes stale. “Live” can therefore imply freshness that the provider/tier does not guarantee. The summary gives simulator shock and correlation details but does not define how those parameters relate to the plan’s “configurable” per-ticker settings.

**Recommendation:** Specify the actual provider endpoint and request unit, batch size, quota calculation, poll schedule, timeout/retry/backoff policy, stale threshold, and UI/API stale indicator. Define whether market-hours data is delayed, and distinguish simulated prices from real quotes visibly. Put simulator cadence, seed, drift/volatility units, correlation groups, and shock distribution in one authoritative contract; avoid implying simulation is a market forecast.

### P1 — Define SQLite transaction, initialization, and migration strategy

**References:** PLAN §3 SQLite rationale (lines 72–81); §4 backend/db and lazy initialization (lines 109–116); §7 initialization/schema (lines 184–240); §11 container/runtime volume (lines 374–402). MARKET_DATA_SUMMARY “PriceCache (thread-safe)” (lines 17–18).

**Issue:** “If the file doesn’t exist or tables are missing, create schema and seed” can reseed or partially repair an existing database in ambiguous ways. No schema version, migration policy, foreign keys, indexes, busy timeout, WAL setting, backup/reset semantics, or concurrent writer policy is stated. The app has portfolio snapshot background writes plus trades and chat requests, and SQLite connections are thread/process-sensitive. The plan’s “future multi-user without schema migration” claim is not supported by hardcoded "default" identity and does not make this a multi-user design.

**Recommendation:** Use explicit versioned migrations, seed only on a truly fresh schema, and fail clearly on an incompatible/corrupt DB. Define connection lifecycle, transaction scope, busy timeout/journal mode, foreign-key constraints/indexes, and one-process/one-worker assumptions. Document backup/reset and persistence guarantees. Either remove the future multi-user claim or enumerate the additional migration and isolation work that would be required.

### P1 — Define valuation and performance calculations

**References:** PLAN §2 portfolio views (lines 28–29); §7 positions and snapshots (lines 210–232); §8 portfolio/history endpoints (lines 256–261); §10 widgets (lines 355–362).

**Issue:** “Total value,” “unrealized P&L,” “% change,” heatmap weight, daily change, and the P&L chart are not mathematically defined. The schema only records total value every 30 seconds and after a trade, but has no initial snapshot or realized P&L/portfolio baseline. Current holdings alone do not explain total return after sales. “P&L chart” could mean total account value, gain from initial $10,000, or day return. Selling a position and having no position rows also affects heatmap semantics.

**Recommendation:** Define formulas and baselines: cash + market value; unrealized P&L = market value − remaining cost basis; specify average-cost update on buys and cost-basis removal on sells; distinguish realized P&L; define portfolio return against initial equity; define weight denominator and zero/negative handling. Persist an initial snapshot and specify snapshot retention/aggregation and chart time range. Define whether “daily change” uses provider previous close or session data; simulator should expose a separate simulated change metric.

### P1 — Specify REST and SSE schemas and error semantics

**References:** PLAN §8 all endpoint tables (lines 249–278); §6 SSE event content/reconnect (lines 174–180); §10 same-origin calls (lines 364–370).

**Issue:** Endpoint descriptions are not sufficient contracts for independent agents. Request/response fields, numeric types, timestamp format/timezone, status codes, validation errors, ticker normalization, pagination, and ordering are absent. The SSE event says what it contains but not event names, JSON framing, initial snapshot, heartbeat comments, event IDs/Last-Event-ID behavior, or whether updates are coalesced. Native EventSource reconnects, but missed data recovery is not automatic without replay or a fresh snapshot. Long-lived connections may also need proxy buffering disabled and graceful shutdown handling.

**Recommendation:** Define Pydantic request/response models and a shared error envelope with examples; set ticker syntax/case policy and watchlist limits; define history filters/limits and response ordering. Specify SSE media type, event name/data schema, initial snapshot, heartbeat interval, cache version/event ID and reconnect recovery, keepalive/proxy requirements, and client behavior on stale/disconnected streams.

### P1 — Make chart requirements achievable from defined data

**References:** PLAN §2 sparklines/chart (lines 24–26); §6 cache/SSE (lines 167–180); §10 main chart and P&L chart (lines 355–359).

**Issue:** The main ticker chart is described as price over time, but the backend only retains the latest and previous price in memory; the browser sparkline starts at page load. Reload loses chart history, and no endpoint exists for ticker history. Massive polling cadence may provide only sparse points, while simulator SSE cadence creates unbounded frontend arrays if retained indefinitely.

**Recommendation:** Decide whether charts are session-only or durable. If session-only, state the bounded point window/downsampling policy and label it as since-connect. If durable, add a price-bar/history storage model and endpoint, including provider granularity, retention, and missing-market periods. Specify maximum client history and chart timezone.

### P1 — Define static export routing and FastAPI file-serving behavior

**References:** PLAN §3 static Next.js export (lines 48–70); §4 frontend/backend boundaries (lines 109–117); §11 multi-stage build (lines 374–392).

**Issue:** Static export can be served from FastAPI, but the plan does not define the export directory layout, asset path/basePath, root index routing, 404 behavior, or deep-link fallback. Next.js features requiring a server (SSR, API routes, image optimization) are incompatible with a pure export and need to be prohibited. The blanket /* static route could capture API/health paths if route precedence is not explicit.

**Recommendation:** Specify static export constraints and output path, serve /api routes before the static mount, map / and supported client-side paths to exported files, return real 404s for unknown assets, and test asset URLs under the chosen base path. Pin Node/Python versions and use reproducible lockfile installs (npm ci rather than floating npm install).

### P1 — Bound and harden chat history and prompt construction

**References:** PLAN §7 chat_messages (lines 234–240); §9 workflow/context (lines 288–299), system prompt (lines 330–338), mock mode (lines 340–345).

**Issue:** “Recent conversation history” has no limit/token budget or retention policy. There is no timeout, retry, rate limit, cancellation, provider outage response, or protection against user content in history being treated as instructions. Storing the full conversation and actions indefinitely can grow the SQLite file. It is also unclear whether user messages are persisted when LLM calls fail and what the mock response contract is.

**Recommendation:** Specify bounded history (by turns and/or token budget), input length limits, database retention/reset, model timeout and retry policy, user-facing provider errors, and request concurrency limits. Treat all prior/user content as untrusted data, keep system instructions separate, and test prompt injection and malformed outputs. Define persistence ordering for user/assistant messages and failed requests; make mock mode exercise the same schema and action-validation path.

### P1 — Narrow the cloud deployment promise or define its constraints

**References:** PLAN §3 single-container and in-memory cache (lines 48–70); §6 cache and future multi-user statement (lines 167–172); §11 optional cloud deployment (lines 420–422).

**Issue:** The design is suitable for a local, single-process container. On container platforms, ephemeral filesystem storage can lose SQLite data; multiple replicas each have separate SQLite files and in-memory price caches; multiple workers can start duplicate simulators/pollers and produce inconsistent data. App Runner/Render behavior, persistent disk availability, and scale-to-zero differ. Therefore “designed to deploy to ... any container platform” overstates portability.

**Recommendation:** Scope the guarantee to one replica, one worker, persistent writable disk, and no scale-to-zero, or document platform-specific persistent storage and process settings. For horizontal scaling, move state/cache to shared services and run one market-data producer. Describe cloud deployment as a stretch goal contingent on those constraints.

### P2 — Fix environment-variable and secrets behavior

**References:** PLAN §5 (lines 121–140); §9 key statement (lines 282–286); §11 Docker run (lines 394–402).

**Issue:** OPENROUTER_API_KEY is labeled required even though mock mode says development works without a key. The plan says the backend reads root .env from a mounted file or Docker --env-file, but the shown runtime command passes --env-file and does not mount it; those are alternative mechanisms that should be stated clearly. A key in a committed example or baked into an image would be a secret leak risk. The Massive provider is called Massive and Polygon.io interchangeably without specifying the client/API contract.

**Recommendation:** Mark the key required only for live LLM mode; state precedence/defaults and startup validation. Pass secrets through environment injection, never COPY them into images, and avoid mounting the whole project. Define exact environment variable names and provider/package terminology, and ensure logs/errors never expose credentials.

### P2 — Reconcile UX and requirement ambiguity

**References:** PLAN §1 “live market data” (line 7); §2 first launch and controls (lines 13–31); §6 simulator and Massive behavior (lines 150–180); §10 UI (lines 349–370).

**Issue:** The vision says “live market data,” while the default is synthetic simulation and optional real data may be delayed or rate-limited. “AI chat panel ready” conflicts with requiring an OpenRouter key for chat unless the UI defines unavailable/mock states. “No confirmation dialog” is explicit for manual trades but AI action behavior has separate unclear wording. Tablet functionality is subjective and not acceptance-testable.

**Recommendation:** Label data source and freshness in the UI, define a no-key experience, and state precisely which actions require confirmation. Convert visual goals into acceptance criteria such as supported viewport widths, keyboard behavior, accessible status labels, and named screen states.

### P2 — Define watchlist/ticker lifecycle and input policy

**References:** PLAN §2 watchlist management (lines 30–31); §6 union of watched tickers (lines 161–165, 178); §7 watchlist schema (lines 203–208); §8 watchlist endpoints (lines 263–268).

**Issue:** The system does not say whether a ticker must be supported by Massive, whether adding a ticker before its first price succeeds, whether duplicate add is idempotent, what happens when deleting an owned holding, or whether a maximum watchlist size exists. Case normalization and provider symbol format are missing.

**Recommendation:** Define ticker validation and provider lookup semantics, maximum count, duplicate behavior, unknown-symbol errors, and deletion behavior when a holding exists. Make add/remove idempotent or specify conflict codes, and keep source subscriptions synchronized with persisted state.

### P2 — Add lifecycle, resource, and health requirements

**References:** PLAN §3 background polling/simulation (lines 48–70); §6 SSE (lines 167–180); §7 snapshot background task (line 228); §8 /api/health (lines 275–278); §11 Docker (lines 374–418).

**Issue:** Background jobs have no startup/shutdown or failure supervision requirements. Each snapshot every 30 seconds grows without bound. The health endpoint has no liveness/readiness distinction and could report healthy while the provider/DB is unusable. There are no limits for SSE client count, request body size, or graceful container stop.

**Recommendation:** Define FastAPI lifespan ownership and cancellation for all tasks, task restart/error logging policy, snapshot retention/compaction, and graceful shutdown timeout. Define liveness versus readiness checks, including DB initialization and market-data degradation; set connection/request limits appropriate to the demo.

### P2 — Make testing strategy measurable and cover critical integration paths

**References:** PLAN §12 (lines 426–456); MARKET_DATA_SUMMARY test and coverage claims (lines 45–58).

**Issue:** The plan lists broad test topics but few pass/fail criteria. It does not cover transactional rollback, concurrent trades, startup/restart persistence, missing/stale prices, provider rate-limit behavior, failed LLM calls, action authorization/idempotency, or static deep-link serving. The market summary’s 84% coverage is useful but does not establish these platform-level integration behaviors; Massive coverage is 56% by design.

**Recommendation:** Add acceptance tests for the P0 invariants, source/watchlist lifecycle, fresh and existing DB behavior, restart persistence, SSE initial/reconnect flow, Docker health/static routes, and provider failure modes. Treat the summary’s market subsystem suite as an existing component-level baseline and add integration tests around its boundaries rather than duplicating its unit tests.

## Cross-document consistency assessment

The architecture and names are broadly aligned: both documents use MASSIVE_API_KEY to choose between a GBM simulator and Massive REST polling, a shared in-memory cache, SSE at /api/stream/prices, and a common source abstraction. The summary makes several plan-level claims concrete: PriceCache is thread-safe and versioned, the simulator uses Cholesky-correlated moves, and the source supports dynamic ticker add/remove.

The key inconsistency is not a direct contradiction but a missing integration layer: the summary gives operational APIs and implementation boundaries that the plan does not incorporate into FastAPI lifespan, watchlist changes, portfolio valuation, trade execution, or startup/reconnect behavior. The plan also describes source polling intervals and “all tickers known” without stating how those map to the summary’s actual provider requests/cache versions. Add a short “market subsystem integration” contract to PLAN or link to the summary as normative for those details.

## Open questions for the plan

1. Is the price source in the default demo explicitly simulated, and how is the source/freshness shown to users?
2. Does a trade use the most recent cached price even when stale, block until a fresh price, or fail with a stale-price error?
3. What are exact trade invariants, rounding rules, and transaction/idempotency semantics?
4. Can LLM-proposed actions execute without confirmation, and what precise server-side evidence of user intent is required?
5. Are action batches atomic or independently committed? How are partial failures represented and persisted?
6. Should deleting a watchlist ticker be allowed while it remains in positions?
7. Are charts session-only or durable? What history window, granularity, retention, and timezone do they use?
8. What does “P&L” mean on the chart and heatmap, and how are realized gains and return since initial capital represented?
9. What SQLite process model is supported (one worker/replica only), and what migration/backup/reset behavior is required?
10. Which Massive endpoints and package version are used, and how are request quotas counted for a multi-ticker poll?
11. What exact OpenRouter model/provider configuration is supported, and what happens when structured output mode is unsupported or the provider is unavailable?
12. What are the REST response/error schemas and SSE snapshot, heartbeat, replay, and stale-data contracts?
13. Which Next.js features are prohibited by static export, and how are deep links and missing static assets routed?
14. Is cloud deployment actually in scope, and if so, which persistent-storage and single-process constraints are guaranteed?
