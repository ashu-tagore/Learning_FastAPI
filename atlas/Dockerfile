# Atlas's production image (lesson 7.1). One image runs every Atlas process; the command
# chooses which: the API (the default below), `atlas worker`, `atlas scheduler`,
# `atlas notifier`, `atlas init-db`.
#
#   docker build -t atlas .
#   docker run --rm -p 8000:8000 --env-file .env atlas

# ---------- Stage 1: build the virtual environment ----------
FROM python:3.13-slim AS build

# uv from its official image (also published as ghcr.io/astral-sh/uv), pinned like any
# dependency: the same Dockerfile must build the same image next month.
COPY --from=astral/uv:0.11.32 /uv /bin/uv

# Compile .py files to bytecode now, so containers don't each do it on first import.
# Copy files out of uv's cache instead of linking: the cache is a build-only mount.
# Never download a Python: the base image's is the one the runtime stage has too.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, on their own: this layer is rebuilt only when the lock file changes,
# not on every code change. The cache mount keeps downloaded packages between builds
# without putting them in the image.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# ---------- Stage 2: add Atlas itself ----------
# A separate stage, so stage 1's environment (dependencies only) can be copied on its own.
FROM build AS project

# Installed as a package (not a link to /app/src), so nothing else from /app is needed.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------- Stage 3: what actually runs ----------
FROM python:3.13-slim AS runtime

# git, because the worker clones repositories and `git archive`s snapshots (lessons 5.3
# and 5.4); the slim image doesn't have it. --no-install-recommends skips the extras
# (documentation, a mail client, Perl modules) that a plain install pulls in, and the
# package lists are deleted so they don't sit in the image.
# The package mirror over HTTPS, not the image's default HTTP: apt checks signatures
# either way, but HTTPS also hides which packages are fetched, and some networks (company
# proxies, the machine this course was built on) allow nothing else.
# `upgrade` applies Debian's security fixes that the base image doesn't have yet: the
# image scan found one (zlib) after Debian had released its fix. The price: builds on
# different days can differ, so CI rebuilds and rescans regularly (lesson 7.2).
# The build cache would otherwise keep the first day's upgrade forever: CI's scan went
# red overnight on Kerberos fixes this step had never fetched (lesson 7.2). A new value
# here makes Docker run this step again; CI passes the date, so it runs once a day.
ARG SECURITY_UPDATES_DATE=unset
RUN echo "Debian security updates as of ${SECURITY_UPDATES_DATE}" \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get upgrade --yes \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# A user with no password, no shell login and no rights beyond its own home: if Atlas
# is ever compromised, the attacker is this user, not root (lesson 2.4).
# The clones folder is created now, owned by atlas: a volume mounted on a folder that
# doesn't exist in the image is created as root, and the worker couldn't write to it.
# pip goes: nothing installs packages at runtime, and its bundled copies of urllib3 and
# msgpack were the only fixable high-severity findings in the Python packages.
RUN groupadd --system --gid 10001 atlas \
    && useradd --system --uid 10001 --gid atlas --create-home atlas \
    && install --directory --owner atlas --group atlas /home/atlas/.cache/atlas \
    && python -m pip uninstall --yes --quiet pip

# Only the built environment crosses over: no uv, no source tree, no cache. In two layers,
# slowest-changing first: the dependencies (about 180 MB, unchanged until uv.lock changes),
# then Atlas's own files (under 1 MB). A code change rebuilds, pushes and pulls only the
# second.
COPY --from=build --chown=atlas:atlas /app/.venv /app/.venv
COPY --from=project --chown=atlas:atlas /app/.venv/lib/python3.13/site-packages/atlas /app/.venv/lib/python3.13/site-packages/atlas
COPY --from=project --chown=atlas:atlas /app/.venv/lib/python3.13/site-packages/atlas-0.1.0.dist-info /app/.venv/lib/python3.13/site-packages/atlas-0.1.0.dist-info
COPY --from=project --chown=atlas:atlas /app/.venv/bin/atlas /app/.venv/bin/atlas

# The environment's scripts (atlas, uvicorn) come first on PATH. Python output goes
# straight to the log instead of waiting in a buffer that a crash would lose.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER atlas
WORKDIR /home/atlas

EXPOSE 8000
# 0.0.0.0, not 127.0.0.1: inside a container, 127.0.0.1 is only the container itself
# (lesson 2.2). No --reload: that's for development.
CMD ["uvicorn", "atlas.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
