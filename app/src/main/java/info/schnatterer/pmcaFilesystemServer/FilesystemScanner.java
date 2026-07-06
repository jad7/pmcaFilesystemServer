package info.schnatterer.pmcaFilesystemServer;

import java.io.File;
import java.util.List;

/*
 * Compatibility wrapper for the existing HTML root page.
 * The real index now lives in CameraFileIndex and is built once per session.
 */
class FilesystemScanner {

    public static List<File> getRawsOnExternalStorage() {
        return CameraFileIndex.getInstance().getFilesByKind(CameraFileKind.RAW);
    }

    public static List<File> getJpegsOnExternalStorage() {
        return CameraFileIndex.getInstance().getFilesByKind(CameraFileKind.IMAGE);
    }

    public static List<File> getVideosOnExternalStorage() {
        return CameraFileIndex.getInstance().getFilesByKind(CameraFileKind.VIDEO);
    }
}
