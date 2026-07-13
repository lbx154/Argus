import Lean

open Lean Lean.Elab

unsafe def auditSource (path : System.FilePath) : IO UInt32 := do
  Lean.initSearchPath (← Lean.findSysroot)
  let source ← IO.FS.readFile path
  let inputCtx := Parser.mkInputContext source path.toString
  let (header, parserState, headerMessages) ← Parser.parseHeader inputCtx
  Lean.enableInitializersExecution
  let (env, messages) ← processHeader
    (header := header)
    (opts := {})
    (messages := headerMessages)
    (inputCtx := inputCtx)
    (trustLevel := 0)
    (mainModule := `ArgusLeanAxiomAuditTarget)
  let state ← IO.processCommands inputCtx parserState (Command.mkState env messages {})
  if state.commandState.messages.hasErrors then
    IO.eprintln "ARGUS_AXIOM_AUDIT_ERROR: source elaboration failed"
    return 2
  let axioms : Array Name :=
    state.commandState.env.constants.map₂.foldl (init := #[]) fun names name info =>
      if info.isAxiom then names.push name else names
  if axioms.isEmpty then
    return 0
  for name in axioms do
    IO.eprintln s!"ARGUS_AXIOM_AUDIT_FOUND: {name}"
  return 3

unsafe def main (args : List String) : IO UInt32 := do
  match args with
  | [path] => auditSource path
  | _ =>
      IO.eprintln "usage: lean_axiom_audit <source.lean>"
      return 64
