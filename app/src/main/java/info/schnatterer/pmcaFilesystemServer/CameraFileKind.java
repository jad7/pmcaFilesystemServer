package info.schnatterer.pmcaFilesystemServer;

import java.util.Locale;

enum CameraFileKind {
    IMAGE,
    RAW,
    VIDEO,
    OTHER;

    private static final CameraFileKind[] VALUES = values();

    public static CameraFileKind fromPath(String path) {
        String lower = path.toLowerCase(Locale.US);
        if (lower.endsWith(".arw")) {
            return RAW;
        }
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
            return IMAGE;
        }
        if (lower.endsWith(".mts") || lower.endsWith(".mp4")) {
            return VIDEO;
        }
        return OTHER;
    }

    public static CameraFileKind fromQuery(String value) {
        if (value == null || value.length() == 0 || "all".equalsIgnoreCase(value)) {
            return null;
        }
        if ("image".equalsIgnoreCase(value)) {
            return IMAGE;
        }
        if ("raw".equalsIgnoreCase(value)) {
            return RAW;
        }
        if ("video".equalsIgnoreCase(value)) {
            return VIDEO;
        }
        if ("other".equalsIgnoreCase(value)) {
            return OTHER;
        }
        throw new IllegalArgumentException("unknown kind");
    }

    public byte toPackedValue() {
        return (byte) ordinal();
    }

    public static CameraFileKind fromPackedValue(byte value) {
        int index = value;
        if (index < 0 || index >= VALUES.length) {
            return OTHER;
        }
        return VALUES[index];
    }

    public String wireName() {
        switch (this) {
            case IMAGE:
                return "image";
            case RAW:
                return "raw";
            case VIDEO:
                return "video";
            default:
                return "other";
        }
    }
}
