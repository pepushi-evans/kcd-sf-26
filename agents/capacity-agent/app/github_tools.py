"""GitHub tools for the capacity agent.

The only write path the agent has, and it's not a kubectl verb: propose a
pull request against the Flux-synced repo. Under nono, GITHUB_TOKEN in this
process is a phantom token — the sandbox proxy swaps in the real credential
toward api.github.com, and its L7 rules only match /repos/<owner>/** for the
agent's own account, so even a prompt-injected agent can't touch other repos.

httpx honors HTTPS_PROXY and SSL_CERT_FILE from the environment, which is
exactly what nono's proxy injection needs; outside nono the same code talks
to GitHub directly with a real token.
"""
import base64
import os
import re
import shutil
import subprocess
import time

import httpx

GITHUB_API = os.environ.get("GITHUB_API", "https://api.github.com")
REPO = os.environ.get("GITHUB_REPO", "pepushi-evans/kcd-sf-26")
BRANCH = os.environ.get("GITHUB_BASE_BRANCH", "main")
HPA_FILE = os.environ.get("HPA_FILE_PATH", "apps/checkout/hpa.yaml")
BRANCH_PREFIX = "capacity/"
DRY_RUN = os.environ.get("DRY_RUN", "") not in ("", "0", "false")


def _client() -> httpx.Client:
    token = os.environ.get("GITHUB_TOKEN", "")
    return httpx.Client(
        base_url=GITHUB_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )


def list_open_capacity_prs() -> dict:
    """List open pull requests already proposing a capacity change.

    Always call this first: if a capacity PR is already open, do NOT open
    another one — report the existing PR instead.
    """
    with _client() as c:
        r = c.get(f"/repos/{REPO}/pulls", params={"state": "open"})
        r.raise_for_status()
        prs = [
            {"number": p["number"], "title": p["title"], "url": p["html_url"],
             "branch": p["head"]["ref"]}
            for p in r.json()
            if p["head"]["ref"].startswith(BRANCH_PREFIX)
        ]
    return {"open_capacity_prs": prs}


def get_hpa_manifest() -> dict:
    """Fetch the HPA manifest (the file a capacity PR would modify) from the
    repo's main branch, returning its text and current maxReplicas."""
    with _client() as c:
        r = c.get(f"/repos/{REPO}/contents/{HPA_FILE}", params={"ref": BRANCH})
        r.raise_for_status()
        data = r.json()
    text = base64.b64decode(data["content"]).decode()
    m = re.search(r"(?m)^\s*maxReplicas:\s*(\d+)", text)
    return {
        "path": HPA_FILE,
        "max_replicas_in_git": int(m.group(1)) if m else None,
        "content": text,
    }


def _schema_check(manifest_text: str) -> str | None:
    """Validate a manifest with the Flux project's flux-schema plugin
    (the same gate CI runs on every commit). Returns an error summary if
    invalid, None if valid. The sandbox only allows GETs to the Flux schema
    catalog for this — the agent checks its own work before proposing it."""
    exe = shutil.which("flux-schema")
    if exe is None:
        return None  # local dev without the plugin; CI still gates the PR
    r = subprocess.run(
        [exe, "validate", "--schema-location", "default"],
        input=manifest_text.encode(),
        capture_output=True,
        timeout=120,
    )
    if r.returncode == 0:
        return None
    return (r.stdout or r.stderr).decode(errors="replace").strip()[:800]


def open_capacity_pr(new_max_replicas: int, title: str, body: str) -> dict:
    """Open a pull request that raises maxReplicas in the HPA manifest.

    Only maxReplicas is changed — the file is edited surgically so review
    diffs stay one line. `body` should carry the full capacity analysis:
    observed HPA state, node headroom math, and why this new value is safe.
    Returns the PR URL (or the would-be change when DRY_RUN is set).
    """
    manifest = get_hpa_manifest()
    old = manifest["max_replicas_in_git"]
    if old is None:
        return {"error": f"could not find maxReplicas in {HPA_FILE}"}
    if int(new_max_replicas) <= old:
        return {"error": f"new_max_replicas ({new_max_replicas}) must exceed current ({old})"}
    new_text = re.sub(
        r"(?m)^(\s*maxReplicas:\s*)\d+",
        rf"\g<1>{int(new_max_replicas)}",
        manifest["content"],
        count=1,
    )
    schema_error = _schema_check(new_text)
    if schema_error:
        return {"error": f"proposed manifest failed flux-schema validation: {schema_error}"}
    branch = f"{BRANCH_PREFIX}checkout-max-{new_max_replicas}-{int(time.time())}"
    if DRY_RUN:
        return {
            "dry_run": True, "branch": branch, "title": title,
            "change": f"maxReplicas: {old} -> {new_max_replicas}",
        }
    with _client() as c:
        r = c.get(f"/repos/{REPO}/git/ref/heads/{BRANCH}")
        r.raise_for_status()
        base_sha = r.json()["object"]["sha"]
        r = c.post(
            f"/repos/{REPO}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        r.raise_for_status()
        r = c.put(
            f"/repos/{REPO}/contents/{HPA_FILE}",
            json={
                "message": f"checkout: raise HPA maxReplicas {old} -> {new_max_replicas}",
                "content": base64.b64encode(new_text.encode()).decode(),
                "sha": get_file_sha(c, branch),
                "branch": branch,
            },
        )
        r.raise_for_status()
        r = c.post(
            f"/repos/{REPO}/pulls",
            json={"title": title, "body": body, "head": branch, "base": BRANCH},
        )
        r.raise_for_status()
        pr = r.json()
    return {"pull_request": pr["html_url"], "number": pr["number"],
            "change": f"maxReplicas: {old} -> {new_max_replicas}"}


def get_file_sha(c: httpx.Client, ref: str) -> str:
    r = c.get(f"/repos/{REPO}/contents/{HPA_FILE}", params={"ref": ref})
    r.raise_for_status()
    return r.json()["sha"]
