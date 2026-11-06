# Working through the chapters

This page explains how the repository is put together for learning: what a chapter tag is, what you do at the start of each chapter, what carries over from one chapter to the next, and how to get unstuck. Read it once before Chapter 0, and come back when something does not work.

## Tags are snapshots, your branch is your workspace

Each chapter has a git tag, `ch0` to `ch10`. A tag is the repository exactly as it stands at the end of that chapter: its code, its tests, its docs, and nothing from later chapters. Tags never move.

You do not work on a tag directly. You start each chapter by making a branch of your own from its tag:

```bash
git switch -c my-ch1 ch1
```

`my-ch1` is yours. The exercises ask you to change code and data; those changes live on your branch, and the tag stays clean. If you would rather look around without a branch, `git checkout ch1` works too, but git will warn you about a "detached HEAD", and anything you commit there is easy to lose.

To see exactly what a chapter added, compare two tags. This works from any branch:

```bash
git diff ch0 ch1
git diff ch0 ch1 --stat
```

The first shows everything Chapter 1 adds; the second lists just the files.

## Starting a chapter: the setup steps, and why

Every chapter's **Do it yourself** section starts with the same steps, in this order. Each one is there because skipping it causes a specific failure.

```bash
git switch -c my-chN chN
docker compose up -d --wait <services>
source .venv/bin/activate
uv pip install -e ".[dev]"
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
```

Some chapters add a line or two after these for state an earlier chapter left behind (the graph load, a published procedure revision). The chapter's step 1 always has them.

1. **The branch** gives you the chapter's code and docs. Read the chapter's doc from your branch, not from `main`, so the instructions match the code in front of you.
2. **The services.** Chapters add services as they need them (Neo4j in Chapter 1, Cube in Chapter 2, the AIP server in Chapter 4, the control plane in Chapter 8). `--wait` makes the command return only once every service is healthy. Without it, the next command can run before the database is accepting connections. Neo4j in particular takes 30 to 60 seconds, longer the first time while it downloads its APOC plugin.
3. **The environment.** The `brightwater` command lives inside `.venv`. Activation lasts only for the terminal you ran it in, so every new terminal needs it again. Alternatively, put `uv run` in front of any command (`uv run brightwater assess --handbuilt`), which needs no activation.
4. **The packages.** Most chapters add libraries: Chapter 1 adds LinkML, pySHACL and neo4j-agent-memory, Chapter 3 adds LangChain, and so on. The install is quick when nothing changed, so run it at the start of every chapter.
5. **The data.** The fixture and the database are put back to a known state, so the chapter starts from the same data whatever you did before: a reset, a skipped chapter, or an exercise that changed rows. Both commands take a few seconds and are safe to rerun.

**Chapter 4 is different once.** From Chapter 4 on, the AIP library needs Python 3.14, so that chapter recreates the environment instead of reusing it:

```bash
uv venv -p 3.14 --clear && source .venv/bin/activate && uv pip install -e ".[dev]"
```

Chapter 0 creates the environment the first time, with Python 3.12, which is all Chapters 0 to 3 need.

## On a Mac or on Linux

Every command in the chapters works the same on macOS and Linux. Two places where the two systems usually differ are written to avoid the difference:

- **Editing a file in place.** The exercises use `sed -i.bak '...' file && rm file.bak`. The Linux and macOS versions of `sed` disagree about plain `sed -i`, and that form is the one both accept.
- **Talking to Postgres.** The `psql` commands run inside the Postgres container (`docker compose exec -T postgres psql ...`), so you do not need a Postgres client installed on your machine.

One difference the commands cannot avoid: zsh, the macOS default shell, does not treat `#` as the start of a comment when you paste commands, so a line such as `git diff ch0 ch1   # everything Chapter 1 adds` passes `#` and the words after it to the command. Run `setopt interactivecomments` once in each terminal, or add that line to `~/.zshrc`, before pasting blocks that contain comments.

## Moving on to the next chapter

When you finish a chapter:

```bash
git add -A && git commit -m "my chapter 1 work"
git switch -c my-ch2 ch2
```

Instead of committing, `git stash` puts your changes aside, and `git restore .` throws them away.

Then run the steps above. Commit, stash or discard your exercise changes first: git refuses to switch branches when an uncommitted change would be overwritten, and changes it can carry over will follow you into the next chapter, where they no longer make sense.

## What carries over, and what does not

| Carries over between chapters | Why |
|---|---|
| `.venv`, the Python environment | It is not tracked by git, so switching branches does not touch it. You only need to install each chapter's new packages (step 4). |
| `fixture/out/`, the generated CSVs | Also untracked. Each chapter's step 1 regenerates it anyway, because Chapter 6 changes the generator: from there on the generated applicants' cash flows differ, and a fixture left over from an earlier chapter gives different numbers. |
| The data in Postgres and Neo4j | It lives in Docker volumes, outside the repository. Later chapters add tables; their steps say when to rerun `brightwater db init`. |
| Your branches and their commits | Each `my-chN` keeps what you did in that chapter. |

| Does not carry over | Why |
|---|---|
| Code changes on one chapter's branch | A new branch starts from the next tag, which has none of your edits. That is deliberate: each chapter starts from a known state. |
| An activated terminal | Activation is per terminal. Open a new one, activate again. |

## Going back, or starting over

To redo a chapter from scratch, make a fresh branch from its tag:

```bash
git switch -c my-ch2-again ch2
```

To put the data back to a known state without touching Docker, reload it:

```bash
brightwater db init && brightwater db load && brightwater policy sync
```

To wipe the services completely and start from nothing, remove their volumes. This deletes all data in Postgres and Neo4j. Afterwards, start the services again and reload the data with the `db init` line above; the fixture itself does not need regenerating, because it is still in `fixture/out/`.

```bash
docker compose down -v
```

## Command blocks: read the first error

A command block is several commands pasted at once, and your shell runs every line even when an earlier one fails. Later lines usually depend on earlier ones, so a failure near the top can leave the rest running against the wrong state and printing output that looks normal but is not what the doc shows. If any line in a block prints an error, stop there: fix that line, then run the block again from the top.

## When something does not work

| You see | What it means | Fix |
|---|---|---|
| `command not found: brightwater` (zsh) or `brightwater: command not found` (bash) | This terminal's environment is not active. | `source .venv/bin/activate`, or `uv run brightwater ...` |
| a traceback ending in `ModuleNotFoundError: No module named 'pyshacl'` (or any other package) | This chapter's packages are not installed yet. | `uv pip install -e ".[dev]"` |
| `Neo4j service unavailable: Couldn't connect to localhost:7687` | Neo4j is not running, or has not finished starting. | `docker compose up -d --wait neo4j-memory`, then check `docker compose ps` says `healthy` |
| `FileNotFoundError: ... fixture/out/ledger.business.csv` (or another `fixture/out/` file) | The fixture was never generated, or was deleted. | `brightwater fixture generate` |
| `httpx.ConnectError: [Errno 61] Connection refused` (macOS; `[Errno 111]` on Linux) from `brightwater assess` or a tool | Cube (Chapter 2 on) is not running. | `docker compose up -d --wait cube` |
| a connection error on port 5432 | Postgres is not running. | `docker compose up -d --wait postgres` |
| `the current Python version (3.12...) does not satisfy Python>=3.14` | You are at Chapter 4 or later with the Chapter 0 environment. | `uv venv -p 3.14 --clear && source .venv/bin/activate && uv pip install -e ".[dev]"` |
| `fatal: a branch named 'my-ch1' already exists` | You started this chapter before. | `git switch my-ch1` to continue, or `git switch -c my-ch1-again ch1` to start fresh |
| `error: Your local changes to the following files would be overwritten` | You have uncommitted exercise changes. | `git stash`, or commit them, then switch |
| `sed: 1: ...: unescaped newline inside substitute pattern`, or `sed: -i may not be used with stdin` | macOS `sed` read your pattern as a backup suffix: the command was written for Linux. | Use the form the chapters use: `sed -i.bak 's/old/new/' file && rm file.bak` |
| `zsh: command not found: #`, or `unrecognized arguments: # ...` | zsh passed a pasted comment to the command. | `setopt interactivecomments`, then run the line again |
| `psql: command not found` | No Postgres client on your machine. | Run it in the container: `docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c "..."` |
| The output does not match the chapter's doc | You may be reading a doc from a different chapter than your branch. | `git branch --show-current`, and read the doc from that branch |

If none of these fit, run the chapter's tests (each doc's **The proof** section lists the command). They check the same things the walkthrough does and usually point at the step that went wrong.
