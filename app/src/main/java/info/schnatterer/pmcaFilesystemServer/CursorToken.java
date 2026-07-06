package info.schnatterer.pmcaFilesystemServer;

import android.util.Base64;

import java.io.UnsupportedEncodingException;

final class CursorToken {
    private final long modifiedAt;
    private final String path;

    CursorToken(long modifiedAt, String path) {
        this.modifiedAt = modifiedAt;
        this.path = path;
    }

    public long getModifiedAt() {
        return modifiedAt;
    }

    public String getPath() {
        return path;
    }

    public static String encode(CameraFileEntry entry) {
        String raw = entry.getModifiedAt() + "\n" + entry.getPath();
        try {
            return Base64.encodeToString(raw.getBytes("UTF-8"), Base64.NO_WRAP | Base64.URL_SAFE | Base64.NO_PADDING);
        } catch (UnsupportedEncodingException e) {
            throw new IllegalStateException(e);
        }
    }

    public static CursorToken decode(String token) {
        try {
            byte[] decoded = Base64.decode(token, Base64.NO_WRAP | Base64.URL_SAFE);
            String raw = new String(decoded, "UTF-8");
            int split = raw.indexOf('\n');
            if (split < 0) {
                throw new IllegalArgumentException("invalid cursor");
            }
            long modifiedAt = Long.parseLong(raw.substring(0, split));
            String path = raw.substring(split + 1);
            return new CursorToken(modifiedAt, path);
        } catch (UnsupportedEncodingException e) {
            throw new IllegalArgumentException("invalid cursor", e);
        }
    }
}
