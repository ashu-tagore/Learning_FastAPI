# 01. Vision

## One sentence

**Zero to Production in FastAPI** takes a learner from never having written code to designing, building, deploying and operating a production AI backend on AWS, by building one real product that grows lesson by lesson.

## Who it is for

- **Starting level: truly zero.** No programming, terminal or Git knowledge assumed.
- **Target role: AI Engineer.** Someone who builds the systems around models: data pipelines, retrieval, agents, evaluation, and the backend that makes them reliable, secure and affordable.
- **First learner and owner:** the repository owner, preparing for Google Summer of Code (GSoC) 2027, aiming at the PyTorch-Ignite project.

## Teaching approach

- **Concepts are language-agnostic; Python/FastAPI is the vehicle.** Every lesson explains the idea first (HTTP, transactions, queues, embeddings), then shows it in Python. A learner should be able to carry the idea to Go or TypeScript later.
- **Analogy, then theory, then real code.** Every lesson opens with an analogy for intuition, then the precise theory, then the project code.
- **One project that keeps growing.** No throwaway toy apps. Every lesson adds something real to the same product.
- **Learn by doing.** Every lesson has a quiz, a guided exercise and a stretch challenge, with hidden solutions.
- **Practice without hand-holding.** Each part ends with a review lesson of unguided tasks, and the course ends with an unguided capstone, because following along is not the same as building alone.

## The product we build

Two stages of one product, both aimed at helping a human contribute to open source:

1. **Atlas** answers questions about a target open-source project (PyTorch-Ignite), citing the exact file and line its answer comes from.
2. **Atlas Agents** watches the project for new good-first issues and, for each one, prepares an **Issue Brief**: a summary, the relevant code, the conventions that apply, the setup and test commands, and a reproduction script it has verified actually fails. The human then writes the fix themselves.

Details are in [02-product.md](02-product.md).

## Non-goals and hard limits

- **Atlas never writes to GitHub.** No issues, PRs, comments, pushes, commit messages or replies to reviewers. Its GitHub token is read-only. This follows PyTorch-Ignite's own `AGENTS.md`, which forbids all of these for AI agents, and protects the learner's GSoC application: mentors judge the *human's* understanding.
- **Atlas does not generate fixes.** The Issue Brief contains no patch. It explains and reproduces; the human writes the fix.
- **Not a frontend course.** A ready-made web interface is provided and briefly explained; the course stays on backend.
- **Not a machine-learning course.** We use models through APIs and local runtimes; we do not train them.

## What "done" looks like

- A public Quarto course site on GitHub Pages, 100 lessons in 10 parts, every lesson complete with exercises, and a review-and-practice lesson closing each part.
- Atlas and Atlas Agents running on AWS, deployed by CI/CD from this repo with Terraform.
- A reference implementation in this repo whose tests pass in CI at every checkpoint tag.
- Measured quality: retrieval and agent evaluations run in CI with scores tracked over time.
- The learner has built one service (the Part 9 capstone) from a spec without guidance.
- The learner can explain every line, and has made real contributions to the target project **by hand**.

## A note on the GSoC timeline

GSoC applications usually open in early spring (verify dates on the official GSoC site). The course reaches agents late. The learner should **start contributing to PyTorch-Ignite by hand now**, in parallel with the course. Atlas becomes useful around Part 6; Atlas Agents around Part 8.
