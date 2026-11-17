# Project context

These documents are the single source of truth for what this repository is, what it builds, and how the work is done. Read them before changing anything. They are written for two readers: humans joining the project, and AI assistants working in it.

| Doc | Answers |
|---|---|
| [01-vision.md](01-vision.md) | Why does this repo exist? Who is it for? What does "done" look like? |
| [02-product.md](02-product.md) | What are Atlas and Atlas Agents, feature by feature? What is an Issue Brief? |
| [03-architecture.md](03-architecture.md) | How is the system built? Components, data model, local vs AWS. |
| [04-curriculum.md](04-curriculum.md) | Which lessons exist, in what order, and what each one adds to the project? |
| [05-roadmap.md](05-roadmap.md) | In what order is the work done, and what is the status of each milestone? |
| [06-conventions.md](06-conventions.md) | How do we write code, lessons, tests and commits? |
| [07-decisions.md](07-decisions.md) | Which decisions were made, why, and what was rejected? |

## Rules for keeping these docs useful

- When a decision changes, update the doc **and** add an entry to `07-decisions.md`. Never silently rewrite history.
- When a milestone changes status, update the table in `05-roadmap.md` in the same commit.
- Keep them short. If a section grows past a screen, it probably belongs in a lesson instead.
