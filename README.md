# Zero to Production in FastAPI

A course that takes you from never having written code to building, deploying and operating a production AI backend on AWS, by building one real project that grows lesson by lesson.

> Status: the course is being built. See the [roadmap](docs/context/05-roadmap.md) for what exists today.

## What you will build

**Atlas** answers questions about an open-source codebase (PyTorch-Ignite) and cites the exact file and line behind every claim.

**Atlas Agents** watches the project for new good-first issues and prepares an **Issue Brief** for each: a summary, the relevant code, the project's conventions, the commands to run, and a reproduction script verified to fail. You then write the fix yourself. Atlas never posts to GitHub and never writes your code.

Along the way you learn the whole backend stack: Python, Git, operating systems and Linux, HTTP, SQL and Postgres in depth, FastAPI (sync and async), authentication and OAuth, background jobs, event streams, caching, retrieval (RAG), agents and sandboxing, Docker, CI/CD, Terraform, AWS, observability, security, incident response, distributed systems and system design. 96 lessons in 10 parts.

## Course map

| Part | Topic | Checkpoint |
|---|---|---|
| 0 | Start here: terminal, Git, GitHub | Your fork |
| 1 | Programming with Python | `atlas search` CLI |
| 2 | Computers and the internet | Fetch issues from GitHub |
| 3 | Databases and SQL | Data in Postgres |
| 4 | Building APIs with FastAPI | Atlas API v1 + web UI |
| 5 | Background work and integrations | Automatic syncing and indexing |
| 6 | AI engineering: Atlas (RAG) | Cited answers, measured quality |
| 7 | Production on AWS | Atlas live on AWS |
| 8 | AI engineering: Atlas Agents | Verified Issue Briefs |
| 9 | Scale and career | Unguided capstone, portfolio |

Full lesson list: [docs/context/04-curriculum.md](docs/context/04-curriculum.md).

## How to use this repo

- **Read the course** on the course site (link added once GitHub Pages is live), or in `course/`.
- **Write your own code** in your own repository, `my-atlas`, which you create in lesson 1.1.
- **Compare** with the reference implementation in `atlas/` at the matching checkpoint tag (`git checkout checkpoint-04`).

## Project documentation

Everything about goals, architecture, conventions and decisions is in [docs/context/](docs/context/README.md).

## History

This repo started as a FastAPI learning project (a Posts API). That state is preserved at tag `posts-api-v1`, and the earlier tutorial's reference solution at `posts-api-solution-v1`.
