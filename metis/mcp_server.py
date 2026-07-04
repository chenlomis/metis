from __future__ import annotations

import os
from typing import Any

from . import services as svc


SERVER_NAME = "metis"


def build_server() -> Any:
    """Build the Metis MCP server.

    The MCP SDK import is intentionally lazy so normal CLI imports do not require
    the optional MCP dependency.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "The MCP SDK is not installed. Install Metis with the MCP extra, "
            "for example: pip install 'metis-job[mcp]'."
        ) from exc

    server = FastMCP(SERVER_NAME)

    @server.tool()
    def get_metis_status(
        data_dir: str | None = None,
        profile_path: str | None = None,
        tracker_path: str | None = None,
    ) -> dict[str, Any]:
        """Return setup/config readiness and missing next steps."""
        return svc.get_metis_status(
            data_dir=data_dir,
            profile_path=profile_path,
            tracker_path=tracker_path,
            env=dict(os.environ),
        )

    @server.tool()
    def run_job_search(
        data_dir: str | None = None,
        profile_path: str | None = None,
        tracker_path: str | None = None,
        lookback: str = "3d",
        score_all: bool = False,
        dry_run: bool = True,
        confirm_send: bool = False,
    ) -> dict[str, Any]:
        """Run or preview the Metis job-search digest pipeline."""
        return svc.run_job_search(
            data_dir=data_dir,
            profile_path=profile_path,
            tracker_path=tracker_path,
            lookback=lookback,
            score_all=score_all,
            dry_run=dry_run,
            confirm_send=confirm_send,
            env=dict(os.environ),
        )

    @server.tool()
    def list_recommended_roles(
        data_dir: str | None = None,
        limit: int = 20,
        latest_run_only: bool = True,
    ) -> dict[str, Any]:
        """List recent recommended roles from Metis run traces."""
        return svc.list_recommended_roles(
            data_dir=data_dir,
            limit=limit,
            latest_run_only=latest_run_only,
        )

    @server.tool()
    def get_role_details(
        role_id: str,
        data_dir: str | None = None,
    ) -> dict[str, Any] | None:
        """Return saved scoring trace details for one role."""
        return svc.get_role_details(role_id, data_dir=data_dir)

    @server.tool()
    def record_scoring_feedback(
        text: str,
        data_dir: str | None = None,
        run_id: str | None = None,
        roles: list[str] | None = None,
        dims: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record explicit scoring calibration feedback."""
        return svc.record_scoring_feedback(
            text,
            data_dir=data_dir,
            run_id=run_id,
            roles=roles,
            dims=dims,
        )

    @server.tool()
    def list_scoring_feedback(
        data_dir: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """List recent scoring calibration feedback."""
        return svc.list_scoring_feedback(data_dir=data_dir, limit=limit)

    @server.tool()
    def track_applications(
        data_dir: str | None = None,
        tracker_path: str | None = None,
        lookback_days: int = 7,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Scan application-status emails and update or preview tracker changes."""
        return svc.track_applications(
            data_dir=data_dir,
            tracker_path=tracker_path,
            lookback_days=lookback_days,
            dry_run=dry_run,
            env=dict(os.environ),
        )

    @server.tool()
    def list_application_activity(
        data_dir: str | None = None,
        tracker_path: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List rows from the Metis Applications tracker."""
        return svc.list_application_activity(
            data_dir=data_dir,
            tracker_path=tracker_path,
            limit=limit,
        )

    @server.tool()
    def generate_progress_summary(
        data_dir: str | None = None,
        tracker_path: str | None = None,
        lookback_days: int = 30,
    ) -> dict[str, Any]:
        """Generate structured progress and market summary data."""
        return svc.generate_progress_summary(
            data_dir=data_dir,
            tracker_path=tracker_path,
            lookback_days=lookback_days,
        )

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
