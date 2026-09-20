#!/usr/bin/env python3
"""Small HTTP client for live camera smoke tests."""

import os
import shutil
import urllib.parse
import urllib.error
import urllib.request


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

    def ui_status(self, message):
        return self.request_text("POST", "/api/v1/ui-status.txt", {"message": message})

    def file_info(self, path):
        return self.request_text("GET", "/api/v1/file.txt", {"path": path})

    def download_url(self, path):
        return self.build_url("/api/v1/download", {"path": path})

    def open_download(self, path, timeout_seconds=None, file_index=None, total_files=None):
        params = {"path": path}
        if file_index is not None and total_files is not None:
            params.update(index=str(file_index), total=str(total_files))
        url = self.build_url("/api/v1/download", params)
        request = urllib.request.Request(url)
        return self.open_response(request, timeout_seconds=timeout_seconds)

    def download_to_path(self, path, destination_path, timeout_seconds=None):
        response = self.open_download(path, timeout_seconds=timeout_seconds)
        try:
            if response.getcode() != 200:
                return response.getcode(), response.read().decode("utf-8", "replace")
            destination_dir = os.path.dirname(destination_path)
            if destination_dir and not os.path.isdir(destination_dir):
                os.makedirs(destination_dir, exist_ok=True)
            with open(destination_path, "wb") as output_file:
                shutil.copyfileobj(response, output_file, length=1024 * 64)
            return response.getcode(), None
        finally:
            response.close()

    def request_text(self, method, path, params=None):
        url = self.build_url(path, params)
        request = urllib.request.Request(
            url,
            data=b"" if method.upper() == "POST" else None,
        )
        request.get_method = lambda: method.upper()
        response = self.open_response(request)
        try:
            return response.getcode(), response.read().decode("utf-8", "replace")
        finally:
            response.close()

    def open_response(self, request, timeout_seconds=None):
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            return error

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


def parse_cursor_page(text):
    header = {}
    entries = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        if line.startswith("# "):
            header = parse_header_fields(line)
            continue
        if line.startswith("#"):
            continue
        columns = line.split("\t")
        if len(columns) != 4:
            continue
        entries.append({
            "path": unescape_field(columns[0]),
            "mtime": int(columns[1]),
            "size": int(columns[2]),
            "kind": columns[3],
        })
    return header, entries


def unescape_field(value):
    result = []
    i = 0
    while i < len(value):
        char = value[i]
        if char != "\\" or i + 1 >= len(value):
            result.append(char)
            i += 1
            continue
        next_char = value[i + 1]
        if next_char == "t":
            result.append("\t")
        elif next_char == "n":
            result.append("\n")
        elif next_char == "r":
            result.append("\r")
        elif next_char == "\\":
            result.append("\\")
        else:
            result.append(next_char)
        i += 2
    return "".join(result)
