# AI-engineering competency scorecard

What this project exercises as an AI-engineering discipline, rated against a
broad competency list. This is a companion to the README's [LLMOps
section](../README.md#llmops), moved out of the README to keep that document
focused on what the project is and how to run it.

"Learned" means real production scar tissue (a bug hit and a fix shipped), not a
concept read about. The "Not touched" rows all sit below the hosted-model API
boundary or assume multiple tenants, out of scope for a single-user, local-first
tool that consumes a hosted endpoint.

| Competency | Rating | Evidence in this repo |
| --- | --- | --- |
| Harness engineering (not just prompt engineering) | Learned | Single-responsibility LangGraph nodes, conditional edges, checkpointing vs `processed_emails` idempotency |
| Context engineering | Learned | Separating "was an application submitted" from "good/bad news"; ignoring "similar jobs" sections; accuracy degrading as the prompt grew |
| Structured-output failures, validation, fallback chains | Learned | `with_structured_output` returns empty on list fields, switched to `PydanticOutputParser` + flat scalar schemas; placeholder-text normalization |
| Function-calling reliability, tool contracts, idempotency | Learned | Disambiguation agent: scalar-only tool args, terminal `submit_verdict`, hand-rolled loop; upsert marks processed exactly once |
| Agent guardrails, loop/tool budgets, termination | Learned | `MAX_AGENT_TURNS` tuned against real data, fail-open on error |
| Model routing, graceful fallback, degraded-mode UX | Learned | Tiered fast + escalation model, optional Groq agent with NVIDIA `.with_fallbacks()`, fail-open everywhere, clean degrade without gmail/search clients |
| Evals: golden sets, regression, LLM-as-judge, human | Learned | `eval/run_eval.py`, human-verified gold, `--strict` gate, per-stage judge, a regression caught and reverted |
| LLM observability (traces, spans, tokens, latency, drift) | Learned | Self-hosted Langfuse; the shared-rate-limiter latency bug was invisible until traces existed |
| Latency / quality / cost / reliability tradeoffs | Learned | 9x model-speed swap that cost then recovered accuracy, the `N/40*60` throughput floor, escalation only where a wrong merge is unrecoverable |
| LLMOps as CI/CD + gating + versioning | Learned | Cloud CI, local eval gate, pre-push enforcement, committed metrics ledger |
| Prompt caching vs semantic caching | Partial | `CompanyProfile` cache keyed by company name with `refresh` invalidation; prompt caching N/A on the hosted endpoint |
| RAG: chunking, embeddings, hybrid search, reranking | Partial | SearXNG retrieval + grounded synthesis with source URLs; no vector store, embeddings, or reranking |
| Retrieval evals: grounding, attribution, citation | Partial | Research card stores and shows its source URLs; no recall/precision metrics |
| Safety: prompt-injection defense, leakage, permissions | Partial | Prompt-injection hardening, readonly Gmail scope, local-first/keyless; no adversarial injection suite yet |
| Cost attribution per feature / workflow / user | Partial | Rate-limit math and tiered models, Langfuse token capture; no per-feature cost surface |
| Fine-tuning vs ICL vs RAG vs distillation | Partial | Chose in-context + light RAG deliberately; never practiced fine-tuning or distillation |
| KV cache, prefill/decode, batching, paged attention | Not touched | Below the hosted-model API boundary |
| Quantization (INT8/INT4/FP8, AWQ, GPTQ) | Not touched | Would require self-hosting the model |
| Speculative decoding vs quantization vs distillation | Not touched | Would require self-hosting the model |
| Multi-tenant isolation, cache safety, cross-user contamination | Not touched | Single-user tool by design |
