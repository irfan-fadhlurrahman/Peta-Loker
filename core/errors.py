"""Error types shared by sources and the run loop. The run log records the
`error_type` of each, so failures can be counted and alerted on by kind
rather than by message text."""

from __future__ import annotations


class SourceError(Exception):
    """Base class; `error_type` is what lands in job_run_logs.error_type."""

    error_type = "error"


class RobotsDisallowed(SourceError):
    """robots.txt forbids the URL for our User-Agent. Never retried."""

    error_type = "robots_disallowed"


class SourceBlocked(SourceError):
    """The site refused us (403, repeated 429, captcha/challenge page). Aborts
    the whole run: hammering a site that is blocking us is both rude and
    pointless."""

    error_type = "blocked"


class LayoutChanged(SourceError):
    """A page no longer has the structure the parser expects (no JSON-LD, no
    embedded JSON, missing selectors). Counted per item; if every item fails
    this way the run is marked layout_changed."""

    error_type = "layout_changed"


class FetchFailed(SourceError):
    """Network/5xx/timeout after all retries for one URL. Counted per item."""

    error_type = "fetch_failed"
