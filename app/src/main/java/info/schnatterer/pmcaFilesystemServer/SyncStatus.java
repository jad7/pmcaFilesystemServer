package info.schnatterer.pmcaFilesystemServer;

import java.util.ArrayList;
import java.util.List;

final class SyncStatus {
    interface Listener {
        void onStatusChanged(StatusSnapshot snapshot);
    }

    static final class StatusSnapshot {
        private final String message;
        private final long updatedAtMillis;

        StatusSnapshot(String message, long updatedAtMillis) {
            this.message = message;
            this.updatedAtMillis = updatedAtMillis;
        }

        public String getMessage() {
            return message;
        }

        public long getUpdatedAtMillis() {
            return updatedAtMillis;
        }
    }

    private static final SyncStatus INSTANCE = new SyncStatus();

    private final Object lock = new Object();
    private final List<Listener> listeners = new ArrayList<Listener>();
    private String message = "idle";
    private long updatedAtMillis = System.currentTimeMillis();
    private long revision;

    static SyncStatus getInstance() {
        return INSTANCE;
    }

    StatusSnapshot getSnapshot() {
        synchronized (lock) {
            return new StatusSnapshot(message, updatedAtMillis);
        }
    }

    void setMessage(String message) {
        setMessageIfCurrent(-1, message);
    }

    // A late stream close must not replace newer client or transfer status.
    long setMessageIfCurrent(long expectedRevision, String message) {
        StatusSnapshot snapshot;
        List<Listener> listenersCopy;
        long newRevision;
        synchronized (lock) {
            if (expectedRevision != -1 && expectedRevision != revision) {
                return -2;
            }
            newRevision = ++revision;
            this.message = message == null || message.length() == 0 ? "idle" : message;
            this.updatedAtMillis = System.currentTimeMillis();
            snapshot = new StatusSnapshot(this.message, this.updatedAtMillis);
            listenersCopy = new ArrayList<Listener>(listeners);
        }
        for (int i = 0; i < listenersCopy.size(); i++) {
            listenersCopy.get(i).onStatusChanged(snapshot);
        }
        return newRevision;
    }

    void registerListener(Listener listener) {
        if (listener == null) {
            return;
        }
        synchronized (lock) {
            if (!listeners.contains(listener)) {
                listeners.add(listener);
            }
        }
    }

    void unregisterListener(Listener listener) {
        if (listener == null) {
            return;
        }
        synchronized (lock) {
            listeners.remove(listener);
        }
    }
}
