"""Run related PC checks; incomplete checks and stale release evidence fail closed."""

import argparse
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "design" / "checks" / "rules" / "checklist.json"
OUTPUT = ROOT / "_temp" / "regression-runs"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    ids = [test["id"] for test in policy["tests"]]
    if policy["schema"] != 1 or len(ids) != len(set(ids)) or not ids:
        raise ValueError("Invalid or duplicate tests")
    if not policy["core"] or not set(policy["core"]).issubset(ids):
        raise ValueError("Invalid core tests")
    for test in policy["tests"]:
        if not (ROOT / test["command"][0]).is_file() or test["timeout"] <= 0:
            raise ValueError("Missing script or invalid timeout: " + test["id"])
        if not (test.get("markers") or test.get("unittest_min")):
            raise ValueError("Missing completion rule: " + test["id"])
    for rule in policy["rules"]:
        if rule["tests"] != "all" and not set(rule["tests"]).issubset(ids):
            raise ValueError("Unknown related test")
    manual = [item["id"] for item in policy["manual"]]
    if not manual or len(manual) != len(set(manual)):
        raise ValueError("Missing or duplicate manual checks")
    return policy


def select_tests(policy, paths, profile):
    all_ids = {test["id"] for test in policy["tests"]}
    chosen = set(policy["core"])
    reasons = []
    if profile == "full":
        chosen = all_ids
    elif profile == "changed":
        for path in paths:
            matched = False
            for rule in policy["rules"]:
                if any(fnmatchcase(path, pattern) for pattern in rule["paths"]):
                    matched = True
                    chosen.update(all_ids if rule["tests"] == "all" else rule["tests"])
                    reasons.append({"path": path, "reason": rule["reason"]})
            if not matched:
                chosen.update(all_ids)
                reasons.append({"path": path, "reason": "Unknown path: run all checks"})
    return [test for test in policy["tests"] if test["id"] in chosen], reasons


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def changed_paths(base):
    # Disable rename detection so both the old and new ownership paths are considered.
    tracked = git("diff", "--name-only", "--no-renames", "-z", base, "--").split("\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted({path for path in tracked + untracked if path})


def fingerprint(root=ROOT):
    files = []
    for name in ("main.py", "buildozer.spec", "README.md", "AGENTS.md", "CLAUDE.md",
                 ".gitignore", ".gitattributes"):
        if (root / name).is_file():
            files.append(root / name)
    for folder in ("src", "sounds", "ui", "design", "docs", ".github", ".githooks"):
        for path in (root / folder).rglob("*"):
            relative = path.relative_to(root)
            if path.is_file() and "__pycache__" not in relative.parts:
                if relative.parts[:2] not in (("sounds", "source"), ("docs", "generated")):
                    files.append(path)
    content = [(path.relative_to(root).as_posix(), digest(path)) for path in sorted(files)]
    return hashlib.sha256(json.dumps(content, ensure_ascii=True).encode()).hexdigest()


def validate_result(test, returncode, text, timed_out=False):
    errors = []
    if timed_out:
        errors.append("timeout")
    if returncode != 0:
        errors.append("exit=%s" % returncode)
    if re.search(r"Traceback \(most recent call last\)|^FAIL(?:ED|URE|:|\s|$)",
                 text, re.MULTILINE):
        errors.append("exception or failed assertion")
    for marker in test.get("markers", []):
        if marker not in text:
            errors.append("missing completion: " + marker)
    if test.get("unittest_min"):
        count = re.search(r"Ran (\d+) tests? in", text)
        if not count or int(count[1]) < test["unittest_min"]:
            errors.append("too few unit tests")
        if not re.search(r"^OK\s*$", text, re.MULTILINE):
            errors.append("missing successful unittest completion")
    if re.search(r"\bskipped[= ]\d+|^SKIP\b", text, re.MULTILINE | re.IGNORECASE):
        errors.append("skipped checks")
    if len(re.findall(r"^PASS ", text, re.MULTILINE)) < test.get("pass_min", 0):
        errors.append("too few passed assertions")
    return errors


def run_test(test, run, env):
    command = [sys.executable] + [
        item.replace("{run}", str(run)).replace("{label}", run.name)
        for item in test["command"]]
    started = time.monotonic()
    log = run / (test["id"] + ".log")
    timed_out = False
    with log.open("w", encoding="utf-8") as stream:
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                   else {"start_new_session": True})
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                 stderr=subprocess.STDOUT, **options)
        try:
            child.wait(timeout=test["timeout"])
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            timed_out = True
            # Kill the whole owned tree, including renderer helper processes.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                               capture_output=True)
                if child.poll() is None:
                    child.kill()
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    errors = validate_result(test, child.returncode, log.read_text(encoding="utf-8"), timed_out)
    return {"id": test["id"], "status": "FAIL" if errors else "PASS",
            "returncode": child.returncode, "timed_out": timed_out, "errors": errors,
            "seconds": round(time.monotonic() - started, 2), "command": command,
            "log": log.relative_to(ROOT).as_posix(), "log_sha256": digest(log)}


def review_errors(review, expected, manual, root=ROOT):
    errors = []
    if not isinstance(review, dict) or review.get("fingerprint") != expected:
        return ["missing or stale manual review"]
    items = review.get("items", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        return ["invalid manual review items"]
    if sorted(item.get("id", "") for item in items) != sorted(item["id"] for item in manual):
        return ["missing or duplicate manual review items"]
    for item in items:
        if item.get("status") != "PASS" or not item.get("reviewer", "").strip():
            errors.append("manual check pending: " + item["id"])
        evidence = item.get("evidence", [])
        if not evidence:
            errors.append("no evidence: " + item["id"])
        for entry in evidence:
            try:
                path = (root / entry["path"]).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    raise ValueError("missing or out-of-project evidence")
                if path.stat().st_size == 0 or digest(path) != entry["sha256"]:
                    raise ValueError("changed or empty evidence")
            except (KeyError, TypeError, ValueError, OSError) as error:
                errors.append(item["id"] + ": " + str(error))
    return errors


def report_errors(report, policy, expected, root=ROOT):
    errors = []
    if (report.get("schema") != 1 or report.get("profile") != "full"
            or report.get("automation_only") or report.get("fingerprint") != expected):
        errors.append("not a current full release report")
    results = report.get("results", [])
    tests = {test["id"]: test for test in policy["tests"]}
    if sorted(row["id"] for row in results) != sorted(tests):
        return errors + ["missing, duplicate or extra automatic checks"]
    for row in results:
        try:
            log = (root / row["log"]).resolve()
            if not log.is_relative_to(root.resolve()) or digest(log) != row["log_sha256"]:
                raise ValueError("missing or modified log")
            problems = validate_result(tests[row["id"]], row["returncode"],
                                       log.read_text(encoding="utf-8"), row["timed_out"])
            if problems or row["status"] != "PASS":
                errors.append("automatic check failed: " + row["id"])
        except (ValueError, OSError) as error:
            errors.append(row["id"] + ": " + str(error))
    errors += review_errors(report.get("review"), expected, policy["manual"], root)
    return errors


def static_check():
    from pyflakes.api import checkPath
    from pyflakes.reporter import Reporter
    import io
    output, errors = io.StringIO(), io.StringIO()
    reporter = Reporter(output, errors)
    paths = [ROOT / "main.py"] + sorted((ROOT / "src").rglob("*.py"))
    for path in paths:
        checkPath(str(path), reporter)
    print(output.getvalue(), end="")
    print(errors.getvalue(), end="")
    if errors.getvalue() or "undefined name" in output.getvalue():
        return 1
    print("STATIC_DONE files=%s" % len(paths))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("core", "changed", "full"), default="changed")
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--automation-only", action="store_true")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--check-report", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--static-check", action="store_true")
    args = parser.parse_args()
    if args.static_check:
        return static_check()
    policy = load_policy()
    current = fingerprint()
    if args.check_report:
        report = json.loads(args.check_report.read_text(encoding="utf-8"))
        errors = report_errors(report, policy, current)
        if args.require_clean:
            if git("status", "--porcelain"):
                errors.append("worktree is not clean; commit then run full checks")
            if report.get("head") != git("rev-parse", "HEAD"):
                errors.append("report does not belong to pushed HEAD")
        print("\n".join(errors) if errors else "RELEASE_REPORT_OK")
        return 1 if errors else 0
    paths = changed_paths(args.base) if args.profile == "changed" else []
    tests, reasons = select_tests(policy, paths, args.profile)
    print("Plan:", ", ".join(test["id"] for test in tests), flush=True)
    if args.plan:
        print(json.dumps(reasons, ensure_ascii=False, indent=2))
        return 0
    run = OUTPUT / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    run.mkdir(parents=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HG_", "KIVY_"))}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               PYTHONPYCACHEPREFIX=str(ROOT / "_temp" / "pycache"))
    report = {"schema": 1, "profile": args.profile, "fingerprint": current,
              "head": git("rev-parse", "HEAD"), "automation_only": args.automation_only,
              "at": datetime.now(timezone.utc).isoformat(), "python": sys.version,
              "platform": platform.platform(), "paths": paths, "reasons": reasons, "results": []}
    review = (json.loads(args.review.read_text(encoding="utf-8")) if args.review else None)
    pending = review_errors(review, current, policy["manual"])
    template = {"fingerprint": current, "items": [
        {"id": item["id"], "description": item["description"], "status": "pending",
         "reviewer": "", "evidence": []} for item in policy["manual"]]}
    (run / "review-template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    for test in tests:
        print("RUN", test["id"], flush=True)
        try:
            row = run_test(test, run, env)
        except OSError as error:
            row = {"id": test["id"], "status": "FAIL", "errors": [str(error)]}
        report["results"].append(row)
        print(row["status"], row["id"], "; ".join(row["errors"]), flush=True)
    automatic_ok = all(row["status"] == "PASS" for row in report["results"])
    unchanged = fingerprint() == current
    report.update(review=review, manual_pending=pending, unchanged=unchanged,
                  automatic_ok=automatic_ok,
                  release_ready=(args.profile == "full" and not args.automation_only
                                 and automatic_ok and unchanged and not pending))
    destination = run / "report.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["release_ready"]:
        (OUTPUT / "latest-release.json").write_text(destination.read_text(encoding="utf-8"),
                                                   encoding="utf-8")
    print("REGRESSION_DONE automatic=%s/%s release_ready=%s report=%s" %
          (sum(row["status"] == "PASS" for row in report["results"]), len(tests),
           report["release_ready"], destination), flush=True)
    return 0 if automatic_ok and unchanged and (
        args.profile != "full" or args.automation_only or not pending) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print("GATE_ERROR:", error)
        raise SystemExit(1)
