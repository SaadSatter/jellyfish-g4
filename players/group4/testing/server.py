"""Serve the local dashboard and start its background batches."""

from __future__ import annotations

import json
import mimetypes
import re
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit

from .batch import (
    BATCH_ID,
    HERE,
    PRESETS,
    ROOT,
    BatchSpec,
    batch_data,
    create_batch,
    maps,
    read_json,
    run_batch,
    run_data,
    worker_command,
)

ASSETS = {
    "/": "index.html",
    "/dashboard.css": "dashboard.css",
    "/dashboard.js": "dashboard.js",
    "/charts.js": "charts.js",
}


class Dashboard(ThreadingHTTPServer):
    def __init__(self, port: int, results: Path):
        super().__init__(("127.0.0.1", port), Handler)
        self.results = results
        self.active: str | None = None
        self.lock = Lock()

    def start_batch(self, settings: dict) -> str:
        with self.lock:
            if self.active is not None:
                raise ValueError("Wait for the active batch to finish")
            folder = create_batch(BatchSpec.parse(settings), self.results)
            self.active = folder.name

        def run() -> None:
            try:
                run_batch(folder)
            finally:
                with self.lock:
                    self.active = None

        Thread(target=run, name=f"batch-{folder.name}").start()
        return folder.name

    def list_batches(self) -> list[dict]:
        batches = []
        for path in self.results.glob("*/manifest.json"):
            if BATCH_ID.fullmatch(path.parent.name):
                manifest = read_json(path)
                batches.append(
                    {
                        "id": manifest["id"],
                        "name": manifest["spec"]["label"],
                        "created": manifest["created"],
                    }
                )
        return sorted(batches, key=lambda batch: batch["created"], reverse=True)


class Handler(BaseHTTPRequestHandler):
    server: Dashboard

    def respond(
        self, value, *, status=HTTPStatus.OK, download: str | None = None
    ) -> None:
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode(),
            "application/json",
            status,
            download,
        )

    def send_bytes(
        self, body: bytes, content_type: str, status=HTTPStatus.OK, download=None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        if download:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{download}"'
            )
        self.end_headers()
        self.wfile.write(body)

    def trusted_host(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self) -> None:
        if not self.trusted_host():
            self.respond(
                {"error": "Use the local dashboard address"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        try:
            self.get(urlsplit(self.path).path)
        except (FileNotFoundError, IndexError):
            self.respond(
                {"error": "Run or batch unavailable"}, status=HTTPStatus.NOT_FOUND
            )

    def get(self, path: str) -> None:
        if path in ASSETS:
            filename = HERE / ASSETS[path]
            self.send_bytes(
                filename.read_bytes(), mimetypes.guess_type(filename)[0] or "text/plain"
            )
            return
        if path == "/api/catalog":
            self.respond(
                {
                    "presets": PRESETS,
                    "maps": maps(),
                    "batches": self.server.list_batches(),
                }
            )
            return
        match = re.fullmatch(
            r"/api/batches/([0-9a-f]{12})(?:/(manifest|export|runs/[1-9][0-9]{0,3}))?",
            path,
        )
        if not match:
            raise FileNotFoundError
        batch_id, resource = match.groups()
        folder = self.server.results / batch_id
        if resource == "manifest":
            self.respond(
                read_json(folder / "manifest.json"),
                download=f"{batch_id}-manifest.json",
            )
        elif resource == "export":
            self.respond(
                batch_data(folder, include_history=True),
                download=f"{batch_id}-results.json",
            )
        elif resource:
            self.respond(run_data(folder, int(resource.split("/")[1])))
        else:
            self.respond(
                {**batch_data(folder), "active": self.server.active == batch_id}
            )

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        if not self.trusted_host() or origin not in (
            None,
            f"http://{self.headers.get('Host')}",
        ):
            self.respond(
                {"error": "Use the local dashboard address"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Send JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size")
            settings = json.loads(self.rfile.read(length))
            if not isinstance(settings, dict):
                raise TypeError("Expected a settings object")
            self.post(urlsplit(self.path).path, settings)
        except (FileNotFoundError, IndexError):
            self.respond(
                {"error": "Run or batch unavailable"}, status=HTTPStatus.NOT_FOUND
            )
        except (OSError, TypeError, ValueError) as error:
            self.respond({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)

    def post(self, path: str, settings: dict) -> None:
        if path == "/api/batches":
            batch_id = self.server.start_batch(settings)
            self.respond({"id": batch_id}, status=HTTPStatus.CREATED)
            return
        match = re.fullmatch(
            r"/api/batches/([0-9a-f]{12})/runs/([1-9][0-9]{0,3})/replay", path
        )
        if not match:
            raise FileNotFoundError
        batch_id, run_id = match.groups()
        folder = self.server.results / batch_id
        manifest = read_json(folder / "manifest.json")
        if int(run_id) > len(manifest["jobs"]):
            raise FileNotFoundError
        subprocess.Popen(worker_command(folder, int(run_id), replay=True), cwd=ROOT)
        self.respond(
            {"message": "Replay started in the simulator viewer"},
            status=HTTPStatus.ACCEPTED,
        )
