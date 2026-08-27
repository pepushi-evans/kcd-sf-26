# =============================================================================
# Agent image for the KCD SF 2026 demo.
#
# Built with Nix for a reproducible, pinned toolchain closure. Multi-stage:
#   1. builder: a Nix image realizes an env of all tooling from a pinned nixpkgs
#   2. final:   copy the Nix store closure into a slim Debian base, run as
#               non-root uid 65532
#
# Bundled (all from nixpkgs, pinned via the channel/commit below):
#   nono, github-copilot-cli (`copilot`), gh (GitHub CLI), git, gcc,
#   coreutils, gnumake, python3, go, bun, nodejs_22 (npm), yarn, rustc, cargo
#
# NOTE: github-copilot-cli is an UNFREE package in nixpkgs, so the builder sets
# NIXPKGS_ALLOW_UNFREE=1 and uses --impure to allow it.
#
# Build:
#   docker build -t ghcr.io/your-org/review-agent:latest .
#
# Why a Debian final stage instead of pure Nix: nono enforces Landlock at
# runtime in the CONTAINER (host kernel), independent of how the rootfs was
# built. A glibc base keeps prebuilt agent binaries happy. If you want a
# pure-Nix image, swap the final stage for `FROM nixos/nix` and
# `nix profile install` the same packages.
# =============================================================================

# ----------------------------------------------------------------------------
# Stage 1: Nix builder. Pin nixpkgs by commit for reproducibility.
# ----------------------------------------------------------------------------
FROM nixos/nix:2.24.9 AS builder

# Enable flakes + the new nix CLI.
RUN mkdir -p /etc/nix && \
    printf 'experimental-features = nix-command flakes\nfilter-syscalls = false\n' \
      >> /etc/nix/nix.conf

# Pin nixpkgs. Bump this commit to move the whole toolchain forward at once.
ARG NIXPKGS_REV=nixos-unstable
ENV NIXPKGS=github:NixOS/nixpkgs/${NIXPKGS_REV}

# Realize a profile containing the full toolchain. `nono` and
# `github-copilot-cli` are both packaged in nixpkgs. github-copilot-cli is
# UNFREE, so allow it explicitly (env + --impure). Everything here is closed
# over into /nix/store.
#
# `gh` bundles its own git, which collides with the standalone `git` on
# share/git-gui/... . Install git FIRST with a higher priority (lower number)
# so it wins the profile symlinks, then install the rest.
ENV NIXPKGS_ALLOW_UNFREE=1
RUN nix profile install --impure --priority 4 "${NIXPKGS}#git" && \
    nix profile install --impure --priority 6 \
      "${NIXPKGS}#nono" \
      "${NIXPKGS}#github-copilot-cli" \
      "${NIXPKGS}#gh" \
      "${NIXPKGS}#gcc" \
      "${NIXPKGS}#coreutils" \
      "${NIXPKGS}#gnumake" \
      "${NIXPKGS}#python3" \
      "${NIXPKGS}#go" \
      "${NIXPKGS}#bun" \
      "${NIXPKGS}#nodejs_22" \
      "${NIXPKGS}#yarn" \
      "${NIXPKGS}#rustc" \
      "${NIXPKGS}#cargo"

# Collect the closure of the profile so we can copy ONLY what's needed.
RUN mkdir -p /output/nix/store && \
    cp -a $(nix-store -qR /root/.nix-profile) /output/nix/store/ && \
    mkdir -p /output/profile && \
    cp -a /root/.nix-profile/. /output/profile/

# ----------------------------------------------------------------------------
# Stage 2: slim runtime image.
# ----------------------------------------------------------------------------
FROM debian:12-slim AS final

# Minimal OS deps: CA certs (TLS to api.github.com / model providers) and
# tini for clean PID 1 signal handling.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates tini && \
    rm -rf /var/lib/apt/lists/*

# Bring in the Nix-built toolchain closure and profile symlinks.
COPY --from=builder /output/nix/store /nix/store
COPY --from=builder /output/profile /opt/toolchain

# Put the Nix profile on PATH. This exposes nono, copilot, gh, git, gcc,
# python3, go, bun, node/npm, yarn, rustc, cargo.
ENV PATH=/opt/toolchain/bin:/usr/local/bin:/usr/bin:/bin

# Non-root runtime user (matches the Deployment's runAsUser: 65532).
# debian-slim has no shadow tools (groupadd/useradd), so append entries directly.
# HOME is /home/agent (nono keeps its state under $HOME/.nono); /workspace is
# the agent's working tree. Keeping them separate avoids nono's "state root
# overlaps an allowed path" guard.
RUN echo 'agent:x:65532:65532:agent:/home/agent:/usr/sbin/nologin' >> /etc/passwd && \
    echo 'agent:x:65532:' >> /etc/group && \
    mkdir -p /home/agent /workspace && \
    chown -R 65532:65532 /home/agent /workspace && \
    chmod 700 /home/agent
ENV HOME=/home/agent

# Writable workspace (the only path nono's profile allows writes to).
WORKDIR /workspace
USER 65532:65532

# Default model/provider config can be supplied via env at runtime.
# Verify the toolchain at build time is left to CI; keep the image entrypoint
# generic so the Deployment can wrap it in `nono run -- ...`.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
