"""Contracts for the temporary, same-repository XPU-only validation PR."""

import json
import os
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
CI = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


class ForkValidationTest(unittest.TestCase):
    def test_only_xpu_jobs_are_active_and_unrelated_jobs_remain_commented(self):
        jobs = CI["jobs"]
        self.assertEqual(
            set(jobs),
            {
                "xpu-script-tests",
                "build-wheel-xpu",
                "xpu-hardware-core",
                "xpu-validation-gate",
            },
        )
        self.assertEqual(jobs["build-wheel-xpu"]["needs"], "xpu-script-tests")
        self.assertEqual(jobs["xpu-hardware-core"]["needs"], "build-wheel-xpu")
        self.assertEqual(
            jobs["xpu-validation-gate"]["needs"],
            ["xpu-script-tests", "build-wheel-xpu", "xpu-hardware-core"],
        )
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        for job in (
            "build-wheel",
            "unit-tests",
            "spell-check",
            "clang-format",
            "ci-gate",
        ):
            self.assertIn(f"#   {job}:", text)
        macos = yaml.safe_load((ROOT / ".github/workflows/ci-macos.yml").read_text())
        self.assertNotIn("pull_request", macos.get("on", macos.get(True)))
        self.assertEqual(
            jobs["build-wheel-xpu"]["with"]["checkout_ref"],
            "${{ github.event.pull_request.head.sha || github.sha }}",
        )
        for name in ("source_sha", "artifact_id", "artifact_digest"):
            self.assertEqual(
                jobs["xpu-hardware-core"]["with"][name],
                "${{ needs.build-wheel-xpu.outputs." + name + " }}",
            )
        hardware = yaml.safe_load(
            (ROOT / ".github/workflows/integration-test-xpu.yml").read_text()
        )
        self.assertEqual(hardware["jobs"]["core"]["runs-on"], "xpu")

    def test_hardware_entry_rejects_foreign_forks_and_other_branches(self):
        condition = CI["jobs"]["xpu-script-tests"]["if"]
        for job in ("build-wheel-xpu", "xpu-hardware-core"):
            self.assertEqual(CI["jobs"][job]["if"], condition)
        base = {
            "repository": "VincyZhang/Mooncake",
            "event_name": "pull_request",
            "ref": "refs/pull/2/merge",
            "event": {
                "pull_request": {
                    "head": {
                        "repo": {"full_name": "VincyZhang/Mooncake"},
                        "ref": "ci/xpu-stage-2-only",
                    }
                }
            },
        }
        cases = [
            (base, True),
            ({**base, "repository": "kvcache-ai/Mooncake"}, False),
            (
                {
                    **base,
                    "event": {
                        "pull_request": {
                            "head": {
                                "repo": {"full_name": "someone/Mooncake"},
                                "ref": "ci/xpu-stage-2-only",
                            }
                        }
                    },
                },
                False,
            ),
            (
                {
                    **base,
                    "event": {
                        "pull_request": {
                            "head": {
                                "repo": {"full_name": "VincyZhang/Mooncake"},
                                "ref": "untrusted",
                            }
                        }
                    },
                },
                False,
            ),
            (
                {
                    **base,
                    "event_name": "workflow_dispatch",
                    "ref": "refs/heads/ci/xpu-stage-2-only",
                },
                True,
            ),
            (
                {**base, "event_name": "workflow_dispatch", "ref": "refs/heads/main"},
                False,
            ),
            ({**base, "event_name": "push"}, False),
        ]
        for context, expected in cases:
            with self.subTest(context=context):
                result = subprocess.run(
                    [
                        "node",
                        "-e",
                        f"const github = JSON.parse(process.env.CONTEXT); console.log(Boolean({condition}));",
                    ],
                    env={**os.environ, "CONTEXT": json.dumps(context)},
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(expected).lower())

    def test_gate_requires_every_job_and_current_head_and_build_sha(self):
        gate = CI["jobs"]["xpu-validation-gate"]
        self.assertIn("always()", gate["if"])
        self.assertIn(CI["jobs"]["xpu-script-tests"]["if"], gate["if"])
        script = gate["steps"][0]["with"]["script"]
        sha = "a" * 40
        harness = """
        const context = {repo: {owner: 'VincyZhang', repo: 'Mooncake'},
          payload: {pull_request: {number: 2}}};
        const github = {rest: {pulls: {get: async () => ({data: {
          head: {sha: process.env.CURRENT_SHA}
        }})}}};
        const core = {
          summary: {addRaw() {return this;}, async write() {}},
          setFailed(message) {throw new Error(message);}
        };
        (async () => { SCRIPT })()
          .catch(error => {console.error(error.message); process.exitCode = 1;});
        """
        for job, result, head, built, valid in (
            ("xpu-hardware-core", "success", sha, sha, True),
            ("xpu-hardware-core", "failure", sha, sha, False),
            ("xpu-hardware-core", "skipped", sha, sha, False),
            ("xpu-hardware-core", "cancelled", sha, sha, False),
            ("xpu-script-tests", "skipped", sha, sha, False),
            ("build-wheel-xpu", "failure", sha, sha, False),
            ("xpu-hardware-core", "success", "b" * 40, sha, False),
            ("xpu-hardware-core", "success", sha, "b" * 40, False),
            ("xpu-hardware-core", "success", sha, "", False),
        ):
            needs = {
                name: {"result": "success", "outputs": {}} for name in gate["needs"]
            }
            needs[job]["result"] = result
            needs["build-wheel-xpu"]["outputs"]["source_sha"] = built
            with self.subTest(job=job, result=result, head=head, built=built):
                completed = subprocess.run(
                    ["node", "-e", harness.replace("SCRIPT", script)],
                    env={
                        **os.environ,
                        "NEEDS_JSON": json.dumps(needs),
                        "EXPECTED_SHA": sha,
                        "CURRENT_SHA": head,
                    },
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(completed.returncode == 0, valid, completed.stderr)


if __name__ == "__main__":
    unittest.main()
