package info.schnatterer.pmcaFilesystemServer;

import com.github.ma1co.openmemories.framework.DeviceInfo;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import fi.iki.elonen.NanoHTTPD.Method;
import fi.iki.elonen.SimpleWebServer;

public class HttpServer extends SimpleWebServer {
    static final int PORT = 8080;
    static final String HOST = null; // bind to all interfaces by default
    static final String WWW_ROOT = "/";
    static final boolean QUIET = false;

    public HttpServer() {
        super(HOST, PORT, new File(WWW_ROOT).getAbsoluteFile(), QUIET);
    }

    @Override
    public Response serve(IHTTPSession session) {
        String uri = session.getUri();
        if ("/".equals(uri)) {
            return serveRoot();
        }
        if ("/api/v1/hello.txt".equals(uri) || "/api/v1/hello".equals(uri)) {
            return serveHello();
        }
        if ("/api/v1/cursor/create.txt".equals(uri) || "/api/v1/cursor/create".equals(uri)) {
            return serveCursorCreate(session);
        }
        if ("/api/v1/cursor/status.txt".equals(uri) || "/api/v1/cursor/status".equals(uri)
                || "/api/v1/status.txt".equals(uri) || "/api/v1/status".equals(uri)) {
            return serveCursorStatus();
        }
        if ("/api/v1/cursor/files.txt".equals(uri) || "/api/v1/cursor/files".equals(uri)
                || "/api/v1/files.txt".equals(uri) || "/api/v1/files".equals(uri)) {
            return serveCursorFiles(session);
        }
        if ("/api/v1/cursor/close.txt".equals(uri) || "/api/v1/cursor/close".equals(uri)) {
            return serveCursorClose(session);
        }
        if ("/api/v1/file.txt".equals(uri) || "/api/v1/file".equals(uri)) {
            return serveFileMeta(session);
        }
        if ("/api/v1/download".equals(uri)) {
            return serveDownload(session);
        }
        return super.serve(session);
    }

    private Response serveHello() {
        StringBuilder response = new StringBuilder();
        response.append("protocol,pmca-sync,1\n");
        response.append("device,").append(escapeLineValue(getDeviceInfo().getBrand())).append(",");
        response.append(escapeLineValue(getDeviceInfo().getModel())).append("\n");
        response.append("limits,").append(CameraFileIndex.getInstance().getDefaultLimit()).append(",");
        response.append(CameraFileIndex.getInstance().getMaxLimit()).append("\n");
        response.append("capabilities,text,cursor,status,singleton\n");
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, response.toString());
    }

    private Response serveCursorCreate(IHTTPSession session) {
        if (!isPost(session)) {
            return methodNotAllowed("POST required\n");
        }

        Map<String, String> params = session.getParms();
        long modifiedAfter;
        try {
            modifiedAfter = parseOptionalLong(params.get("modified_after"), Long.MIN_VALUE);
        } catch (NumberFormatException e) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "invalid modified_after\n");
        }
        String prefix = params.get("prefix");
        CameraFileKind kind = null;
        boolean includeOther = false;
        String kindValue = params.get("kind");
        if (kindValue != null && kindValue.length() > 0) {
            if ("all".equalsIgnoreCase(kindValue)) {
                includeOther = true;
            } else {
                try {
                    kind = CameraFileKind.fromQuery(kindValue);
                } catch (IllegalArgumentException e) {
                    return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "invalid kind\n");
                }
            }
        }
        boolean force = isTruthy(params.get("force"));

        CameraFileIndex.CreateResult result;
        try {
            result = CameraFileIndex.getInstance().createCursor(modifiedAfter, prefix, kind, includeOther, force);
        } catch (IllegalArgumentException e) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "invalid prefix\n");
        }

        CameraFileIndex.CursorSnapshot snapshot = result.getSnapshot();
        Response.Status status;
        if (snapshot.getState() == CameraFileIndex.CursorSnapshot.State.ERROR) {
            status = Response.Status.BAD_REQUEST;
        } else if (result.isCreated()) {
            status = Response.Status.ACCEPTED;
        } else {
            status = Response.Status.CONFLICT;
        }
        return newFixedLengthResponse(status, MIME_PLAINTEXT, renderCursorSnapshot(snapshot));
    }

    private Response serveCursorStatus() {
        CameraFileIndex.CursorSnapshot snapshot = CameraFileIndex.getInstance().getStatus();
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, renderCursorSnapshot(snapshot));
    }

    private Response serveCursorFiles(IHTTPSession session) {
        int limit = parseLimit(session.getParms().get("limit"));
        CameraFileIndex.CursorPage page = CameraFileIndex.getInstance().getCursorPage(limit);
        CameraFileIndex.CursorSnapshot snapshot = page.getSnapshot();
        if (!snapshot.isReady()) {
            Response.Status status = snapshot.getState() == CameraFileIndex.CursorSnapshot.State.ERROR
                    ? Response.Status.INTERNAL_ERROR
                    : Response.Status.CONFLICT;
            return newFixedLengthResponse(status, MIME_PLAINTEXT, renderCursorSnapshot(snapshot));
        }
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, renderCursorPage(page, limit));
    }

    private Response serveCursorClose(IHTTPSession session) {
        if (!isPost(session)) {
            return methodNotAllowed("POST required\n");
        }
        CameraFileIndex.CursorSnapshot snapshot = CameraFileIndex.getInstance().closeCursor();
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, renderCursorSnapshot(snapshot));
    }

    private Response serveFileMeta(IHTTPSession session) {
        String path = session.getParms().get("path");
        if (path == null || path.length() == 0) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "missing path\n");
        }
        CameraFileEntry entry = CameraFileIndex.getInstance().findByPath(path);
        if (entry == null) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "not found\n");
        }
        StringBuilder response = new StringBuilder();
        response.append("# pmca-sync file v=1\n");
        appendCursorLine(response, entry.getPath(), entry.getModifiedAt(), entry.getSize(), entry.getKind());
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, response.toString());
    }

    private Response serveDownload(IHTTPSession session) {
        String path = session.getParms().get("path");
        if (path == null || path.length() == 0) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "missing path\n");
        }
        CameraFileEntry entry = CameraFileIndex.getInstance().findByPath(path);
        if (entry == null) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "not found\n");
        }
        File file = new File(entry.getPath());
        if (!file.exists() || !file.isFile()) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "not found\n");
        }
        try {
            return newFixedLengthResponse(
                    Response.Status.OK,
                    getMimeTypeForFile(path),
                    new FileInputStream(file),
                    file.length());
        } catch (FileNotFoundException e) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "not found\n");
        }
    }

    private Response serveRoot() {
        String heading =  getDeviceInfo().getBrand() + " - " + getDeviceInfo().getModel();
        StringBuilder response = new StringBuilder("<html><head><title>" + heading
                + "</title><style><!--\n" + "span.dirname { font-weight: bold; }\n"
                + "span.filesize { font-size: 75%; }\n"
                + "// -->\n" + "</style>" + "</head><body><h1>" + heading + "</h1>");

        response.append("<h1>Videos</h1>");
        createFileList(FilesystemScanner.getVideosOnExternalStorage(), response);

        response.append("<h1>JPEGs</h1>");
        createFileList(FilesystemScanner.getJpegsOnExternalStorage(), response);

        response.append("<h1>RAW</h1>");
        createFileList(FilesystemScanner.getRawsOnExternalStorage(), response);

        response.append("<h1>Log File</h1>");
        createLinkToLogFile(response);

        response.append("<h1>File System</h1>");
        response.append(listDirectory("/", new File("/"))
                .replaceFirst("<html>.*<body>", ""));
        return newFixedLengthResponse(Response.Status.OK, MIME_HTML, response.toString());
    }

    private void createLinkToLogFile(StringBuilder response) {
        response.append("<a href=\"");
        File logFile = Logger.getFile();
        response.append(logFile.getAbsolutePath());
        response.append("\">");
        response.append(logFile.getName());
        response.append("</a>");
    }

    private DeviceInfo getDeviceInfo() {
        return DeviceInfo.getInstance();
    }

    private void createFileList(List<File> files, StringBuilder response) {
        response.append("<ul>");
        for (File file : files) {
            response.append("<li><a href=\"");
            response.append(file.getAbsolutePath());
            response.append("\">");
            response.append(file.getName());
            response.append("</a></li>");
        }
        response.append("</ul>");
    }

    private int parseLimit(String value) {
        CameraFileIndex index = CameraFileIndex.getInstance();
        int limit = index.getDefaultLimit();
        if (value != null && value.length() > 0) {
            try {
                limit = Integer.parseInt(value);
            } catch (NumberFormatException e) {
                return index.getDefaultLimit();
            }
        }
        if (limit < 1) {
            limit = 1;
        }
        if (limit > index.getMaxLimit()) {
            limit = index.getMaxLimit();
        }
        return limit;
    }

    private long parseOptionalLong(String value, long defaultValue) {
        if (value == null || value.length() == 0) {
            return defaultValue;
        }
        return Long.parseLong(value);
    }

    private boolean isTruthy(String value) {
        if (value == null) {
            return false;
        }
        return "1".equals(value) || "true".equalsIgnoreCase(value) || "yes".equalsIgnoreCase(value) || "on".equalsIgnoreCase(value);
    }

    private boolean isPost(IHTTPSession session) {
        return session.getMethod() == Method.POST;
    }

    private Response methodNotAllowed(String message) {
        return newFixedLengthResponse(Response.Status.METHOD_NOT_ALLOWED, MIME_PLAINTEXT, message);
    }

    private String renderCursorSnapshot(CameraFileIndex.CursorSnapshot snapshot) {
        StringBuilder response = new StringBuilder();
        response.append("cursor,1\n");
        response.append("status,").append(snapshot.getState().name().toLowerCase(Locale.US)).append("\n");
        response.append("matched,").append(snapshot.getMatchedCount()).append("\n");
        response.append("scanned,").append(snapshot.getScannedCount()).append("\n");
        response.append("emitted,").append(snapshot.getEmittedCount()).append("\n");
        response.append("remaining,").append(snapshot.getRemainingCount()).append("\n");
        if (snapshot.getMessage() != null && snapshot.getMessage().length() > 0) {
            response.append("error,").append(escapeLineValue(snapshot.getMessage())).append("\n");
        }
        return response.toString();
    }

    private String renderCursorPage(CameraFileIndex.CursorPage page, int limit) {
        CameraFileIndex.CursorSnapshot snapshot = page.getSnapshot();
        StringBuilder response = new StringBuilder();
        response.append("# pmca-sync cursor v=1");
        response.append(" cursor=1");
        response.append(" status=").append(snapshot.getState().name().toLowerCase(Locale.US));
        response.append(" limit=").append(limit);
        response.append(" count=").append(page.getCount());
        response.append(" has_more=").append(page.hasMore() ? "1" : "0");
        response.append(" matched=").append(snapshot.getMatchedCount());
        response.append(" scanned=").append(snapshot.getScannedCount());
        response.append(" emitted=").append(snapshot.getEmittedCount());
        response.append(" remaining=").append(snapshot.getRemainingCount());
        response.append(" format=tsv fields=path,mtime,size,kind encoding=backslash\n");
        String[] paths = page.getPaths();
        long[] mtimes = page.getMtimes();
        long[] sizes = page.getSizes();
        byte[] kinds = page.getKinds();
        for (int i = page.getFromIndex(); i < page.getToIndex(); i++) {
            appendCursorLine(response, paths[i], mtimes[i], sizes[i], CameraFileKind.fromPackedValue(kinds[i]));
        }
        return response.toString();
    }

    private void appendCursorLine(StringBuilder response, String path, long modifiedAt, long size, CameraFileKind kind) {
        response.append(escapeField(path)).append("\t");
        response.append(modifiedAt).append("\t");
        response.append(size).append("\t");
        response.append(kind.wireName()).append("\n");
    }

    private String escapeLineValue(String value) {
        return value.replace("\n", " ").replace("\r", " ").replace(",", " ");
    }

    private String escapeField(String value) {
        StringBuilder escaped = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if (ch == '\\') {
                escaped.append("\\\\");
            } else if (ch == '\t') {
                escaped.append("\\t");
            } else if (ch == '\n') {
                escaped.append("\\n");
            } else if (ch == '\r') {
                escaped.append("\\r");
            } else {
                escaped.append(ch);
            }
        }
        return escaped.toString();
    }
}
