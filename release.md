# RAI v2.0.0 Release Notes

RAI v2 is a full rewrite and release refresh of the open-source AI security operator. This version focuses on making the main workflow faster to start, easier to package, and broader in capability across autonomous security work.

## Highlights

- `rai chat` is now part of the default install and starts the HTTP-backed TUI experience out of the box.
- The HTTP server stack is included in the base package, so the main interactive flow works without extra dependencies.
- The Docker image is now supported for local testing and GHCR publishing.
- The project now ships with a single release commit history for the v2 baseline.
- Versioning is aligned across the package, CLI, and runtime as `2.0.0`.

## Top Features in v2

### Interactive Security Operator

RAI is built to operate as a terminal-native security assistant, not a generic chat client. It can run autonomous tasks, manage approvals, and coordinate work through the CLI and TUI.

### Plan Mode

Complex work can be broken into structured steps, reviewed before execution, and tracked live during the run. This is the main control point for high-trust autonomous workflows.

### Persistent Memory

RAI stores user, agent, and target memory across sessions so it can preserve methodology, findings, preferences, and target context over time.

### Multi-Agent Execution

RAI can dispatch specialized subagents for recon, research, code analysis, cloud work, reversing, Android analysis, and other focused tasks.

### HTTP Streaming API

The FastAPI-backed server exposes runs, threads, tasks, subagents, HITL approval, and SSE event streams for remote control and TUI integration.

### Textual TUI

The built-in TUI gives a rich operator interface for approvals, plan review, findings, threads, model selection, and background runs.

### MCP and Skills

RAI integrates with MCP tools and Markdown-based skills so capabilities can be extended without changing the core agent runtime.

### Security Tooling

The toolkit covers bash execution, findings management, memory, references, web security, cloud, container, Active Directory, Android, and reversing workflows.

### Docker and GHCR

The project now includes a container build path and a release workflow that publishes Docker images to GitHub Container Registry.

## Packaging Notes

- Base install: `pip install revolt-rai`
- No extra is required for `rai chat`
- Optional provider extras remain available for alternate model backends

## What Changed at a Glance

- Unified release version: `2.0.0`
- Default interactive path now works without optional HTTP extras
- Docker build and publish workflow added
- Source tree and release metadata aligned for the v2 baseline

## Compatibility

This release is intended as the new v2 baseline. If you are upgrading from an older branch, review agent configs, custom prompts, and any local workflows that assumed the older packaging layout.
