# BABU Capabilities

## Services
Services are infrastructure capabilities. Each service has known capabilities, dependencies, failure modes, and fallbacks.
- **Telegram**: Chat-based input/output interface. Fallbacks: Console Simulator.
- **Facebook**: Social Media page publishing.
- **Email**: Sending via Google Workspace.
- **Google Workspace**: Sheets, Docs, Drive, Calendar integrations.
- **Scheduler**: Time-based background triggers.
- **Tavily / Web API**: Public information retrieval.

## Workflows
- **Social Media Marketing**: Configured via scheduled task to draft graphics and copy, waiting for user approval.
- **Data Lookup**: Zero-token querying against the local profile or rapid RAG lookup.
- **Agent Orchestration**: Decomposing intents into goal graphs (DAG) and running parallel/sequential tasks across multiple departments.
