package info.schnatterer.pmcaFilesystemServer;

import android.os.Environment;

import java.io.File;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/*
 * Compatibility wrapper for the existing HTML root page.
 * The machine API now uses singleton cursor sessions instead of a full snapshot.
 */
class FilesystemScanner {

    public static List<File> getRawsOnExternalStorage() {
        return scanFiles(CameraFileKind.RAW);
    }

    public static List<File> getJpegsOnExternalStorage() {
        return scanFiles(CameraFileKind.IMAGE);
    }

    public static List<File> getVideosOnExternalStorage() {
        return scanFiles(CameraFileKind.VIDEO);
    }

    private static List<File> scanFiles(CameraFileKind kind) {
        File root = Environment.getExternalStorageDirectory();
        if (root == null) {
            return Collections.emptyList();
        }

        ArrayList<File> files = new ArrayList<File>();
        ArrayDeque<File> stack = new ArrayDeque<File>();
        stack.push(root);

        while (!stack.isEmpty()) {
            File current = stack.pop();
            File[] children = current.listFiles();
            if (children == null) {
                continue;
            }
            for (int i = 0; i < children.length; i++) {
                File child = children[i];
                if (child.isDirectory()) {
                    stack.push(child);
                    continue;
                }
                if (!child.isFile()) {
                    continue;
                }
                if (CameraFileKind.fromPath(child.getAbsolutePath()) != kind) {
                    continue;
                }
                files.add(child.getAbsoluteFile());
            }
        }

        return files;
    }
}
