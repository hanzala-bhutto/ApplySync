---
name: concepts
description: Explain a LangChain/LangChain Community/LangGraph/LangSmith/Langfuse or agentic-AI concept in the context of THIS project's actual code, not as a generic textbook definition. Use when the user asks "what is X", "how does X work here", or wants to understand a concept they just saw used.
---

# In-context concept explainer

This is the primary learning tool for the project. The user is deliberately
building this tracker to learn these technologies, and the value of this skill is
tying an abstract concept to the concrete line of code that uses it, not
reciting documentation.

## Process

1. Identify the concept being asked about (e.g. "structured output parsing",
   "conditional edges", "checkpointing", "tracing", "multi-agent
   orchestration", "state graphs", "tool calling").
2. Find where it is *actually used* in this codebase, grep
   `backend/applysync/` for the relevant construct (e.g. `with_structured_output`,
   `add_conditional_edges`, `SqliteSaver`, `StateGraph`). If the code doesn't
   exist yet (early milestones), say so plainly and explain the concept
   against the *planned* usage from `CLAUDE.md` instead, don't pretend code
   exists that doesn't.
3. Explain in this shape:
   - **What it is** (1-2 sentences, plain language, no jargon left
     unexplained).
   - **Where it's used here**: exact file:function/line.
   - **Why it was chosen for that spot**: what problem it solves in this
     pipeline specifically (e.g. "conditional edges here skip extraction
     entirely for irrelevant emails so we don't burn an LLM call on every
     newsletter").
   - **What breaks without it**: a concrete failure mode if this piece were
     removed or done naively (e.g. "without the `processed_emails` idempotency
     check, every scheduled run would re-extract and duplicate every email
     ever seen").
4. If the user seems to be asking out of general curiosity rather than about
   this codebase, still anchor the answer here. The point of this skill is
   making the abstract concrete, not answering in the abstract and mentioning
   the project as an afterthought.

## Scope

Covers: LangChain (prompt templates, output parsers, structured output),
LangChain Community (document loaders, integrations), LangGraph (StateGraph,
nodes/edges, conditional routing, checkpointing, `Send`/map-reduce patterns),
LangSmith (tracing, evaluation, datasets, once phase 2 lands), Langfuse
(tracing, comparison to LangSmith), and general agentic/multi-agent
orchestration ideas (specialized single-purpose agents vs. one monolithic
prompt, heuristic-first-LLM-fallback design, idempotent pipelines).

Do not use this skill to explain unrelated general programming concepts (e.g.
"what is a decorator") unless they're directly load-bearing for understanding
a piece of this pipeline.
