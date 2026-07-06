package info.schnatterer.pmcaFilesystemServer;

import android.os.Environment;

import java.io.File;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

final class CameraFileIndex {
    private static final CameraFileIndex INSTANCE = new CameraFileIndex();
    private static final int DEFAULT_LIMIT = 100;
    private static final int MAX_LIMIT = 200;
    private static final Comparator<CameraFileEntry> ORDER = new Comparator<CameraFileEntry>() {
        @Override
        public int compare(CameraFileEntry left, CameraFileEntry right) {
            if (left.getModifiedAt() != right.getModifiedAt()) {
                return left.getModifiedAt() > right.getModifiedAt() ? -1 : 1;
            }
            return left.getPath().compareTo(right.getPath());
        }
    };

    private volatile boolean loading;
    private volatile boolean loaded;
    private List<CameraFileEntry> ordered = Collections.emptyList();
    private Map<String, CameraFileEntry> byPath = Collections.emptyMap();

    public static CameraFileIndex getInstance() {
        return INSTANCE;
    }

    public void primeAsync() {
        if (!beginLoading()) {
            return;
        }
        Thread thread = new Thread(new Runnable() {
            @Override
            public void run() {
                loadAndPublish();
            }
        }, "camera-file-index");
        thread.setDaemon(true);
        thread.start();
    }

    public void ensureLoaded() {
        if (loaded) {
            return;
        }
        if (beginLoading()) {
            loadAndPublish();
            return;
        }
        waitForLoaded();
    }

    public int getDefaultLimit() {
        return DEFAULT_LIMIT;
    }

    public int getMaxLimit() {
        return MAX_LIMIT;
    }

    public long getFileCount() {
        ensureLoaded();
        return ordered.size();
    }

    public boolean isLoaded() {
        return loaded;
    }

    public boolean isLoading() {
        return loading;
    }

    public long getIndexedCount() {
        if (!loaded) {
            return 0L;
        }
        return ordered.size();
    }

    public CameraFileEntry findByPath(String path) {
        ensureLoaded();
        return byPath.get(path);
    }

    public PageResult query(PageQuery query) {
        ensureLoaded();
        int limit = query.getLimit();
        ArrayList<CameraFileEntry> items = new ArrayList<CameraFileEntry>(limit);
        int startIndex = findStartIndex(query.getCursor());
        for (int i = startIndex; i < ordered.size(); i++) {
            CameraFileEntry entry = ordered.get(i);
            if (query.getModifiedAfter() != Long.MIN_VALUE && entry.getModifiedAt() <= query.getModifiedAfter()) {
                break;
            }
            if (!query.matches(entry)) {
                continue;
            }
            items.add(entry);
            if (items.size() > limit) {
                break;
            }
        }

        boolean hasMore = items.size() > limit;
        if (hasMore) {
            items.remove(items.size() - 1);
        }

        String nextCursor = null;
        if (!items.isEmpty() && hasMore) {
            nextCursor = CursorToken.encode(items.get(items.size() - 1));
        }

        return new PageResult(items, hasMore, nextCursor);
    }

    public List<File> getFilesByKind(CameraFileKind kind) {
        ensureLoaded();
        ArrayList<File> files = new ArrayList<File>();
        for (CameraFileEntry entry : ordered) {
            if (kind == null || entry.getKind() == kind) {
                files.add(new File(entry.getPath()));
            }
        }
        return files;
    }

    private int findStartIndex(CursorToken cursor) {
        if (cursor == null) {
            return 0;
        }
        CameraFileEntry probe = new CameraFileEntry(cursor.getPath(), cursor.getModifiedAt(), 0, CameraFileKind.OTHER);
        int low = 0;
        int high = ordered.size();
        while (low < high) {
            int mid = (low + high) >>> 1;
            CameraFileEntry midValue = ordered.get(mid);
            if (ORDER.compare(midValue, probe) <= 0) {
                low = mid + 1;
            } else {
                high = mid;
            }
        }
        return low;
    }

    private void waitForLoaded() {
        synchronized (this) {
            while (!loaded) {
                try {
                    wait();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }
    }

    private boolean beginLoading() {
        synchronized (this) {
            if (loaded || loading) {
                return false;
            }
            loading = true;
            return true;
        }
    }

    private void loadAndPublish() {
        File root = Environment.getExternalStorageDirectory();
        ArrayList<CameraFileEntry> freshOrdered = new ArrayList<CameraFileEntry>();
        Map<String, CameraFileEntry> freshByPath = new HashMap<String, CameraFileEntry>();

        if (root != null) {
            ArrayDeque<File> stack = new ArrayDeque<File>();
            stack.push(root);

            while (!stack.isEmpty()) {
                File current = stack.pop();
                File[] children = current.listFiles();
                if (children == null) {
                    continue;
                }
                for (File child : children) {
                    if (child.isDirectory()) {
                        stack.push(child);
                        continue;
                    }
                    if (!child.isFile()) {
                        continue;
                    }
                    String path = child.getAbsolutePath();
                    CameraFileKind kind = CameraFileKind.fromPath(path);
                    CameraFileEntry entry = new CameraFileEntry(path, child.lastModified(), child.length(), kind);
                    freshOrdered.add(entry);
                    freshByPath.put(path, entry);
                }
            }
        }

        Collections.sort(freshOrdered, ORDER);

        synchronized (this) {
            ordered = freshOrdered;
            byPath = freshByPath;
            loaded = true;
            loading = false;
            notifyAll();
        }
    }

    static final class PageQuery {
        private final int limit;
        private final CursorToken cursor;
        private final CameraFileKind kind;
        private final String ext;
        private final String prefix;
        private final long modifiedAfter;

        PageQuery(int limit, CursorToken cursor, CameraFileKind kind, String ext, String prefix, long modifiedAfter) {
            this.limit = limit;
            this.cursor = cursor;
            this.kind = kind;
            this.ext = ext;
            this.prefix = prefix;
            this.modifiedAfter = modifiedAfter;
        }

        int getLimit() {
            return limit;
        }

        CursorToken getCursor() {
            return cursor;
        }

        long getModifiedAfter() {
            return modifiedAfter;
        }

        boolean matches(CameraFileEntry entry) {
            if (kind != null && entry.getKind() != kind) {
                return false;
            }
            if (prefix != null && prefix.length() > 0 && !entry.getPath().startsWith(prefix)) {
                return false;
            }
            if (ext != null && ext.length() > 0 && !entry.getPath().toLowerCase(Locale.US).endsWith(ext)) {
                return false;
            }
            return true;
        }
    }

    static final class PageResult {
        private final List<CameraFileEntry> items;
        private final boolean hasMore;
        private final String nextCursor;

        PageResult(List<CameraFileEntry> items, boolean hasMore, String nextCursor) {
            this.items = items;
            this.hasMore = hasMore;
            this.nextCursor = nextCursor;
        }

        public List<CameraFileEntry> getItems() {
            return items;
        }

        public boolean hasMore() {
            return hasMore;
        }

        public String getNextCursor() {
            return nextCursor;
        }
    }
}
