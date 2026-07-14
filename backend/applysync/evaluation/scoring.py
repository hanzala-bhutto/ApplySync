"""Pure scoring logic for the eval harness (see eval/run_eval.py for the
runner that produces predictions against the real model). Kept free of any
LLM or DB dependency so it is unit-testable with plain fixtures.

Metrics are deliberately per-stage, not one blended number, because the
stages fail differently and their costs are asymmetric:
- scrutiny: a false REJECT silently drops a real application forever, while
  a false pass merely costs one extra LLM call - so false-reject rate is
  the metric, not overall accuracy.
- classification: plain accuracy of the relevant/irrelevant call.
- extraction: per-field accuracy on the samples both labeled and classified
  relevant, using the SAME normalization the matching layer uses, so "EGYM"
  vs "EGYM SE" scores as correct exactly when matching would treat it as
  the same company.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from applysync.db.repository import _normalize_for_matching
from applysync.pipeline.nodes import UNSPECIFIED_JOB_TITLE


@dataclass
class EvalSample:
    """One labeled email. `verified` distinguishes human-checked labels from
    machine-pre-filled ones (build_eval_dataset.py writes verified=false;
    a human flips it after reviewing/correcting the labels)."""

    message_id: str
    sender: str
    subject: str
    date: str
    body: str
    label_is_relevant: bool
    label_company: str | None = None
    label_title: str | None = None
    label_status: str | None = None
    verified: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> EvalSample:
        labels = data.get("labels", {})
        return cls(
            message_id=data["message_id"],
            sender=data.get("sender", ""),
            subject=data.get("subject", ""),
            date=data.get("date", ""),
            body=data.get("body", ""),
            label_is_relevant=labels.get("is_relevant", False),
            label_company=labels.get("company_name"),
            label_title=labels.get("job_title"),
            label_status=labels.get("status"),
            verified=data.get("verified", False),
        )

    def to_dict(self) -> dict:
        # Field order matters here: this dict is what gets pretty-printed to
        # the review file (see save_samples), and a human reviewing it wants
        # verified/labels visible first, the long body last - not needing to
        # scroll past a paragraph of email text to find the fields they're
        # actually checking.
        return {
            "message_id": self.message_id,
            "verified": self.verified,
            "labels": {
                "is_relevant": self.label_is_relevant,
                "company_name": self.label_company,
                "job_title": self.label_title,
                "status": self.label_status,
            },
            "sender": self.sender,
            "subject": self.subject,
            "date": self.date,
            "body": self.body,
        }


@dataclass
class StagePrediction:
    """What the pipeline produced for one sample. classification and the
    extraction fields are None when the email never reached that stage
    (scrutiny rejected it, or extraction failed)."""

    message_id: str
    scrutiny: str  # "pass" | "reject"
    classification: str | None = None  # "relevant" | "irrelevant"
    company: str | None = None
    title: str | None = None
    status: str | None = None


@dataclass
class Mismatch:
    message_id: str
    field: str
    expected: str | None
    got: str | None


@dataclass
class EvalReport:
    total: int = 0
    # scrutiny
    scrutiny_false_rejects: list[str] = field(default_factory=list)
    scrutiny_relevant_total: int = 0
    scrutiny_over_passes: int = 0
    # classification (samples that passed scrutiny)
    classified_total: int = 0
    classified_correct: int = 0
    # extraction (samples labeled relevant AND classified relevant)
    extraction_total: int = 0
    company_correct: int = 0
    title_correct: int = 0
    status_correct: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def scrutiny_false_reject_rate(self) -> float:
        if self.scrutiny_relevant_total == 0:
            return 0.0
        return len(self.scrutiny_false_rejects) / self.scrutiny_relevant_total

    @property
    def classification_accuracy(self) -> float:
        return self.classified_correct / self.classified_total if self.classified_total else 0.0

    @property
    def company_accuracy(self) -> float:
        return self.company_correct / self.extraction_total if self.extraction_total else 0.0

    @property
    def title_accuracy(self) -> float:
        return self.title_correct / self.extraction_total if self.extraction_total else 0.0

    @property
    def status_accuracy(self) -> float:
        return self.status_correct / self.extraction_total if self.extraction_total else 0.0


@dataclass
class Thresholds:
    """Reliability targets (see docs/feasibility/eval-harness.md). A false
    scrutiny reject silently loses a real application, so its budget is zero
    by default; the extraction fields tolerate the documented residual error
    the model shows on real inboxes."""

    max_scrutiny_false_rejects: int = 0
    min_company_accuracy: float = 0.90
    min_title_accuracy: float = 0.90
    min_status_accuracy: float = 0.95

    def passed(self, report: EvalReport) -> bool:
        return (
            len(report.scrutiny_false_rejects) <= self.max_scrutiny_false_rejects
            and report.company_accuracy >= self.min_company_accuracy
            and report.title_accuracy >= self.min_title_accuracy
            and report.status_accuracy >= self.min_status_accuracy
        )


def _titles_match(label_title: str | None, predicted_title: str | None) -> bool:
    # A null label means the email genuinely never states the role, so the
    # pipeline is correct exactly when it emits the unspecified sentinel
    # instead of inventing something.
    if label_title is None:
        return predicted_title == UNSPECIFIED_JOB_TITLE
    if predicted_title is None:
        return False
    return _normalize_for_matching(predicted_title) == _normalize_for_matching(label_title)


def _companies_match(label_company: str | None, predicted_company: str | None) -> bool:
    if label_company is None or predicted_company is None:
        return label_company is None and predicted_company is None
    return _normalize_for_matching(predicted_company) == _normalize_for_matching(label_company)


def score_samples(
    samples: list[EvalSample], predictions: dict[str, StagePrediction]
) -> EvalReport:
    report = EvalReport(total=len(samples))

    for sample in samples:
        pred = predictions.get(sample.message_id)
        if pred is None:
            continue

        if sample.label_is_relevant:
            report.scrutiny_relevant_total += 1
            if pred.scrutiny == "reject":
                report.scrutiny_false_rejects.append(sample.message_id)
                # Never reached classification/extraction; scoring those
                # stages for it would double-count the same failure.
                continue
        elif pred.scrutiny == "pass":
            report.scrutiny_over_passes += 1

        if pred.scrutiny != "pass" or pred.classification is None:
            continue

        report.classified_total += 1
        predicted_relevant = pred.classification == "relevant"
        if predicted_relevant == sample.label_is_relevant:
            report.classified_correct += 1
        else:
            report.mismatches.append(
                Mismatch(
                    sample.message_id,
                    "is_relevant",
                    str(sample.label_is_relevant),
                    str(predicted_relevant),
                )
            )

        if not (sample.label_is_relevant and predicted_relevant):
            continue

        report.extraction_total += 1
        if _companies_match(sample.label_company, pred.company):
            report.company_correct += 1
        else:
            report.mismatches.append(
                Mismatch(sample.message_id, "company_name", sample.label_company, pred.company)
            )
        if _titles_match(sample.label_title, pred.title):
            report.title_correct += 1
        else:
            report.mismatches.append(
                Mismatch(sample.message_id, "job_title", sample.label_title, pred.title)
            )
        if sample.label_status is not None and pred.status == sample.label_status:
            report.status_correct += 1
        else:
            report.mismatches.append(
                Mismatch(sample.message_id, "status", sample.label_status, pred.status)
            )

    return report


def format_report(report: EvalReport, thresholds: Thresholds | None = None) -> str:
    def pct(value: float) -> str:
        return f"{value * 100:.1f}%"

    lines = [
        f"samples scored: {report.total}",
        "",
        "scrutiny",
        f"  false rejects: {len(report.scrutiny_false_rejects)}/{report.scrutiny_relevant_total} relevant"
        f" ({pct(report.scrutiny_false_reject_rate)})"
        + (f"  <- {report.scrutiny_false_rejects}" if report.scrutiny_false_rejects else ""),
        f"  over-passes (irrelevant let through, costs 1 LLM call each): {report.scrutiny_over_passes}",
        "",
        "classification (relevant vs irrelevant)",
        f"  accuracy: {report.classified_correct}/{report.classified_total} ({pct(report.classification_accuracy)})",
        "",
        "extraction (on correctly-classified relevant samples)",
        f"  company: {report.company_correct}/{report.extraction_total} ({pct(report.company_accuracy)})",
        f"  title:   {report.title_correct}/{report.extraction_total} ({pct(report.title_accuracy)})",
        f"  status:  {report.status_correct}/{report.extraction_total} ({pct(report.status_accuracy)})",
    ]
    if report.mismatches:
        lines += ["", f"mismatches ({len(report.mismatches)})"]
        for m in report.mismatches:
            lines.append(f"  [{m.field}] {m.message_id}: expected {m.expected!r}, got {m.got!r}")
    if thresholds is not None:
        lines += ["", f"thresholds: {'PASS' if thresholds.passed(report) else 'FAIL'}"]
    return "\n".join(lines)


def load_all_samples(path) -> list[EvalSample]:
    """Every sample in the file regardless of verified status - used by the
    dataset builder (which needs to preserve unverified ones too), not the
    eval runner (see load_samples)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [EvalSample.from_dict(data) for data in raw]


def load_samples(path, *, include_unverified: bool = False) -> list[EvalSample]:
    all_samples = load_all_samples(path)
    if include_unverified:
        return all_samples
    return [s for s in all_samples if s.verified]


def save_samples(path, samples: list[EvalSample]) -> None:
    """Pretty-printed JSON array, not JSONL: a human reviews/corrects this
    file directly in an editor, and one record per unbroken line (JSONL's
    whole point for streaming/appending) is unreadable once a record holds a
    multi-paragraph email body. indent=2 costs nothing here - this dataset is
    a few hundred records, not a streaming log."""
    data = [s.to_dict() for s in samples]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
