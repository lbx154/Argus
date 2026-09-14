# Optional plugin task roots and Windows PLATON

This change builds on draft PR #119's optional plugin API. It does not modify or repackage the separately licensed CrystalPilot wheel.

## Separate installation identity from task state

`ARGUS_WORKBENCH_HOST_ROOT` identifies the trusted host installation/registry. `ARGUS_SKILL_HOME` may identify a plugin workbench's independent task namespace. Desktop launch and clean daemon spawning pin the former before task bootstrap changes the latter; CLI descendants inherit it. Hosted trial profile lookup also remains under the host account root rather than copying credentials into a workbench.

Catalog plugin names and reserved `s-<plugin>-...` session namespaces are explicit identities, not keyword classifiers. An unavailable plugin or missing workdir binding must reject execution. A workbench record already routed to a different vertical is retained and rejected, not silently rewritten or resumed as research. Native conversations may still add optional tools while keeping their original vertical.

A refused mission emits a failed completion event with a blocked outcome so the workbench can leave its pending state. The backlog uses its existing `failed` status; an unknown `blocked` status would normalize to pending and cause repeated execution attempts.

## Official PLATON Windows environment

The plugin center offers **Prepare official PLATON runtime** on Windows. The operator must confirm compliance with the publisher's terms: acknowledged academic, scientific and non-commercial use, or separately authorized commercial use. No scientific software or license credential is bundled in this PR.

The host downloads checksum-pinned publisher artifacts:

- PLATON executable: <https://www.chem.gla.ac.uk/~louis/software/platon/platon.zip>
- Official Windows Taskbar/runtime: <https://www.chem.gla.ac.uk/~louis/software/platon/pwt_setup.zip>
- innoextract: <https://constexpr.org/innoextract/>, Zlib license.

`setup.exe` is only archive input to the verified extractor. The host deploys the unmodified official `platon.exe` and `salflibc.dll` into a plugin-private directory; it does not run a system installer, change the registry/global PATH, or download individual DLLs from mirrors. Runtime bytes and PE architecture are verified before activation. An existing installation is retained on failure.

The publisher's taskbar uses the `+00` switch to close PLATON's completion dialog. Without it, `-z2` can successfully generate `check.def` but leave the Windows process alive, causing unattended scientific probes to time out. The small first-party `platon-headless.exe` adapter forwards native argument boundaries, adds that switch, and returns the actual exit status. It never uses a shell or kills a process to declare success.

The verified result is selected through the published plugin's existing `configure` action. SHELXT/SHELXL still require the user's own authorization. Runtime preparation or a minimal read-only tool test is not proof of a complete crystal solve/refinement.

## Building and testing

Windows desktop builds run `desktop-tauri/scripts/build-native-tools.ps1` before freezing the backend. Use a verified MSVC developer environment. The script compiles/tests `platon-headless.rs` with a static CRT and reproducible-link flag; the generated EXE is ignored by Git and explicitly included in the Windows PyInstaller payload. Source-only Windows users must build this adapter before using the host's preparation action, or configure another already-working installation.

Regressions cover product-generated child environments, frozen/source launch behavior, plugin refusal and completion events, old misrouted sessions, missing bindings, account-root lookup, typed/explicit license consent, archive safety, real probe completion, and preservation of existing installations. Windows state readers also permit atomic replacement while polled, with bounded writer retries and correct installer-thread liveness.
