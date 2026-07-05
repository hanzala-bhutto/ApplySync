---
name: gmail-setup
description: Walk through the one-time Gmail API OAuth setup for this project (Google Cloud Console project, consent screen, credentials.json, first-run consent, token caching). Use when the user needs to set up or re-set-up Gmail access, or hits an OAuth/auth error running the tracker.
---

# Gmail OAuth setup walkthrough

This is a fiddly manual step done once per machine and easy to forget the
details of between sessions. Walk the user through it interactively; don't
just dump a script, since several steps happen in the Google Cloud Console UI
outside any tool's control.

## What this is NOT

The `mcp__claude_ai_Gmail__*` tools available in Claude Code sessions are
unrelated to this: those authenticate *this assistant's* access for its own
use, not the shipped application. The tracker app needs its own standalone
OAuth client, independent of any Claude Code session.

## Steps

1. **Google Cloud Console project**: create (or reuse) a project at
   console.cloud.google.com. Enable the "Gmail API" under APIs & Services.
2. **OAuth consent screen**: configure as "External" (unless the user has a
   Google Workspace org to restrict to "Internal"), fill minimal required
   fields (app name, support email). Since this is a personal/local tool, it
   stays in "Testing" publishing status, add the user's own Google account
   under "Test users" so it doesn't need Google's app-verification review.
3. **OAuth client credentials**: under Credentials, create an OAuth client ID
   of type "Desktop app" (this matches the installed-app flow the client code
   uses, not "Web application"). Download the JSON; this is `credentials.json`.
4. **Place the file**: put `credentials.json` at the path the project's
   `.env` points `GMAIL_CLIENT_SECRETS_PATH` to. It must NOT be committed,
   confirm it's covered by `.gitignore`.
5. **Scope**: confirm the code requests only
   `https://www.googleapis.com/auth/gmail.readonly`. If a consent screen ever
   asks for broader access than that, stop and check the code, that's a
   regression against the hard "readonly only" constraint in `CLAUDE.md`.
6. **First run**: running the app's Gmail-connecting command (e.g.
   `applysync sync` or `scripts/gmail_probe.py`) for the first time opens a
   browser consent prompt. After granting, a `token.json` is cached (path per
   `.env`/config) so subsequent runs don't re-prompt.
7. **Token refresh**: the client library refreshes automatically using the
   cached refresh token. If auth errors resurface later, the usual fix is
   deleting the cached `token.json` and re-running the first-run consent,
   not regenerating `credentials.json` (that's only needed if the OAuth client
   itself was deleted/rotated in Cloud Console).

## Troubleshooting

- **"Access blocked: app not verified"**: the user's own Google account isn't
  listed under Test users on the consent screen; add it there.
- **invalid_grant / token expired**: delete cached `token.json`, re-run to
  re-consent.
- **403 insufficient scope**: scope mismatch between what's cached in
  `token.json` and what the code now requests; delete `token.json` and
  re-auth after any scope change.
