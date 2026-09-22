"""Two real processes competing for the same durable learning queue."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.life.answer_learning import learning_status

WORKER = r'''
import json,sys,time
from pathlib import Path
from argus.life import answer_learning,reflection
root=Path(sys.argv[1]); label=sys.argv[2]
(root/'projects'/'s-test').mkdir(parents=True,exist_ok=True)
answer_learning._backend=lambda *args: object()
def reflect(**kw):
    if label=='first':
        (root/'first-started').write_text('ready')
        while True: time.sleep(.02)
    with (root/'completed.jsonl').open('a') as out:
        out.write(json.dumps(kw['reply'])+'\n')
    return {}
reflection.reflect_after_answer=reflect
answer_learning.enqueue_answer(root=root,sid='s-test',operator_text='q',reply=label,
    turn_id=label).join(20)
'''


def _until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("queue did not reach expected state")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-kill recovery exercise")
def test_killed_worker_releases_lease_and_second_process_recovers_both_answers(tmp_path):
    processes = []
    source = str(Path(__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": source, "ARGUS_SKILL_SOURCE_ROOT": source}
    try:
        first = subprocess.Popen([sys.executable, "-c", WORKER, str(tmp_path), "first"], cwd=source, env=env)
        processes.append(first)
        _until(lambda: (tmp_path / "first-started").exists())
        second = subprocess.Popen([sys.executable, "-c", WORKER, str(tmp_path), "second"], cwd=source, env=env)
        processes.append(second)
        _until(lambda: learning_status(tmp_path, "s-test")["pending"] == 2)
        first.kill()
        first.wait(timeout=5)
        assert second.wait(timeout=20) == 0
        status = learning_status(tmp_path, "s-test")
        assert status["pending"] == 0
        assert [json.loads(line) for line in (tmp_path / "completed.jsonl").read_text().splitlines()] == ["first", "second"]
        assert sorted(job["attempts"] for job in status["jobs"]) == [1, 2]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
