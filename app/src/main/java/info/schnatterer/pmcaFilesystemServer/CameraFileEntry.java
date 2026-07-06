package info.schnatterer.pmcaFilesystemServer;

import java.io.File;

final class CameraFileEntry {
    private final String path;
    private final long modifiedAt;
    private final long size;
    private final CameraFileKind kind;

    CameraFileEntry(String path, long modifiedAt, long size, CameraFileKind kind) {
        this.path = path;
        this.modifiedAt = modifiedAt;
        this.size = size;
        this.kind = kind;
    }

    public String getPath() {
        return path;
    }

    public String getName() {
        return new File(path).getName();
    }

    public long getModifiedAt() {
        return modifiedAt;
    }

    public long getSize() {
        return size;
    }

    public CameraFileKind getKind() {
        return kind;
    }
}
