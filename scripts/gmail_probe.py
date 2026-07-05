"""Manual M1 spike: authenticate, run the filtered query, dump raw emails.

Run after completing the /gmail-setup skill. Prints enough of each message to
eyeball whether extraction will have what it needs, without running any LLM.
"""

from __future__ import annotations

from applysync.config import get_settings, get_sources
from applysync.gmail.client import GmailClient
from applysync.gmail.query_builder import build_search_query


def main() -> None:
    settings = get_settings()
    sources = get_sources()

    query = build_search_query(sources)
    print(f"Gmail query:\n  {query}\n")

    client = GmailClient(settings)
    emails = client.fetch_messages(query, max_results=10)
    print(f"Fetched {len(emails)} message(s)\n")

    for email in emails:
        print("=" * 80)
        print(f"From:    {email.sender}")
        print(f"Subject: {email.subject}")
        print(f"Date:    {email.date}")
        print(f"Body (first 500 chars):\n{email.body[:500]}")


if __name__ == "__main__":
    main()
