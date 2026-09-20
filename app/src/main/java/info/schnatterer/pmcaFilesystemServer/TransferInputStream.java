package info.schnatterer.pmcaFilesystemServer;

import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Locale;

/** Reports bytes supplied to HTTP, not a client acknowledgement of receipt. */
final class TransferInputStream extends FilterInputStream {
    private final String title;
    private final long size;
    private final long started = System.nanoTime();
    private long lastReport = started;
    private long transferred;
    private long statusRevision = -1;

    TransferInputStream(InputStream input, String title, long size) {
        super(input);
        this.title = title;
        this.size = size;
        report(started, "Sending");
    }

    @Override
    public int read() throws IOException {
        int value = in.read();
        if (value >= 0) {
            advanced(1);
        }
        return value;
    }

    @Override
    public int read(byte[] bytes, int offset, int length) throws IOException {
        int count = in.read(bytes, offset, length);
        if (count > 0) {
            advanced(count);
        }
        return count;
    }

    private void advanced(int count) {
        transferred += count;
        long now = System.nanoTime();
        if (now - lastReport >= 1000000000L) {
            report(now, "Sending");
        }
    }

    private void report(long now, String state) {
        if (statusRevision == -2) {
            return;
        }
        lastReport = now;
        double seconds = Math.max(0.001, (now - started) / 1000000000.0);
        statusRevision = SyncStatus.getInstance().setMessageIfCurrent(statusRevision, String.format(Locale.US,
                "%s %s\n%.1f / %.1f MiB\n%.2f MiB/s | %.0f s",
                state, title, transferred / 1048576.0, size / 1048576.0,
                transferred / 1048576.0 / seconds, seconds));
    }

    @Override
    public void close() throws IOException {
        try {
            super.close();
        } finally {
            report(System.nanoTime(), transferred == size ? "Sent" : "Interrupted");
        }
    }
}
