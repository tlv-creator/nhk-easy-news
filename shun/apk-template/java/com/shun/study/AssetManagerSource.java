package com.shun.study;

import android.content.res.AssetFileDescriptor;
import android.content.res.AssetManager;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;

/**
 * 把 APK 内置的 assets 交给 MediaProxy / NativePlayer 用。
 *
 * 为什么要绕这一圈（不让 WebView 直接读 file:///android_asset/）：
 *   file:// 源的 localStorage 不持久 —— 观看记录、生词本会在每次启动时丢。
 *   走 http://127.0.0.1:18789/app/... 就是一个稳定的 origin，localStorage 正常持久化。
 *
 * 音频（assets/audio/epNN.m4a）：打包时用 `aapt2 link -0 m4a` 让它**不压缩存储**，
 *   否则 AssetManager.openFd() 会抛「file is probably compressed」——
 *   这时只能整块读出来播，seek 会很难受。
 */
public class AssetManagerSource implements MediaProxy.AssetSource {

    private final AssetManager assets;

    public AssetManagerSource(AssetManager assets) {
        this.assets = assets;
    }

    @Override
    public InputStream open(String name) throws IOException {
        try {
            return assets.open(name);
        } catch (IOException e) {
            return null;
        }
    }

    /**
     * 未压缩的资源可以直接拿 fd（assets 里的 m4a 用 -0 打包 → STORED）。
     * 拿不到就返回 null（调用方退回普通流）。
     */
    public AssetFileDescriptor openFd(String name) {
        try {
            return assets.openFd(name);
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * 取一段字节（用于 HTTP Range）。
     * @param offset 起始偏移（相对文件开头）
     * @param length 长度（-1 = 到结尾）
     */
    @Override
    public InputStream openRange(String name, long offset, long length) throws IOException {
        AssetFileDescriptor afd = openFd(name);
        if (afd != null) {
            try {
                // createInputStream() 从 startOffset 开始（未压缩资源就是文件流，skip 有效）
                InputStream in = afd.createInputStream();
                long skipped = 0;
                while (skipped < offset) {
                    long n = in.skip(offset - skipped);
                    if (n <= 0) break;
                    skipped += n;
                }
                return in;
            } catch (Exception e) {
                // 落到下面的普通流
            }
        }
        InputStream in = open(name);
        if (in == null) return null;
        long skipped = 0;
        while (skipped < offset) {
            long n = in.skip(offset - skipped);
            if (n <= 0) break;
            skipped += n;
        }
        return in;
    }

    @Override
    public long size(String name) {
        try {
            // 未压缩的资源可以直接拿长度
            return assets.openFd(name).getLength();
        } catch (Exception e) {
            // 压缩过的资源拿不到 fd，退回读一遍算长度
            InputStream in = null;
            try {
                in = assets.open(name);
                ByteArrayOutputStream bos = new ByteArrayOutputStream();
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
                return bos.size();
            } catch (Exception e2) {
                return 0;
            } finally {
                if (in != null) {
                    try {
                        in.close();
                    } catch (Exception ignored) {
                    }
                }
            }
        }
    }

    @Override
    public String mime(String name) {
        String n = name.toLowerCase(java.util.Locale.US);
        if (n.endsWith(".html") || n.endsWith(".htm")) return "text/html; charset=utf-8";
        if (n.endsWith(".js")) return "text/javascript; charset=utf-8";
        if (n.endsWith(".css")) return "text/css; charset=utf-8";
        if (n.endsWith(".json")) return "application/json; charset=utf-8";
        if (n.endsWith(".webmanifest")) return "application/manifest+json; charset=utf-8";
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".jpg") || n.endsWith(".jpeg")) return "image/jpeg";
        if (n.endsWith(".m4a") || n.endsWith(".m4s")) return "audio/mp4";
        if (n.endsWith(".mp3")) return "audio/mpeg";
        if (n.endsWith(".txt")) return "text/plain; charset=utf-8";
        if (n.endsWith(".ico")) return "image/x-icon";
        return "application/octet-stream";
    }
}
