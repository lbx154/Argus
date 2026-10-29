# Delivery version replay

Recorded on 2026-10-29 through the built TypeScript server on port 9414, in an isolated ARGUS_HOME. A scripted Codex app-server returned real Manager hand-offs through the regular message API. The workdir files were changed before each request; keep() produced the snapshots and saved copies. Only messages and hand-offs are retained here.

The report was delivered with value 10, changed to 20 with an additional CSV, then delivered unchanged. The binary and single-file HTML projects each delivered two different contents. In the missing-copy project the first saved copy was deleted after the second delivery.

The baseline browser showed value 20 beside the first report's value-10 explanation and two zip buttons for the single HTML file. The copies here are the actual saved text, keyed by their recorded snapshot version.


`model-candidates.json` records the Reviewer’s model-resource regression through the same message and hand-off flow: first deliver `robot/robot.urdf`, `robot/mesh.stl` and `mesh.stl`, then update only the root mesh. The URDF references `package://robot/mesh.stl`. Its unchanged mesh reads directly with HTTP 200, but eager candidate evaluation originally reached the unrelated root mesh and returned HTTP 410. The fixture retains the actual hand-offs, saved URDF, binary STL bytes in base64 and modification times in Unix seconds so the test can restore the recorded metadata exactly. The test also invalidates the chosen mesh while restoring the later candidate, to verify that the chosen mesh’s own HTTP 410 remains explicit.
