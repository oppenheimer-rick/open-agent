# Core Philosophy: J.A.R.V.I.S. (Just A Rather Very Intelligent System) Protocol

This document outlines the core operational philosophy of `open-agent`. All features, startup routines, and command loops must align with these principles.

---

## 1. Zero-Latency Boot
* **Non-Blocking Execution**: The startup process must never block the user from typing or using the agent. All remote network requests (e.g. news feeds, updates) must have sub-second timeouts and execute gracefully.
* **Instant Local Queries**: File-system scans (like scanning the Obsidian Vault) must only query directory metadata and file paths. The agent must never read full file contents or invoke LLM calls on startup, avoiding costly execution delays.

## 2. Instant System Utility
* **High-Signal Diagnostics**: On startup, the agent should act like an interactive system dashboard (similar to Tony Stark's J.A.R.V.I.S.).
* **Real-time updates**: Provide immediate, meaningful information about the local machine (load averages, memory usage, backend LLM availability) and the broader world (top tech/industry news via Hacker News).
* **Clear Actions**: Present a structured greeting and update summary, letting the user know exactly what has changed or what needs attention.
