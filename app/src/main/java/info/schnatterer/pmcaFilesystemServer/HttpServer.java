package info.schnatterer.pmcaFilesystemServer;

import com.github.ma1co.openmemories.framework.DeviceInfo;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.util.List;
import java.util.Map;
import java.util.Locale;

import fi.iki.elonen.SimpleWebServer;

public class HttpServer extends SimpleWebServer {
    static final int PORT = 8080;
    static final String HOST = null; // bind to all interfaces by default
    static final String WWW_ROOT = "/";
    static final boolean QUIET = false;

    public HttpServer() {
        super(HOST, PORT, new File(WWW_ROOT).getAbsoluteFile(), QUIET);
        CameraFileIndex.getInstance().primeAsync();
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
        if ("/api/v1/files.txt".equals(uri) || "/api/v1/files".equals(uri)) {
            return serveFiles(session);
        }
        if ("/api/v1/file.txt".equals(uri) || "/api/v1/file".equals(uri)) {
            return serveFileMeta(session);
        }
        if ("/api/v1/status.txt".equals(uri) || "/api/v1/status".equals(uri)) {
            return serveStatus();
        }
        if ("/api/v1/download".equals(uri)) {
            return serveDownload(session);
        } else {
            return super.serve(session);
        }
    }

    private Response serveHello() {
        StringBuilder response = new StringBuilder();
        response.append("protocol,pmca-sync,1\n");
        response.append("device,").append(escapeLineValue(getDeviceInfo().getBrand())).append(",");
        response.append(escapeLineValue(getDeviceInfo().getModel())).append("\n");
        response.append("limits,").append(CameraFileIndex.getInstance().getDefaultLimit()).append(",");
        response.append(CameraFileIndex.getInstance().getMaxLimit()).append("\n");
        response.append("capabilities,text,cursor,status\n");
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, response.toString());
    }

    private Response serveStatus() {
        CameraFileIndex index = CameraFileIndex.getInstance();
        StringBuilder response = new StringBuilder();
        response.append("status,");
        response.append(index.isLoaded() ? "ready" : (index.isLoading() ? "scanning" : "starting"));
        response.append("\n");
        response.append("files_indexed,").append(index.getIndexedCount()).append("\n");
        response.append("scanning,").append(index.isLoading() ? "1" : "0").append("\n");
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, response.toString());
    }

    private Response serveFiles(IHTTPSession session) {
        Map<String, String> params = session.getParms();
        int limit = parseLimit(params.get("limit"));
        long modifiedAfter = parseLongOrDefault(params.get("modified_after"), Long.MIN_VALUE);
        String ext = normalizeExt(decodeValue(params.get("ext")));
        String prefix = decodeValue(params.get("prefix"));
        CameraFileKind kind;
        try {
            kind = CameraFileKind.fromQuery(decodeValue(params.get("kind")));
        } catch (IllegalArgumentException e) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "invalid kind\n");
        }
        CursorToken cursor;
        try {
            cursor = parseCursor(params.get("cursor"));
        } catch (IllegalArgumentException e) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "invalid cursor\n");
        }

        CameraFileIndex.PageQuery query = new CameraFileIndex.PageQuery(limit, cursor, kind, ext, prefix, modifiedAfter);
        CameraFileIndex.PageResult page = CameraFileIndex.getInstance().query(query);
        StringBuilder response = new StringBuilder();
        response.append("# pmca-sync files v=1");
        response.append(" limit=").append(limit);
        response.append(" count=").append(page.getItems().size());
        response.append(" has_more=").append(page.hasMore() ? "1" : "0");
        if (page.getNextCursor() != null) {
            response.append(" next=").append(page.getNextCursor());
        }
        response.append(" format=tsv fields=path,mtime,size,kind encoding=backslash\n");
        for (CameraFileEntry entry : page.getItems()) {
            response.append(escapeField(entry.getPath())).append("\t");
            response.append(entry.getModifiedAt()).append("\t");
            response.append(entry.getSize()).append("\t");
            response.append(entry.getKind().wireName()).append("\n");
        }
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, response.toString());
    }

    private Response serveFileMeta(IHTTPSession session) {
        String path = decodeValue(session.getParms().get("path"));
        if (path == null || path.length() == 0) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "missing path\n");
        }
        CameraFileEntry entry = CameraFileIndex.getInstance().findByPath(path);
        if (entry == null) {
            return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "not found\n");
        }
        StringBuilder response = new StringBuilder();
        response.append("# pmca-sync file v=1\n");
        response.append(escapeField(entry.getPath())).append("\t");
        response.append(entry.getModifiedAt()).append("\t");
        response.append(entry.getSize()).append("\t");
        response.append(entry.getKind().wireName()).append("\n");
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, response.toString());
    }

    private Response serveDownload(IHTTPSession session) {
        String path = decodeValue(session.getParms().get("path"));
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

    private long parseLongOrDefault(String value, long defaultValue) {
        if (value == null || value.length() == 0) {
            return defaultValue;
        }
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    private CursorToken parseCursor(String value) {
        if (value == null || value.length() == 0) {
            return null;
        }
        return CursorToken.decode(value);
    }

    private String normalizeExt(String value) {
        if (value == null || value.length() == 0) {
            return null;
        }
        String lower = value.toLowerCase(Locale.US);
        if (!lower.startsWith(".")) {
            lower = "." + lower;
        }
        return lower;
    }

    private String decodeValue(String value) {
        return value;
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
