package info.schnatterer.pmcaFilesystemServer;

import android.os.Environment;

import java.io.File;
import java.io.IOException;
import java.util.ArrayDeque;

final class CameraFileIndex {
    private static final CameraFileIndex INSTANCE = new CameraFileIndex();
    private static final int DEFAULT_LIMIT = 100;
    private static final int MAX_LIMIT = 500;
    private static final long SESSION_TTL_MS = 10L * 60L * 1000L;
    private static final int INITIAL_CAPACITY = 128;

    private final Object lock = new Object();
    private CursorSession activeSession;
    private String rootCanonicalPath;

    public static CameraFileIndex getInstance() {
        return INSTANCE;
    }

    public int getDefaultLimit() {
        return DEFAULT_LIMIT;
    }

    public int getMaxLimit() {
        return MAX_LIMIT;
    }

    public CreateResult createCursor(long modifiedAfter, String prefix, CameraFileKind kind, boolean force) {
        String normalizedPrefix = normalizePrefix(prefix);
        File scanRoot = resolveScanRoot(normalizedPrefix);
        if (scanRoot == null) {
            return new CreateResult(false, CursorSnapshot.error("invalid prefix"));
        }

        synchronized (lock) {
            expireIfNeededLocked();

            if (activeSession != null && activeSession.isOpen()) {
                if (!force) {
                    return new CreateResult(false, activeSession.snapshot());
                }
                activeSession.closeNow();
            }

            CursorSession session = new CursorSession(scanRoot, normalizedPrefix, modifiedAfter, kind, SESSION_TTL_MS);
            activeSession = session;
            session.start();
            return new CreateResult(true, session.snapshot());
        }
    }

    public CursorSnapshot getStatus() {
        synchronized (lock) {
            expireIfNeededLocked();
            if (activeSession == null) {
                return CursorSnapshot.idle();
            }
            return activeSession.snapshot();
        }
    }

    public CursorPage getCursorPage(int limit) {
        synchronized (lock) {
            expireIfNeededLocked();
            if (activeSession == null) {
                return CursorPage.notReady(CursorSnapshot.idle());
            }
            return activeSession.page(limit);
        }
    }

    public CursorSnapshot closeCursor() {
        synchronized (lock) {
            if (activeSession == null) {
                return CursorSnapshot.closed(0, 0, 0, 0);
            }
            activeSession.closeNow();
            return activeSession.snapshot();
        }
    }

    public CameraFileEntry findByPath(String path) {
        File file = resolveExistingCameraFile(path);
        if (file == null) {
            return null;
        }
        String absolutePath = file.getAbsolutePath();
        return new CameraFileEntry(
                absolutePath,
                file.lastModified(),
                file.length(),
                CameraFileKind.fromPath(absolutePath));
    }

    private void expireIfNeededLocked() {
        if (activeSession == null) {
            return;
        }
        if (activeSession.isExpired()) {
            activeSession.expireNow();
        }
    }

    private File resolveScanRoot(String normalizedPrefix) {
        File root = getExternalStorageRoot();
        if (root == null) {
            return null;
        }
        if (normalizedPrefix == null || normalizedPrefix.length() == 0) {
            return root;
        }

        File candidate = new File(normalizedPrefix);
        while (candidate != null && (!candidate.exists() || !candidate.isDirectory())) {
            candidate = candidate.getParentFile();
        }
        if (candidate == null) {
            return root;
        }
        String candidateCanonical = canonicalPath(candidate);
        String rootCanonical = getRootCanonicalPath();
        if (candidateCanonical != null && rootCanonical != null && isInsideRoot(candidateCanonical, rootCanonical)) {
            return candidate;
        }
        return root;
    }

    private String normalizePrefix(String prefix) {
        if (prefix == null || prefix.length() == 0) {
            return null;
        }
        File root = getExternalStorageRoot();
        if (root == null) {
            throw new IllegalArgumentException("external storage unavailable");
        }
        String canonical = canonicalPath(new File(prefix));
        String rootCanonical = getRootCanonicalPath();
        if (canonical == null || rootCanonical == null || !isInsideRoot(canonical, rootCanonical)) {
            throw new IllegalArgumentException("invalid prefix");
        }
        return canonical;
    }

    private File resolveExistingCameraFile(String path) {
        if (path == null || path.length() == 0) {
            return null;
        }
        File root = getExternalStorageRoot();
        if (root == null) {
            return null;
        }
        String canonical = canonicalPath(new File(path));
        String rootCanonical = getRootCanonicalPath();
        if (canonical == null || rootCanonical == null || !isInsideRoot(canonical, rootCanonical)) {
            return null;
        }
        File file = new File(canonical);
        if (!file.exists() || !file.isFile()) {
            return null;
        }
        return file;
    }

    private File getExternalStorageRoot() {
        File root = Environment.getExternalStorageDirectory();
        if (root == null) {
            return null;
        }
        return root.getAbsoluteFile();
    }

    private String getRootCanonicalPath() {
        synchronized (lock) {
            if (rootCanonicalPath != null) {
                return rootCanonicalPath;
            }
            File root = getExternalStorageRoot();
            if (root == null) {
                return null;
            }
            rootCanonicalPath = canonicalPath(root);
            return rootCanonicalPath;
        }
    }

    private boolean isInsideRoot(String candidateCanonical, String rootCanonical) {
        if (candidateCanonical.equals(rootCanonical)) {
            return true;
        }
        return candidateCanonical.startsWith(rootCanonical + File.separator);
    }

    private String canonicalPath(File file) {
        try {
            return file.getCanonicalPath();
        } catch (IOException e) {
            return null;
        }
    }

    private static final class CursorSession {
        private final File scanRoot;
        private final String normalizedPrefix;
        private final long modifiedAfter;
        private final CameraFileKind kind;
        private final long createdAtMillis;
        private final long expiresAtMillis;
        private final Object sessionLock = new Object();

        private PackedEntries entries = new PackedEntries();
        private volatile CursorSnapshot.State state = CursorSnapshot.State.SCANNING;
        private volatile String message;
        private volatile boolean cancelled;
        private volatile int scannedCount;
        private volatile int emittedCount;
        private volatile int finalMatchedCount;
        private volatile int finalScannedCount;
        private volatile int finalEmittedCount;
        private Thread worker;

        CursorSession(File scanRoot, String normalizedPrefix, long modifiedAfter, CameraFileKind kind, long ttlMs) {
            this.scanRoot = scanRoot;
            this.normalizedPrefix = normalizedPrefix;
            this.modifiedAfter = modifiedAfter;
            this.kind = kind;
            this.createdAtMillis = System.currentTimeMillis();
            this.expiresAtMillis = this.createdAtMillis + ttlMs;
        }

        void start() {
            Thread thread = new Thread(new Runnable() {
                @Override
                public void run() {
                    scan();
                }
            }, "pmca-cursor-scan");
            thread.setDaemon(true);
            worker = thread;
            thread.start();
        }

        boolean isOpen() {
            CursorSnapshot.State current = state;
            return current == CursorSnapshot.State.SCANNING || current == CursorSnapshot.State.READY;
        }

        boolean isExpired() {
            return System.currentTimeMillis() > expiresAtMillis;
        }

        void expireNow() {
            synchronized (sessionLock) {
                if (state == CursorSnapshot.State.CLOSED || state == CursorSnapshot.State.EXPIRED) {
                    return;
                }
                cancelled = true;
                captureFinalCountsLocked();
                state = CursorSnapshot.State.EXPIRED;
                releaseEntries();
            }
            interruptWorker();
        }

        void closeNow() {
            synchronized (sessionLock) {
                if (state == CursorSnapshot.State.CLOSED) {
                    return;
                }
                cancelled = true;
                captureFinalCountsLocked();
                state = CursorSnapshot.State.CLOSED;
                releaseEntries();
            }
            interruptWorker();
        }

        CursorSnapshot snapshot() {
            synchronized (sessionLock) {
                int matched = entries.size();
                int scanned = scannedCount;
                int emitted = emittedCount;
                if (state == CursorSnapshot.State.CLOSED || state == CursorSnapshot.State.EXPIRED || state == CursorSnapshot.State.ERROR) {
                    matched = finalMatchedCount;
                    scanned = finalScannedCount;
                    emitted = finalEmittedCount;
                }
                return new CursorSnapshot(
                        state,
                        matched,
                        scanned,
                        emitted,
                        expiresAtMillis,
                        message);
            }
        }

        CursorPage page(int limit) {
            synchronized (sessionLock) {
                if (state != CursorSnapshot.State.READY) {
                    return CursorPage.notReady(snapshot());
                }

                int safeLimit = limit;
                if (safeLimit < 1) {
                    safeLimit = 1;
                }

                int fromIndex = emittedCount;
                int toIndex = fromIndex + safeLimit;
                if (toIndex > entries.size()) {
                    toIndex = entries.size();
                }
                boolean hasMore = toIndex < entries.size();
                emittedCount = toIndex;
                return new CursorPage(
                        snapshot(),
                        entries.paths,
                        entries.mtimes,
                        entries.sizes,
                        entries.kinds,
                        fromIndex,
                        toIndex,
                        hasMore);
            }
        }

        private void scan() {
            try {
                ArrayDeque<File> stack = new ArrayDeque<File>();
                stack.push(scanRoot);

                while (!stack.isEmpty()) {
                    if (isCancelled()) {
                        return;
                    }

                    File current = stack.pop();
                    File[] children = current.listFiles();
                    if (children == null) {
                        continue;
                    }

                    for (int i = 0; i < children.length; i++) {
                        if (isCancelled()) {
                            return;
                        }

                        File child = children[i];
                        if (child.isDirectory()) {
                            stack.push(child);
                            continue;
                        }
                        if (!child.isFile()) {
                            continue;
                        }

                        recordScanned();
                        String path = child.getAbsolutePath();
                        if (!matches(path, child.lastModified(), child.length())) {
                            continue;
                        }

                        recordMatch(path, child.lastModified(), child.length(), CameraFileKind.fromPath(path));
                    }
                }

                synchronized (sessionLock) {
                    if (cancelled) {
                        return;
                    }
                    state = CursorSnapshot.State.READY;
                }
            } catch (Throwable t) {
                synchronized (sessionLock) {
                    if (cancelled) {
                        return;
                    }
                    captureFinalCountsLocked();
                    state = CursorSnapshot.State.ERROR;
                    message = t.getClass().getSimpleName();
                    if (t.getMessage() != null && t.getMessage().length() > 0) {
                        message = message + ": " + t.getMessage();
                    }
                    releaseEntries();
                }
            }
        }

        private void recordScanned() {
            synchronized (sessionLock) {
                scannedCount++;
            }
        }

        private void recordMatch(String path, long modifiedAt, long size, CameraFileKind fileKind) {
            synchronized (sessionLock) {
                if (cancelled) {
                    return;
                }
                entries.add(path, modifiedAt, size, fileKind);
            }
        }

        private boolean matches(String path, long modifiedAt, long size) {
            if (modifiedAfter != Long.MIN_VALUE && modifiedAt <= modifiedAfter) {
                return false;
            }
            if (normalizedPrefix != null && normalizedPrefix.length() > 0 && !matchesPrefix(path, normalizedPrefix)) {
                return false;
            }
            if (kind != null && kind != CameraFileKind.fromPath(path)) {
                return false;
            }
            return true;
        }

        private boolean matchesPrefix(String path, String prefix) {
            if (path.equals(prefix)) {
                return true;
            }
            if (!path.startsWith(prefix)) {
                return false;
            }
            int prefixLength = prefix.length();
            if (path.length() <= prefixLength) {
                return false;
            }
            return path.charAt(prefixLength) == File.separatorChar;
        }

        private boolean isCancelled() {
            return cancelled || isExpired();
        }

        private void releaseEntries() {
            entries.clear();
        }

        private void captureFinalCountsLocked() {
            finalMatchedCount = entries.size();
            finalScannedCount = scannedCount;
            finalEmittedCount = emittedCount;
        }

        private void interruptWorker() {
            Thread currentWorker = worker;
            if (currentWorker != null) {
                currentWorker.interrupt();
            }
        }
    }

    static final class CreateResult {
        private final boolean created;
        private final CursorSnapshot snapshot;

        CreateResult(boolean created, CursorSnapshot snapshot) {
            this.created = created;
            this.snapshot = snapshot;
        }

        public boolean isCreated() {
            return created;
        }

        public CursorSnapshot getSnapshot() {
            return snapshot;
        }
    }

    static final class CursorSnapshot {
        enum State {
            IDLE,
            SCANNING,
            READY,
            CLOSED,
            EXPIRED,
            ERROR
        }

        private final State state;
        private final int matchedCount;
        private final int scannedCount;
        private final int emittedCount;
        private final long expiresAtMillis;
        private final String message;

        CursorSnapshot(State state, int matchedCount, int scannedCount, int emittedCount, long expiresAtMillis, String message) {
            this.state = state;
            this.matchedCount = matchedCount;
            this.scannedCount = scannedCount;
            this.emittedCount = emittedCount;
            this.expiresAtMillis = expiresAtMillis;
            this.message = message;
        }

        static CursorSnapshot idle() {
            return new CursorSnapshot(State.IDLE, 0, 0, 0, 0L, null);
        }

        static CursorSnapshot closed(int matchedCount, int scannedCount, int emittedCount, long expiresAtMillis) {
            return new CursorSnapshot(State.CLOSED, matchedCount, scannedCount, emittedCount, expiresAtMillis, null);
        }

        static CursorSnapshot error(String message) {
            return new CursorSnapshot(State.ERROR, 0, 0, 0, 0L, message);
        }

        public State getState() {
            return state;
        }

        public int getMatchedCount() {
            return matchedCount;
        }

        public int getScannedCount() {
            return scannedCount;
        }

        public int getEmittedCount() {
            return emittedCount;
        }

        public int getRemainingCount() {
            int remaining = matchedCount - emittedCount;
            if (remaining < 0) {
                return 0;
            }
            return remaining;
        }

        public long getExpiresAtMillis() {
            return expiresAtMillis;
        }

        public String getMessage() {
            return message;
        }

        public boolean isReady() {
            return state == State.READY;
        }

        public boolean isOpen() {
            return state == State.SCANNING || state == State.READY;
        }
    }

    static final class CursorPage {
        private final CursorSnapshot snapshot;
        private final String[] paths;
        private final long[] mtimes;
        private final long[] sizes;
        private final byte[] kinds;
        private final int fromIndex;
        private final int toIndex;
        private final boolean hasMore;

        CursorPage(CursorSnapshot snapshot, String[] paths, long[] mtimes, long[] sizes, byte[] kinds, int fromIndex, int toIndex, boolean hasMore) {
            this.snapshot = snapshot;
            this.paths = paths;
            this.mtimes = mtimes;
            this.sizes = sizes;
            this.kinds = kinds;
            this.fromIndex = fromIndex;
            this.toIndex = toIndex;
            this.hasMore = hasMore;
        }

        static CursorPage notReady(CursorSnapshot snapshot) {
            return new CursorPage(snapshot, new String[0], new long[0], new long[0], new byte[0], 0, 0, false);
        }

        public CursorSnapshot getSnapshot() {
            return snapshot;
        }

        public String[] getPaths() {
            return paths;
        }

        public long[] getMtimes() {
            return mtimes;
        }

        public long[] getSizes() {
            return sizes;
        }

        public byte[] getKinds() {
            return kinds;
        }

        public int getFromIndex() {
            return fromIndex;
        }

        public int getToIndex() {
            return toIndex;
        }

        public int getCount() {
            return toIndex - fromIndex;
        }

        public boolean hasMore() {
            return hasMore;
        }
    }

    private static final class PackedEntries {
        private String[] paths = new String[INITIAL_CAPACITY];
        private long[] mtimes = new long[INITIAL_CAPACITY];
        private long[] sizes = new long[INITIAL_CAPACITY];
        private byte[] kinds = new byte[INITIAL_CAPACITY];
        private int size;

        void add(String path, long modifiedAt, long size, CameraFileKind kind) {
            ensureCapacity(this.size + 1);
            paths[this.size] = path;
            mtimes[this.size] = modifiedAt;
            sizes[this.size] = size;
            kinds[this.size] = kind.toPackedValue();
            this.size++;
        }

        int size() {
            return size;
        }

        void clear() {
            paths = new String[0];
            mtimes = new long[0];
            sizes = new long[0];
            kinds = new byte[0];
            size = 0;
        }

        private void ensureCapacity(int minCapacity) {
            if (paths.length >= minCapacity) {
                return;
            }
            int newCapacity = paths.length * 2;
            if (newCapacity < minCapacity) {
                newCapacity = minCapacity;
            }
            if (newCapacity < INITIAL_CAPACITY) {
                newCapacity = INITIAL_CAPACITY;
            }

            String[] freshPaths = new String[newCapacity];
            long[] freshMtimes = new long[newCapacity];
            long[] freshSizes = new long[newCapacity];
            byte[] freshKinds = new byte[newCapacity];

            for (int i = 0; i < size; i++) {
                freshPaths[i] = paths[i];
                freshMtimes[i] = mtimes[i];
                freshSizes[i] = sizes[i];
                freshKinds[i] = kinds[i];
            }

            paths = freshPaths;
            mtimes = freshMtimes;
            sizes = freshSizes;
            kinds = freshKinds;
        }
    }
}
