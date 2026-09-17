"""Compatibility exports for the shared task-timeline implementation."""

from ...core.timeline import (
    estimate,
    main,
    render_markdown,
)

__all__ = ['estimate', 'render_markdown', 'main']

if __name__ == "__main__":
    raise SystemExit(main())
