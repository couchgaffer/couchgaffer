# Security

## Reporting a vulnerability

Report it privately through this repository's **Security** tab, using **Report a vulnerability**. Please do not open a public issue. Include what you found, how to reproduce it, and what it lets someone do. We aim to acknowledge a report within a week.

Only the latest release on `main` is supported.

## What the design protects

The public demo has no sign-in and asks for no personal data. All clubs, players, managers and matches are invented.

**No keys.** The app and the recap job reach Azure with managed identities. Storage has shared keys off, the search service accepts Microsoft Entra ID only, and the container registry has its admin user off ([`infra/main.bicep`](infra/main.bicep)). The Foundry account, which this template references rather than creates, has local auth off ([`infra/README.md`](infra/README.md)).

**Session secrets.** Each match has a random phone token, compared in constant time ([`app/main.py`](backend/watchparty/app/main.py)). Page links carry it in the URL fragment, so it stays out of page requests and Referer headers ([`frontend/src/lib/api.ts`](frontend/src/lib/api.ts)). The WebSocket sends it in its query string, which the app masks in its own logs and leaves out of traces.

**Limits against abuse.** Request bodies over 4 KB are refused. Each address has a cap on matches at once and per hour, and on matches that use the AI agents; past an hourly cap on agent matches across everyone, new matches run on code alone. Each match has a cap on screens. Screens only listen: a screen that sends anything is closed, and one that stops reading is dropped ([`app/main.py`](backend/watchparty/app/main.py), [`app/session.py`](backend/watchparty/app/session.py)).

**Model output.** Every number a model writes must match cited evidence with its sign and unit, every capitalised name must belong to the match, and betting and money words are refused. A failed answer gets one retry and then a plain line written by code ([`agents/checks.py`](backend/watchparty/agents/checks.py)). The match tools are reachable only from inside the app unless `MCP_PUBLIC=1`, which the deployment does not set ([`app/main.py`](backend/watchparty/app/main.py)).

**Browser.** Responses carry a Content Security Policy with `frame-ancestors 'none'`, and nosniff, no-referrer, HSTS and Permissions-Policy headers ([`app/main.py`](backend/watchparty/app/main.py)).

**Telemetry.** When traces are on, they carry ids, timings, request paths and status, model token counts and error types (for an unhandled API error, also its message, which our code writes). They carry no prompts, model answers, display names, phone tokens, query strings, client addresses or user agents ([`telemetry.py`](backend/watchparty/telemetry.py)).

Security fixes made during the build each carry a regression test.
