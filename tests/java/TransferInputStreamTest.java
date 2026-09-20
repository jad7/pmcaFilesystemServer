package info.schnatterer.pmcaFilesystemServer;

import java.io.ByteArrayInputStream;
import java.util.Arrays;

public class TransferInputStreamTest {
    public static void main(String[] args) throws Exception {
        byte[] data = new byte[131072];
        Arrays.fill(data, (byte) 42);
        TransferInputStream stream = new TransferInputStream(
                new ByteArrayInputStream(data), "2 / 1480\ntest.ARW", data.length);
        byte[] received = new byte[data.length];
        int offset = 0;
        while (offset < received.length) {
            int count = stream.read(received, offset, Math.min(4096, received.length - offset));
            if (count < 0) throw new AssertionError("Premature EOF");
            offset += count;
        }
        stream.close();
        if (!Arrays.equals(data, received)) throw new AssertionError("Corrupt stream");
        String status = SyncStatus.getInstance().getSnapshot().getMessage();
        if (!status.startsWith("Sent 2 / 1480") || !status.contains("MiB/s")) {
            throw new AssertionError(status);
        }
        stream = new TransferInputStream(new ByteArrayInputStream(data), "test", data.length);
        stream.read();
        stream.close();
        if (!SyncStatus.getInstance().getSnapshot().getMessage().startsWith("Interrupted")) {
            throw new AssertionError("Missing interruption status");
        }
        stream = new TransferInputStream(new ByteArrayInputStream(data), "test", data.length);
        SyncStatus.getInstance().setMessage("Sync complete");
        stream.close();
        if (!"Sync complete".equals(SyncStatus.getInstance().getSnapshot().getMessage())) {
            throw new AssertionError("Late stream close replaced final status");
        }
        System.out.println("TransferInputStream tests passed");
    }
}
