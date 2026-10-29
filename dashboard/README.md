# Dashboard development

This directory contains the read-only React/Vite UI, a small FastAPI status controller, and the versioned profile registry. See [the operator runbook](../docs/DASHBOARD.md) for Windows setup, local access, API routes and verification commands.

The production static UI is built into `frontend/dist` and served by FastAPI. The controller binds to `127.0.0.1` and performs fixed Docker status queries only. Profile switching, benchmarks, database monitoring and logs are separate later phases.
