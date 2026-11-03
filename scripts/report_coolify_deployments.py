"""Observe Coolify deployments and mirror their actual status to GitHub."""

import fnmatch
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

COOLIFY = "https://richard-coolify.duckdns.org"
SITE = "https://stock.107.149.104.100.sslip.io/"
BACKEND = ["src/**", "migrations/**", "pyproject.toml", "uv.lock", "Dockerfile"]
SERVICES = {
    "api": ("mkdmai628svxojzksohdgglc", BACKEND),
    "worker": ("ghr0av2w6b6ooaeifuntypam", BACKEND),
    "mcp": ("0o4u9do7tjy3gunokrezu5jp", BACKEND),
    "frontend": ("bwye09ddsuzwwuhljm4aktel", ["frontend/**"]),
    "kronos": ("jkdsjng5eoaixv8h8ihogatz", ["kronos-service/**"]),
    "gateway": (
        "0ytyhezcydrzg593nzpslkqs",
        [
            "deploy/coolify/split/nginx.conf",
            "deploy/coolify/split/Dockerfile.gateway",
        ],
    ),
}


def request(base, path, token, data=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        base + path,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "stock-coolify-reporter",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(5)
    raise RuntimeError("Request retry exhausted")


def affected(name, paths):
    patterns = SERVICES[name][1] + [f"deploy/coolify/split/{name}.yml"]
    return any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)


def main():
    event = json.loads(open(os.environ["GITHUB_EVENT_PATH"]).read())
    manual = os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch"
    sha = os.environ["GITHUB_SHA"]
    paths = []
    if not manual:
        before = event["before"]
        if before == "0" * 40:
            paths = subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", sha], text=True
            ).splitlines()
        else:
            paths = subprocess.check_output(
                ["git", "diff", "--name-only", before, sha], text=True
            ).splitlines()
    selected = [name for name in SERVICES if manual or affected(name, paths)]
    if not selected:
        print("No deployed service changed; no deployment records created.")
        return
    gh_base = os.environ["GITHUB_API_URL"] + "/repos/" + os.environ["GITHUB_REPOSITORY"]
    coolify_token = os.environ["COOLIFY_API_TOKEN"]
    gh_token = os.environ["GH_TOKEN"]
    run_url = (
        os.environ["GITHUB_SERVER_URL"]
        + "/"
        + os.environ["GITHUB_REPOSITORY"]
        + "/actions/runs/"
        + os.environ["GITHUB_RUN_ID"]
    )
    pending = {name: {"id": None, "deployment": None} for name in selected}
    started = time.monotonic()
    failures = []
    summary = []

    def status(record, state, description, log=run_url):
        request(
            gh_base,
            f"/deployments/{record['id']}/statuses",
            gh_token,
            {
                "state": state,
                "description": description,
                "log_url": log,
                "environment_url": SITE,
                "auto_inactive": True,
            },
        )

    try:
        while pending:
            for name, record in list(pending.items()):
                uuid = SERVICES[name][0]
                deployments = request(
                    COOLIFY,
                    f"/api/v1/deployments/applications/{uuid}?skip=0&take=100",
                    coolify_token,
                )["deployments"]
                matches = deployments if manual else [d for d in deployments if d["commit"] == sha]
                # Manual reconciliation reports actual deployed commits, never the workflow SHA.
                dep = max(matches, key=lambda d: d["created_at"]) if matches else None
                if record["id"] is None and (dep or not manual):
                    record["id"] = request(
                        gh_base,
                        "/deployments",
                        gh_token,
                        {
                            "ref": dep["commit"] if dep else sha,
                            "environment": f"Coolify / stock-{name}",
                            "auto_merge": False,
                            "required_contexts": [],
                            "production_environment": True,
                            "description": f"Coolify stock-{name} deployment",
                            "payload": {"coolify_application_uuid": uuid},
                        },
                    )["id"]
                    status(record, "in_progress", "Waiting for Coolify deployment")
                elapsed = time.monotonic() - started
                if not dep:
                    if elapsed < 360:
                        continue
                    raise RuntimeError(
                        f"stock-{name}: no Coolify deployment found within 6 minutes"
                    )
                record["deployment"] = dep
                state = dep["status"]
                if state == "finished":
                    result = "success"
                elif state in ("failed", "cancelled", "cancelled-by-user"):
                    result = "failure"
                elif elapsed > 2400:
                    result = "failure"
                    state = "timed out"
                else:
                    continue
                path = dep.get("deployment_url") or ""
                log = COOLIFY + path if path.startswith("/") else run_url
                status(record, result, f"Coolify: {state}", log)
                line = f"- stock-{name}: **{state}**, commit `{dep['commit'][:7]}` ([logs]({log}))"
                print(line, flush=True)
                summary.append(line)
                if result != "success":
                    failures.append(name)
                del pending[name]
            if pending:
                time.sleep(15)
    except Exception:
        for record in pending.values():
            if record["id"]:
                status(record, "failure", "Deployment reporter failed; see Actions logs")
        raise
    finally:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
            output.write("## Coolify deployments\n\n" + "\n".join(summary) + "\n")
    if failures:
        raise RuntimeError("Failed Coolify services: " + ", ".join(failures))


if __name__ == "__main__":
    main()
