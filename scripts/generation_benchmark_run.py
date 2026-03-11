#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any
from urllib.parse import urlencode

import httpx


DEFAULT_SAMPLE_TEXT = """Telegram Mini App benchmark sample.

Neural networks learn patterns from examples.
A transformer model uses attention over tokens.
Spaced repetition helps long-term retention in memory.
Anki cards are effective when questions are atomic and specific.
For quality checks, each generated card should have evidence and source links.
"""


def to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    low = int(rank)
    high = min(len(ordered) - 1, low + 1)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "count": float(len(values)),
        "mean": mean(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "min": min(values),
        "max": max(values),
    }


def make_init_data(bot_token: str, user_id: int) -> str:
    user = {
        "id": user_id,
        "first_name": "Benchmark",
        "username": f"bench_{user_id}",
        "language_code": "en",
        "is_premium": False,
    }
    data = {
        "auth_date": str(int(time.time())),
        "query_id": f"bench-{uuid.uuid4().hex[:12]}",
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret_key, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(data)


@dataclass
class RunResult:
    run_index: int
    topic_id: str
    job_id: str
    status: str
    error_message: str | None
    metrics: dict[str, Any]


class ApiClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.token: str | None = None

    def close(self) -> None:
        self.client.close()

    def set_token(self, token: str) -> None:
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(self._headers())
        headers.update(kwargs.pop("headers", {}))
        response = self.client.post(path, headers=headers, **kwargs)
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(self._headers())
        headers.update(kwargs.pop("headers", {}))
        response = self.client.get(path, headers=headers, **kwargs)
        return response

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(self._headers())
        headers.update(kwargs.pop("headers", {}))
        response = self.client.delete(path, headers=headers, **kwargs)
        return response


def auth(client: ApiClient, bot_token: str, user_id: int) -> str:
    init_data = make_init_data(bot_token, user_id=user_id)
    response = client.post("/auth/telegram", json={"init_data": init_data})
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Auth response has no access_token")
    return token


def create_topic(client: ApiClient, title: str) -> str:
    response = client.post("/topics/", json={"title": title})
    response.raise_for_status()
    payload = response.json()
    topic_id = payload.get("id")
    if not isinstance(topic_id, str):
        raise RuntimeError("Topic create response has no id")
    return topic_id


def upload_file(client: ApiClient, topic_id: str, filename: str, content: bytes) -> str:
    response = client.post(
        f"/topics/{topic_id}/files/",
        files={"file": (filename, content, "text/plain")},
    )
    response.raise_for_status()
    payload = response.json()
    file_id = payload.get("id")
    if not isinstance(file_id, str):
        raise RuntimeError("File upload response has no id")
    return file_id


def start_job(client: ApiClient, topic_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"/topics/{topic_id}/jobs/", json=payload)
    response.raise_for_status()
    job = response.json()
    if not isinstance(job, dict) or "id" not in job:
        raise RuntimeError("Job create response is invalid")
    return job


def get_job(client: ApiClient, topic_id: str, job_id: str) -> dict[str, Any]:
    response = client.get(f"/topics/{topic_id}/jobs/{job_id}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Job status response is invalid")
    return payload


def delete_topic(client: ApiClient, topic_id: str) -> None:
    response = client.delete(f"/topics/{topic_id}")
    if response.status_code not in {200, 204, 404}:
        response.raise_for_status()


def load_input_files(paths: list[str]) -> list[tuple[str, bytes]]:
    if not paths:
        return [("benchmark_input.txt", DEFAULT_SAMPLE_TEXT.encode("utf-8"))]
    files: list[tuple[str, bytes]] = []
    for path in paths:
        with open(path, "rb") as handle:
            files.append((os.path.basename(path), handle.read()))
    return files


def collect_summary(results: list[RunResult]) -> dict[str, Any]:
    done = [item for item in results if item.status == "done"]
    e2e_seconds: list[float] = []
    qps: list[float] = []
    quality_score: list[float] = []
    source_coverage: list[float] = []
    llm_latency_ms: list[float] = []
    for result in done:
        metrics = result.metrics
        value = to_float(metrics.get("total_elapsed_sec"))
        if value is not None:
            e2e_seconds.append(value)
        value = to_float(metrics.get("throughput_qps_end_to_end"))
        if value is not None:
            qps.append(value)
        value = to_float(metrics.get("quality_score"))
        if value is not None:
            quality_score.append(value)
        value = to_float(metrics.get("source_coverage_ratio"))
        if value is not None:
            source_coverage.append(value)
        llm = None
        agent_metrics = metrics.get("agent_metrics")
        if isinstance(agent_metrics, dict):
            llm_raw = agent_metrics.get("llm")
            if isinstance(llm_raw, dict):
                llm = llm_raw
        if isinstance(llm, dict):
            value = to_float(llm.get("latency_avg_sec"))
            if value is not None:
                llm_latency_ms.append(value * 1000.0)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_total": len(results),
        "runs_done": len(done),
        "runs_failed": len([item for item in results if item.status == "failed"]),
        "runs_cancelled": len([item for item in results if item.status == "cancelled"]),
        "e2e_seconds": summarize(e2e_seconds),
        "throughput_qps_end_to_end": summarize(qps),
        "quality_score": summarize(quality_score),
        "source_coverage_ratio": summarize(source_coverage),
        "llm_latency_ms": summarize(llm_latency_ms),
        "runs": [
            {
                "run_index": item.run_index,
                "topic_id": item.topic_id,
                "job_id": item.job_id,
                "status": item.status,
                "error_message": item.error_message,
                "metrics": item.metrics,
            }
            for item in results
        ],
    }


def print_summary(summary: dict[str, Any]) -> None:
    def fmt(stat: dict[str, Any], unit: str) -> str:
        if not stat:
            return "n/a"
        return (
            f"mean={stat['mean']:.3f}{unit}, "
            f"p50={stat['p50']:.3f}{unit}, "
            f"p95={stat['p95']:.3f}{unit}, "
            f"min={stat['min']:.3f}{unit}, "
            f"max={stat['max']:.3f}{unit}"
        )

    print("# Benchmark run summary")
    print(f"- Generated (UTC): {summary['generated_at']}")
    print(f"- Runs: total={summary['runs_total']}, done={summary['runs_done']}, failed={summary['runs_failed']}, cancelled={summary['runs_cancelled']}")
    print(f"- E2E time: {fmt(summary['e2e_seconds'], 's')}")
    print(f"- Throughput: {fmt(summary['throughput_qps_end_to_end'], ' q/s')}")
    print(f"- Quality score: {fmt(summary['quality_score'], '')}")
    print(f"- Source coverage: {fmt(summary['source_coverage_ratio'], '')}")
    print(f"- LLM latency: {fmt(summary['llm_latency_ms'], 'ms')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run end-to-end benchmark generations through API.")
    parser.add_argument("--api-base-url", type=str, default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--bot-token", type=str, default=os.getenv("BOT_TOKEN", ""))
    parser.add_argument("--user-id", type=int, default=987654321)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--questions", type=int, default=20)
    parser.add_argument("--difficulty", type=str, default="medium", choices=["easy", "medium", "hard"])
    parser.add_argument("--mode", type=str, default="merged", choices=["merged", "per_file", "concat"])
    parser.add_argument("--file", dest="files", action="append", default=[])
    parser.add_argument("--poll-interval-sec", type=float, default=3.0)
    parser.add_argument("--timeout-sec", type=float, default=900.0)
    parser.add_argument("--request-timeout-sec", type=float, default=30.0)
    parser.add_argument("--keep-topics", action="store_true")
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    if not args.bot_token:
        raise SystemExit("BOT token is required. Pass --bot-token or set BOT_TOKEN env var.")

    files = load_input_files(args.files)
    payload = {
        "mode": args.mode,
        "number_of_questions": int(args.questions),
        "difficulty": args.difficulty,
        "avoid_repeats": True,
        "include_answers": True,
    }

    client = ApiClient(base_url=args.api_base_url, timeout=args.request_timeout_sec)
    try:
        token = auth(client, args.bot_token, user_id=args.user_id)
        client.set_token(token)
        print(f"Authenticated user {args.user_id}. Starting benchmark runs: {args.runs}")

        results: list[RunResult] = []
        for run_idx in range(1, max(1, args.runs) + 1):
            topic_id = ""
            job_id = ""
            try:
                topic_title = f"Benchmark {datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-r{run_idx}"
                topic_id = create_topic(client, topic_title)
                for filename, content in files:
                    upload_file(client, topic_id, filename, content)

                job = start_job(client, topic_id, payload=payload)
                job_id = str(job["id"])
                print(f"[run {run_idx}] topic={topic_id} job={job_id} started")

                started = time.time()
                last_progress = None
                last_stage = None
                while True:
                    current = get_job(client, topic_id, job_id)
                    status = str(current.get("status", ""))
                    progress = current.get("progress")
                    stage = current.get("stage")
                    if progress != last_progress or stage != last_stage:
                        print(f"[run {run_idx}] status={status} stage={stage} progress={progress}")
                        last_progress = progress
                        last_stage = stage

                    if status in {"done", "failed", "cancelled"}:
                        results.append(
                            RunResult(
                                run_index=run_idx,
                                topic_id=topic_id,
                                job_id=job_id,
                                status=status,
                                error_message=current.get("error_message"),
                                metrics=current.get("metrics_json") if isinstance(current.get("metrics_json"), dict) else {},
                            )
                        )
                        break

                    if (time.time() - started) > args.timeout_sec:
                        results.append(
                            RunResult(
                                run_index=run_idx,
                                topic_id=topic_id,
                                job_id=job_id,
                                status="failed",
                                error_message=f"Timeout after {args.timeout_sec}s",
                                metrics={},
                            )
                        )
                        break
                    time.sleep(max(0.5, args.poll_interval_sec))
            except Exception as exc:
                results.append(
                    RunResult(
                        run_index=run_idx,
                        topic_id=topic_id,
                        job_id=job_id,
                        status="failed",
                        error_message=str(exc),
                        metrics={},
                    )
                )
            finally:
                if topic_id and not args.keep_topics:
                    try:
                        delete_topic(client, topic_id)
                    except Exception:
                        pass

        summary = collect_summary(results)
        print_summary(summary)
        if args.json_out:
            out_dir = os.path.dirname(args.json_out)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, ensure_ascii=False, indent=2)
            print(f"Saved JSON summary to {args.json_out}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
