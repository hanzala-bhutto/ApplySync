from __future__ import annotations

import json
import uuid

from sqlmodel import Session, select

from applysync.config import Settings, SourcesConfig, get_settings, get_sources
from applysync.db import repository as repo
from applysync.db.init_db import get_engine, init_db
from applysync.db.models import Application, ProcessedEmail
from applysync.gmail.client import GmailClient
from applysync.gmail.models import RawEmail
from applysync.gmail.query_builder import guess_platform
from applysync.llm import get_chat_model
from applysync.pipeline.nodes import make_classify_and_extract_node, make_scrutinize_relevance_node
from applysync.pipeline.state import JobApplicationEvent


def _application_differs(application, event, extracted: JobApplicationEvent) -> bool:
    """Compares against the specific status event THIS email originally
    created (event.status), not application.current_status. current_status
    reflects the most recent event across the application's whole history -
    for an application with more than one transition (e.g. applied ->
    interview -> rejected), comparing against it would make re-scanning the
    original "applied" email always look like a disagreement, even though
    nothing is actually wrong with either the old or new extraction.
    """
    return (
        application.company_name != extracted.company_name
        or application.job_title != extracted.job_title
        or event.status != extracted.status
    )


def _extract_payload(extracted: JobApplicationEvent, platform_hint: str | None) -> str:
    """JobApplicationEvent has no platform field (platform_hint is computed
    separately from the sender), so it's merged into the stored JSON here
    rather than added to the schema just for this snapshot."""
    return json.dumps({**extracted.model_dump(), "platform": platform_hint or "other"})


def _previous_payload(application, event) -> str:
    """Snapshot of what this specific email originally recorded, for diff
    display only - deliberately NOT built via JobApplicationEvent, since
    that schema's status Literal excludes "declined" (manual-only, the LLM
    should never produce it - see CLAUDE.md), but a real application can
    legitimately hold that status. Validating an arbitrary stored value
    against an LLM-output schema is the wrong tool here. Status comes from
    the event (this email's own original record), not application.current_status
    - see _application_differs for why that distinction matters.
    """
    return json.dumps(
        {
            "company_name": application.company_name,
            "job_title": application.job_title,
            "status": event.status,
            "platform": application.platform,
        }
    )


def process_full_scan(
    emails: list[RawEmail],
    *,
    model,
    session: Session,
    sources: SourcesConfig,
    run_id: str,
) -> dict:
    """Core, unit-testable full-scan logic: given already-fetched emails
    (real production code refetches every ever-processed message id, see
    full_scan() below), re-runs scrutiny + classify-and-extract on each and
    queues a ReviewSuggestion wherever the new result disagrees with what's
    on record. Never writes to Application/StatusEvent directly.
    """
    scrutinize = make_scrutinize_relevance_node(model, sources)
    classify_and_extract = make_classify_and_extract_node(model, sources)

    repo.update_pipeline_run_progress(session, run_id, emails_total=len(emails))

    emails_scrutinized = 0
    emails_extracted = 0
    emails_relevant = 0
    emails_written = 0
    suggestions_created = 0

    for email in emails:
        processed = session.get(ProcessedEmail, email.message_id)
        old_event = repo.find_status_event_by_source_email(session, email.message_id)
        old_application = session.get(Application, old_event.application_id) if old_event is not None else None

        scrutiny_result = scrutinize({"email": email})
        emails_scrutinized += 1

        new_extracted = None
        platform_hint = None
        if scrutiny_result.get("scrutiny") == "pass":
            extract_result = classify_and_extract({"email": email})
            emails_extracted += 1
            new_extracted = extract_result.get("extracted")
            platform_hint = extract_result.get("platform_hint")
            if new_extracted is not None:
                emails_relevant += 1
        else:
            platform_hint = guess_platform(email.sender, sources)

        # A pending suggestion for this email already exists - from an
        # earlier full-scan run, or a crashed run that got this far before
        # failing (suggestions are committed per-email, not all-or-nothing).
        # Skip creating a duplicate rather than re-flagging the same thing
        # every time a scan runs.
        already_pending = repo.has_pending_suggestion_for_message(session, email.message_id)

        if not already_pending and old_application is not None:
            if new_extracted is None:
                repo.create_review_suggestion(
                    session,
                    message_id=email.message_id,
                    application_id=old_application.id,
                    action="reclassify_irrelevant",
                    previous_classification=processed.classification,
                    suggested_classification="irrelevant",
                    pipeline_run_id=run_id,
                )
                suggestions_created += 1
            elif _application_differs(old_application, old_event, new_extracted):
                repo.create_review_suggestion(
                    session,
                    message_id=email.message_id,
                    application_id=old_application.id,
                    action="update_existing",
                    previous_classification=processed.classification,
                    suggested_classification="relevant",
                    previous_extract_json=_previous_payload(old_application, old_event),
                    suggested_extract_json=_extract_payload(new_extracted, platform_hint),
                    pipeline_run_id=run_id,
                )
                suggestions_created += 1
        elif not already_pending and new_extracted is not None:
            match = repo.find_matching_application(
                session, new_extracted.company_name, new_extracted.job_title
            )
            repo.create_review_suggestion(
                session,
                message_id=email.message_id,
                application_id=match.id if match is not None else None,
                action="update_existing" if match is not None else "new_application",
                previous_classification=processed.classification,
                suggested_classification="relevant",
                suggested_extract_json=_extract_payload(new_extracted, platform_hint),
                pipeline_run_id=run_id,
            )
            suggestions_created += 1

        emails_written += 1
        repo.update_pipeline_run_progress(
            session,
            run_id,
            emails_scrutinized=emails_scrutinized,
            emails_extracted=emails_extracted,
            emails_written=emails_written,
        )

    return {
        "emails_fetched": len(emails),
        "emails_relevant": emails_relevant,
        "applications_created": 0,
        "events_created": 0,
        "suggestions_created": suggestions_created,
    }


def full_scan(settings: Settings | None = None) -> dict:
    """Real end-to-end entrypoint: refetches every email ever seen (ignoring
    the processed_emails idempotency skip a normal sync relies on) from the
    actual Gmail API and re-runs today's pipeline against it, so
    pipeline/prompt improvements can be validated against the real
    historical dataset. See process_full_scan for the testable core logic.
    """
    settings = settings or get_settings()
    sources = get_sources()

    init_db(settings.db_path)
    with Session(get_engine(settings.db_path)) as session:
        run_id = str(uuid.uuid4())
        repo.create_pipeline_run(session, run_id, run_type="full_scan")

        model = get_chat_model(settings)
        client = GmailClient(settings)
        all_message_ids = [row.message_id for row in session.exec(select(ProcessedEmail)).all()]
        emails = client.fetch_messages_by_id(all_message_ids)

        stats = process_full_scan(emails, model=model, session=session, sources=sources, run_id=run_id)

        repo.finish_pipeline_run(
            session,
            run_id,
            emails_fetched=stats["emails_fetched"],
            emails_relevant=stats["emails_relevant"],
            applications_created=stats["applications_created"],
            events_created=stats["events_created"],
            suggestions_created=stats["suggestions_created"],
        )
        return {"run_id": run_id, **stats}
