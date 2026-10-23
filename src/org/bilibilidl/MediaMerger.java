package org.bilibilidl;

import android.content.ContentValues;
import android.content.ContentResolver;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import java.io.File;
import java.io.FileInputStream;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;

/** Small Android-native helper for DASH muxing and scoped-storage export. */
public final class MediaMerger {
    private MediaMerger() {}

    public static boolean merge(String videoPath, String audioPath, String outputPath) {
        MediaExtractor ve = new MediaExtractor();
        MediaExtractor ae = new MediaExtractor();
        MediaMuxer muxer = null;
        try {
            ve.setDataSource(videoPath);
            ae.setDataSource(audioPath);
            muxer = new MediaMuxer(outputPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            int videoTrack = -1, audioTrack = -1;
            int videoInputTrack = -1, audioInputTrack = -1;
            for (int i=0; i<ve.getTrackCount(); i++) {
                MediaFormat f = ve.getTrackFormat(i);
                String mime = f.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("video/")) { videoTrack = muxer.addTrack(f); videoInputTrack = i; ve.selectTrack(i); break; }
            }
            for (int i=0; i<ae.getTrackCount(); i++) {
                MediaFormat f = ae.getTrackFormat(i);
                String mime = f.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("audio/")) { audioTrack = muxer.addTrack(f); audioInputTrack = i; ae.selectTrack(i); break; }
            }
            if (videoTrack < 0 || audioTrack < 0) throw new IllegalStateException("Missing audio or video track");
            muxer.start();
            copySamples(ve, muxer, videoTrack, videoInputTrack);
            copySamples(ae, muxer, audioTrack, audioInputTrack);
            muxer.stop();
            return new File(outputPath).length() > 0;
        } catch (Exception e) {
            try { new File(outputPath).delete(); } catch (Exception ignored) {}
            return false;
        } finally {
            try { ve.release(); } catch (Exception ignored) {}
            try { ae.release(); } catch (Exception ignored) {}
            if (muxer != null) try { muxer.release(); } catch (Exception ignored) {}
        }
    }

    private static void copySamples(MediaExtractor ex, MediaMuxer muxer, int outputTrack, int inputTrack) throws Exception {
        MediaFormat fmt = ex.getTrackFormat(inputTrack);
        int max = 1024 * 1024;
        if (fmt.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) max = Math.max(max, fmt.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE));
        ByteBuffer buffer = ByteBuffer.allocateDirect(max);
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (true) {
            buffer.clear();
            int size = ex.readSampleData(buffer, 0);
            if (size < 0) break;
            buffer.position(0); buffer.limit(size);
            info.offset = 0; info.size = size; info.presentationTimeUs = ex.getSampleTime();
            info.flags = ex.getSampleFlags();
            muxer.writeSampleData(outputTrack, buffer, info);
            ex.advance();
        }
    }

    /** Export a completed file to public Downloads without requesting broad storage permission. */
    public static String exportToDownloads(Context context, String sourcePath, String displayName) {
        File src = new File(sourcePath);
        if (!src.isFile()) return "Source file not found";
        ContentResolver resolver = context.getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, displayName);
        String lowerName = displayName.toLowerCase(java.util.Locale.ROOT);
        String mime;
        if (lowerName.endsWith(".jpg") || lowerName.endsWith(".jpeg")) mime = "image/jpeg";
        else if (lowerName.endsWith(".png")) mime = "image/png";
        else if (lowerName.endsWith(".webp")) mime = "image/webp";
        else if (lowerName.endsWith(".gif")) mime = "image/gif";
        else if (lowerName.endsWith(".xml")) mime = "application/xml";
        else if (lowerName.endsWith(".srt")) mime = "application/x-subrip";
        else mime = "video/mp4";
        values.put(MediaStore.MediaColumns.MIME_TYPE, mime);
        if (Build.VERSION.SDK_INT >= 29) values.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/bilibilidl");
        Uri uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
        if (uri == null) return "Could not create Downloads entry";
        try (FileInputStream in = new FileInputStream(src); OutputStream out = resolver.openOutputStream(uri)) {
            if (out == null) throw new IllegalStateException("Could not open destination");
            byte[] buf = new byte[1024*128]; int n;
            while ((n=in.read(buf)) >= 0) out.write(buf,0,n);
            out.flush();
            return uri.toString();
        } catch (Exception e) {
            try { resolver.delete(uri, null, null); } catch (Exception ignored) {}
            return "Export failed: " + e.getMessage();
        }
    }
}
