// WebView2 injects initialization scripts into child frames as well. Freeze
// the privileged shell only: the sandboxed Web cockpit has no native command
// permissions and must retain normal browser semantics (e.g. d3 prototypes).
if (window === window.top) {
  Object.freeze(Object.prototype);
}
