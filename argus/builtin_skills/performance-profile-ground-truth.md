---
name: "Performance Profile Ground Truth"
description: "定位真实工作负载的性能瓶颈。 Profile a requested workload with its configured environment and representative inputs; compare unprofiled measurements, inspect the limiting factor and stop before unrequested optimization."
---

# Performance Profile Ground Truth

Use when the task asks for measurements or diagnosis before optimization. Start
from existing valid measurements when sufficient. Do not turn a performance task
into a mandatory full test-suite run or assume Python/pytest is its workload.

1. Identify the actual operation, inputs, environment, comparison conditions and
   allowed instrumentation. Preserve production behavior when profiling only.
2. Verify the smallest correctness/setup condition needed for measurement, using
   the project's configured interpreter/runtime and lockfile.
3. Measure the unprofiled workload first. Record warmup, repetitions and variation
   as appropriate. A profiler changes execution cost, so its time is not itself
   the baseline. Include synchronization for asynchronous accelerator work.
4. Choose the matching profiler: for example cProfile for Python call costs, a
   native sampler for native CPU work, the allocated accelerator's profiler for
   GPU kernels, or request traces for service latency. Avoid unrelated full-suite
   work when a representative operation answers the question.
5. Inspect the strongest observed constraint: compute, memory/data movement, I/O,
   subprocess startup, synchronization or test setup. Separate observations from
   hypotheses and use the cheapest additional measurement when attribution is unclear.
6. Preserve the command, relevant versions, raw measurements and readable profiler
   output in the existing task artifacts. Produce a separate report only if asked
   or needed to communicate the result; do not duplicate the project record.

Python example, **only for a Python workload** and after choosing its interpreter:

```bash
/path/to/project/python -m cProfile -o profile.prof workload.py
/path/to/project/python -c 'import pstats; pstats.Stats("profile.prof").sort_stats("cumulative").print_stats(25)'
```

Report measured facts, uncertainty and the next useful action. Missing dependencies,
insufficient permissions or an impractical workload are concrete limits, not evidence
that another interpreter/workload represents this task. Do not edit project stages,
patch the evaluator or optimize production code as part of a profiling-only request.
