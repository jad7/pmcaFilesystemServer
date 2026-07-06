#!/usr/bin/env python3
"""Small HTTP client for live camera smoke tests."""

import urllib.parse
import urllib.request
import urllib.error


class CameraClient(object):
    def __init__(self, base_url, timeout_seconds=10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def hello(self):
        return self.request_text("GET", "/api/v1/hello.txt")

    def cursor_create(self, modified_after=None, prefix=None, kind=None, force=False):
        params = {}
        if modified_after is not None:
            params["modified_after"] = str(int(modified_after))
        if prefix:
            params["prefix"] = prefix
        if kind:
            params["kind"] = kind
        if force:
            params["force"] = "1"
        return self.request_text("POST", "/api/v1/cursor/create.txt", params)

    def cursor_status(self):
        return self.request_text("GET", "/api/v1/cursor/status.txt")

    def cursor_files(self, limit=100):
        return self.request_text("GET", "/api/v1/cursor/files.txt", {"limit": str(int(limit))})

    def cursor_close(self):
        return self.request_text("POST", "/api/v1/cursor/close.txt")

    def file_info(self, path):
        return self.request_text("GET", "/api/v1/file.txt", {"path": path})

    def download_url(self, path):
        return self.build_url("/api/v1/download", {"path": path})

    def request_text(self, method, path, params=None):
        url = self.build_url(path, params)
        request = urllib.request.Request(
            url,
            data=b"" if method.upper() == "POST" else None,
        )
        request.get_method = lambda: method.upper()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.getcode(), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")

    def build_url(self, path, params=None):
        url = self.base_url + path
        if params:
            query = urllib.parse.urlencode(params)
            url = url + "?" + query
        return url


def parse_key_values(text):
    values = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "," not in line:
            continue
        key, value = line.split(",", 1)
        values[key] = value
    return values


def parse_header_fields(text):
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            fields = {}
            for token in line[2:].split():
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                fields[key] = value
            return fields
    return {}
