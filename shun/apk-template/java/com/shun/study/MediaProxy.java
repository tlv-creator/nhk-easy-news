package com.shun.study;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;

/**
 * App 内 127.0.0.1 迷你 HTTP 服务 —— 给 WebView / 原生 MediaPlayer 提供音频流。
 *
 * 音频托管在自建 CDN（静态托管节点），地址表在 assets/cdn.json：
 *   { "86200": ["https://节点A/media/ep01.m4a", "https://节点B/media/ep01.m4a"], ... }
 * 一集对应 2 个节点做冗余 —— 某个节点冷启动 503 时自动换下一个。
 *
 * 为什么还要这个本机代理（音频明明可以直接连 CDN）：
 *   页面只跟本机代理说话，代理负责 Range 透传、Content-Type 纠正（audio/mp4）、
 *   以及跨域头 —— 客户端看到的是一个规规矩矩的静态文件服务器，行为可预期。
 *   同时 /app/... 伺服内置前端资源，让页面 origin 固定在 http://127.0.0.1:端口，
 *   这样 localStorage（观看记录 / 生词本）才持久。
 *
 * 路由：
 *   GET|HEAD  /media/<epId>.m4a   音频流（透传 Range，支持 206/416）
 *   GET       /resolve/<epId>     查到的上游直链（JSON，调试用）
 *   GET       /selftest/<epId>    自检：查表 + 首段拉取 + Range 验证（JSON）
 *   GET       /health             运行状态（JSON）
 *   OPTIONS   *                   CORS / Private Network Access 预检
 *
 * 只依赖 JDK + org.json（Android 自带 org.json），因此能在普通 JVM 上做端到端测试。
 */
public class MediaProxy {

    /** 普通浏览器 UA。 */
    public static final String DESKTOP_UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    + "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

    private static final int CONNECT_TIMEOUT = 15000;
    private static final int READ_TIMEOUT = 30000;
    private static final int BUF = 64 * 1024;

    private ServerSocket server;
    private final ExecutorService pool = Executors.newCachedThreadPool(r -> {
        Thread t = new Thread(r, "ccs-proxy");
        t.setDaemon(true);
        return t;
    });

    /** epId（B 站 ep_id，86200~86269）→ 自建 CDN 上的候选音频地址。 */
    private final Map<String, List<String>> cdnMap = new ConcurrentHashMap<String, List<String>>();
    private volatile boolean cdnLoaded = false;

    private volatile int port = -1;
    private volatile String lastError = "";
    private final AtomicLong reqCount = new AtomicLong();
    private final AtomicLong byteCount = new AtomicLong();

    // ---------------------------------------------------------------- 生命周期

    /** 启动本地服务，绑定 127.0.0.1 随机端口。 */
    public void start() throws IOException {
        start(0);
    }

    /**
     * 启动本地服务。
     * @param desiredPort 0 = 随机端口；固定端口 = origin 稳定（localStorage 能持久化）
     */
    public void start(int desiredPort) throws IOException {
        if (server != null) return;
        if (desiredPort > 0) {
            try {
                server = new ServerSocket(desiredPort, 64, InetAddress.getByName("127.0.0.1"));
            } catch (IOException e) {
                server = null;   // 端口被占 → 退回随机端口
            }
        }
        if (server == null) {
            server = new ServerSocket(0, 64, InetAddress.getByName("127.0.0.1"));
        }
        port = server.getLocalPort();
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop();
            }
        }, "ccs-proxy-accept");
        t.setDaemon(true);
        t.start();
    }

    public void stop() {
        try {
            if (server != null) server.close();
        } catch (IOException ignored) {
        }
        server = null;
        pool.shutdownNow();
    }

    public int getPort() {
        return port;
    }

    public String getLastError() {
        return lastError;
    }

    private void acceptLoop() {
        while (server != null && !server.isClosed()) {
            final Socket sock;
            try {
                sock = server.accept();
            } catch (IOException e) {
                if (server == null || server.isClosed()) return;
                continue;
            }
            try {
                pool.execute(new Runnable() {
                    @Override
                    public void run() {
                        handle(sock);
                    }
                });
            } catch (RuntimeException ignored) {
                closeQuietly(sock);
            }
        }
    }

    // ---------------------------------------------------------------- 路由

    private void handle(Socket sock) {
        try {
            sock.setSoTimeout(30000);
            sock.setTcpNoDelay(true);
            InputStream in = sock.getInputStream();
            OutputStream rawOut = sock.getOutputStream();

            String requestLine = readLine(in);
            if (requestLine == null || requestLine.isEmpty()) return;
            reqCount.incrementAndGet();

            String[] parts = requestLine.split(" ");
            String method = parts.length > 0 ? parts[0] : "GET";
            String rawPath = parts.length > 1 ? parts[1] : "/";

            Map<String, String> headers = new LinkedCaseInsensitiveMap();
            String line;
            while ((line = readLine(in)) != null && !line.isEmpty()) {
                int c = line.indexOf(':');
                if (c > 0) headers.put(line.substring(0, c).trim(), line.substring(c + 1).trim());
            }

            if ("OPTIONS".equalsIgnoreCase(method)) {
                writePreflight(rawOut);
                return;
            }

            String path = rawPath;
            int q = path.indexOf('?');
            if (q >= 0) path = path.substring(0, q);
            path = decode(path);

            if (path.startsWith("/media/")) {
                String epId = path.substring("/media/".length());
                int dot = epId.lastIndexOf('.');
                if (dot > 0) epId = epId.substring(0, dot);
                serveMedia(rawOut, epId, headers.get("Range"), "HEAD".equalsIgnoreCase(method));
                return;
            }
            if (path.startsWith("/app/")) {
                // 内置前端资源：/app/index.html → assets 里的 index.html
                serveAsset(rawOut, path.substring("/app/".length()), "HEAD".equalsIgnoreCase(method));
                return;
            }
            if (path.startsWith("/resolve/")) {
                String epId = path.substring("/resolve/".length());
                writeJson(rawOut, 200, resolveJson(epId));
                return;
            }
            if (path.startsWith("/selftest/")) {
                String epId = path.substring("/selftest/".length());
                writeJson(rawOut, 200, selfTest(epId));
                return;
            }
            if (path.equals("/health") || path.equals("/")) {
                JSONObject o = new JSONObject();
                o.put("ok", true);
                o.put("port", port);
                o.put("requests", reqCount.get());
                o.put("bytesServed", byteCount.get());
                o.put("lastError", lastError);
                o.put("cdnEntries", cdnMap.size());
                writeJson(rawOut, 200, o.toString());
                return;
            }
            writeText(rawOut, 404, "not found");
        } catch (Throwable e) {
            // 客户端提前断开（播放器 seek/取消）不是错误，别污染诊断行
            String msg = describe(e);
            if (msg.indexOf("Broken pipe") < 0 && msg.indexOf("Connection reset") < 0
                    && msg.indexOf("Socket closed") < 0) {
                lastError = "proxy: " + msg;
            }
        } finally {
            closeQuietly(sock);
        }
    }

    // ---------------------------------------------------------------- 音频流

    /**
     * 代理音频流。
     *
     * 本项目的音频**内置在 APK 里**（assets/audio/epNN.m4a），所以优先从 assets 伺服：
     *   1) assets 里有 → 直接吐字节（支持 Range，206），完全离线
     *   2) assets 里没有 → 回落到 cdn.json 里的远端地址（万一以后换成分发版）
     */
    private void serveMedia(OutputStream out, String epId, String range, boolean headOnly) throws IOException {
        // ---- ① 内置音频
        String assetName = "audio/" + epId + ".m4a";
        if (assets != null && assets.size(assetName) > 0) {
            serveAssetMedia(out, assetName, range, headOnly);
            return;
        }
        // ---- ② 远端
        List<String> urls = cdnUrls(epId);
        if (urls.isEmpty()) {
            lastError = "沒有 " + epId + " 的音頻（內置資源和 cdn.json 都沒有）";
            writeText(out, 502, lastError);
            return;
        }

        HttpURLConnection up = null;
        int code = 0;
        for (int attempt = 0; attempt < urls.size(); attempt++) {
            String url = urls.get(attempt);
            try {
                up = openUpstream(url, range);
                code = up.getResponseCode();
            } catch (Exception e) {
                lastError = "上游連接失敗: " + describe(e);
                if (up != null) { up.disconnect(); up = null; }
                continue;
            }
            if (code == 200 || code == 206 || code == 416) break;
            // 這個節點不可用（冷啟動 503 / 被限流）→ 換下一個
            lastError = "上游 HTTP " + code + " → 換下一個音頻節點";
            up.disconnect();
            up = null;
        }
        if (up == null) {
            writeText(out, 502, lastError.isEmpty() ? "所有音頻節點都不可用" : lastError);
            return;
        }
        if (code == 416) {
            // 范围不合法：把上游的 Content-Range 透回去
            String cr = up.getHeaderField("Content-Range");
            out.write(("HTTP/1.1 416 Range Not Satisfiable\r\n"
                    + "Content-Range: " + (cr == null ? "bytes */*" : cr) + "\r\n"
                    + "Accept-Ranges: bytes\r\n"
                    + "Content-Length: 0\r\n"
                    + "Connection: close\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1));
            out.flush();
            up.disconnect();
            return;
        }

        int status = up.getResponseCode();
        // 上游给的是 application/octet-stream，但文件其实就是 ISO-BMFF 音频（m4a/m4s）。
        // 系统 MediaPlayer 会按 Content-Type 选解封装器，octet-stream 会让它挑错 → 播放失败，
        // 所以这里统一声明成 audio/mp4。
        String contentType = "audio/mp4";
        long contentLength = up.getContentLengthLong();
        String contentRange = up.getHeaderField("Content-Range");

        StringBuilder head = new StringBuilder();
        head.append("HTTP/1.1 ").append(status == 206 ? "206 Partial Content" : "200 OK").append("\r\n");
        head.append("Content-Type: ").append(contentType).append("\r\n");
        head.append("Accept-Ranges: bytes\r\n");
        if (contentRange != null) head.append("Content-Range: ").append(contentRange).append("\r\n");
        if (contentLength >= 0) head.append("Content-Length: ").append(contentLength).append("\r\n");
        head.append("Cache-Control: no-store\r\n");
        head.append("Access-Control-Allow-Origin: *\r\n");
        head.append("Access-Control-Allow-Private-Network: true\r\n");
        head.append("Access-Control-Allow-Headers: Range, Content-Type\r\n");
        head.append("Access-Control-Allow-Methods: GET, HEAD, OPTIONS\r\n");
        head.append("Access-Control-Expose-Headers: Content-Length, Content-Range, Accept-Ranges\r\n");
        head.append("Connection: close\r\n\r\n");
        out.write(head.toString().getBytes(StandardCharsets.ISO_8859_1));
        out.flush();

        if (headOnly) {
            up.disconnect();
            return;
        }

        InputStream src = up.getInputStream();
        byte[] buf = new byte[BUF];
        long total = 0;
        try {
            int n;
            while ((n = src.read(buf)) > 0) {
                out.write(buf, 0, n);
                total += n;
            }
            out.flush();
        } finally {
            closeQuietly(src);
            up.disconnect();
        }
        byteCount.addAndGet(total);
        lastError = "";
    }

    // ---------------------------------------------------------------- 内置资源

    /**
     * 伺服**内置**音频（assets/audio/epNN.m4a），支持 Range（206）。
     * 为什么自己伺服而不是让客户端直接读 assets：
     *   WebView 的 <audio> 和原生 MediaPlayer 都更愿意吃一个规规矩矩的 HTTP 源，
     *   还能支持 seek（Range）。
     */
    private void serveAssetMedia(OutputStream out, String name, String range, boolean headOnly) throws IOException {
        long total = assets.size(name);
        long start = 0, end = total - 1;
        boolean partial = false;
        if (range != null && !range.trim().isEmpty()) {
            String r = range.trim();
            if (r.startsWith("bytes=")) r = r.substring(6);
            String[] parts = r.split("-", -1);
            try {
                if (parts.length == 2) {
                    if (!parts[0].trim().isEmpty()) start = Long.parseLong(parts[0].trim());
                    if (!parts[1].trim().isEmpty()) end = Long.parseLong(parts[1].trim());
                }
            } catch (NumberFormatException e) {
                start = 0; end = total - 1;
            }
            if (end >= total) end = total - 1;
            if (start > end || start >= total) {
                out.write(("HTTP/1.1 416 Range Not Satisfiable\r\n"
                        + "Content-Range: bytes */" + total + "\r\n"
                        + "Accept-Ranges: bytes\r\n"
                        + "Content-Length: 0\r\n"
                        + "Connection: close\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1));
                out.flush();
                return;
            }
            partial = true;
        }
        long len = end - start + 1;
        StringBuilder head = new StringBuilder();
        head.append("HTTP/1.1 ").append(partial ? "206 Partial Content" : "200 OK").append("\r\n");
        head.append("Content-Type: audio/mp4\r\n");
        head.append("Accept-Ranges: bytes\r\n");
        if (partial) {
            head.append("Content-Range: bytes ").append(start).append("-").append(end)
                    .append("/").append(total).append("\r\n");
        }
        head.append("Content-Length: ").append(len).append("\r\n");
        head.append("Cache-Control: no-store\r\n");
        head.append("Access-Control-Allow-Origin: *\r\n");
        head.append("Access-Control-Allow-Private-Network: true\r\n");
        head.append("Connection: close\r\n\r\n");
        out.write(head.toString().getBytes(StandardCharsets.ISO_8859_1));
        out.flush();
        if (headOnly) return;
        InputStream in = null;
        try {
            in = assets.openRange(name, start, len);
            if (in == null) throw new IOException("openRange failed: " + name);
            byte[] buf = new byte[BUF];
            long wrote = 0;
            int n;
            while (wrote < len
                    && (n = in.read(buf, 0, (int) Math.min(buf.length, len - wrote))) > 0) {
                out.write(buf, 0, n);
                wrote += n;
            }
            out.flush();
        } finally {
            closeQuietly(in);
        }
        byteCount.addAndGet(len);
        lastError = "";
    }

    /** 内置前端资源的来源（Android 里用 AssetManager 实现；JVM 测试时可以不设）。 */
    public interface AssetSource {
        InputStream open(String name) throws IOException;
        long size(String name);
        String mime(String name);
        /** 取一段字节（HTTP Range 用）；不支持就返回 null */
        InputStream openRange(String name, long offset, long length) throws IOException;
    }

    private AssetSource assets;

    public void setAssetSource(AssetSource src) {
        this.assets = src;
    }

    /**
     * 伺服 APK 内置的前端资源。
     * 为什么不让 WebView 直接读 file:///android_asset/：
     *   file:// 源的 localStorage 不持久 → 观看记录 / 生词本会丢。
     *   走 http://127.0.0.1:固定端口 就是一个稳定的 origin，localStorage 正常持久化。
     */
    private void serveAsset(OutputStream out, String name, boolean headOnly) throws IOException {
        if (assets == null) {
            writeText(out, 404, "no asset source");
            return;
        }
        if (name.contains("..") || name.startsWith("/")) {
            writeText(out, 403, "bad path");
            return;
        }
        InputStream in = null;
        try {
            in = assets.open(name);
        } catch (IOException e) {
            writeText(out, 404, "asset not found: " + name);
            return;
        }
        if (in == null) {
            writeText(out, 404, "asset not found: " + name);
            return;
        }
        long len = assets.size(name);
        String mime = assets.mime(name);
        StringBuilder head = new StringBuilder();
        head.append("HTTP/1.1 200 OK\r\n");
        head.append("Content-Type: ").append(mime).append("\r\n");
        head.append("Content-Length: ").append(len).append("\r\n");
        // 换版本后不能拿到旧的缓存
        head.append("Cache-Control: no-store, no-cache, must-revalidate\r\n");
        head.append("Pragma: no-cache\r\n");
        head.append("Access-Control-Allow-Origin: *\r\n");
        head.append("Connection: close\r\n\r\n");
        out.write(head.toString().getBytes(StandardCharsets.ISO_8859_1));
        out.flush();
        if (headOnly) {
            closeQuietly(in);
            return;
        }
        byte[] buf = new byte[BUF];
        try {
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            out.flush();
        } finally {
            closeQuietly(in);
        }
    }

    private HttpURLConnection openUpstream(String url, String range) throws IOException {        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(CONNECT_TIMEOUT);
        c.setReadTimeout(READ_TIMEOUT);
        c.setInstanceFollowRedirects(true);
        c.setRequestProperty("User-Agent", DESKTOP_UA);
        c.setRequestProperty("Accept", "*/*");
        if (range != null && !range.trim().isEmpty()) {
            c.setRequestProperty("Range", range.trim());
        }
        return c;
    }

    // ---------------------------------------------------------------- 解析

    /** 取某集在自建 CDN 上的主地址。 */
    public String resolve(String epId) throws IOException {
        List<String> us = cdnUrls(epId);
        if (us.isEmpty()) throw new IOException("cdn.json 里没有 " + epId + " 的地址");
        lastError = "";
        return us.get(0);
    }

    /** 读 assets/cdn.json（{ "epId": ["url", ...] }），只读一次。 */
    private void loadCdnMap() {
        if (cdnLoaded) return;
        cdnLoaded = true;
        if (assets == null) return;
        InputStream in = null;
        try {
            // 打包时 cdn.json 可能在 assets 根，也可能在 assets/data/ 下，两个都试
            try {
                in = assets.open("cdn.json");
            } catch (IOException e1) {
                in = assets.open("data/cdn.json");
            }
            String body = readAll(in);
            JSONObject root = new JSONObject(body);
            Iterator<String> it = root.keys();
            while (it.hasNext()) {
                String k = it.next();
                Object v = root.opt(k);
                List<String> list = new ArrayList<String>();
                if (v instanceof JSONArray) {
                    JSONArray arr = (JSONArray) v;
                    for (int i = 0; i < arr.length(); i++) {
                        String u = arr.optString(i, "");
                        if (!u.isEmpty()) list.add(u);
                    }
                } else if (v instanceof String && !((String) v).isEmpty()) {
                    list.add((String) v);
                }
                if (!list.isEmpty()) cdnMap.put(k, list);
            }
        } catch (Throwable e) {
            lastError = "cdn.json 讀取失敗: " + describe(e);
        } finally {
            closeQuietly(in);
        }
    }

    /** 某集的候选音频地址（主 + 备用节点）。 */
    public List<String> cdnUrls(String epId) {
        loadCdnMap();
        List<String> l = cdnMap.get(epId);
        return l == null ? Collections.<String>emptyList() : l;
    }

    private String resolveJson(String epId) {
        JSONObject o = new JSONObject();
        try {
            o.put("epId", epId);
            o.put("url", resolve(epId));
            o.put("ok", true);
        } catch (Exception e) {
            try {
                o.put("ok", false);
                o.put("error", describe(e));
            } catch (Exception ignored) {
            }
        }
        return o.toString();
    }

    // ---------------------------------------------------------------- 自检

    /**
     * 自检：解析 → 拉首段 → 验证 Range。
     * 装机后在手机上点一下就能知道链路断在哪一环，不用猜。
     */
    public String selfTest(String epId) {
        JSONObject o = new JSONObject();
        long t0 = System.currentTimeMillis();
        try {
            String url = resolve(epId);
            o.put("resolve", "ok");
            o.put("url", url);
            o.put("resolveMs", System.currentTimeMillis() - t0);

            HttpURLConnection c = openUpstream(url, "bytes=0-65535");
            int code = c.getResponseCode();
            o.put("upstreamStatus", code);
            o.put("contentType", String.valueOf(c.getHeaderField("Content-Type")));
            o.put("contentRange", String.valueOf(c.getHeaderField("Content-Range")));
            o.put("acceptRanges", String.valueOf(c.getHeaderField("Accept-Ranges")));
            InputStream is = c.getInputStream();
            byte[] buf = new byte[BUF];
            int n = 0;
            try {
                // 读满 64KB（一次 read 往往只给一部分）
                while (n < 65536) {
                    int k = is.read(buf, 0, Math.min(buf.length, 65536 - n));
                    if (k <= 0) break;
                    n += k;
                }
            } catch (IOException ignored) {
            }
            o.put("firstChunkBytes", n);
            is.close();
            c.disconnect();
            o.put("ok", code == 200 || code == 206);
            if (n <= 0) o.put("error", "首段读取为 0 字节");
        } catch (Exception e) {
            try {
                o.put("ok", false);
                o.put("error", describe(e));
            } catch (Exception ignored) {
            }
        }
        try {
            o.put("port", port);
            o.put("lastError", lastError);
            o.put("requests", reqCount.get());
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    // ---------------------------------------------------------------- 小工具

    private void writeJson(OutputStream out, int status, String json) throws IOException {
        byte[] b = json.getBytes(StandardCharsets.UTF_8);
        String head = "HTTP/1.1 " + status + " OK\r\n"
                + "Content-Type: application/json; charset=utf-8\r\n"
                + "Content-Length: " + b.length + "\r\n"
                + "Cache-Control: no-store\r\n"
                + "Access-Control-Allow-Origin: *\r\n"
                + "Access-Control-Allow-Private-Network: true\r\n"
                + "Connection: close\r\n\r\n";
        out.write(head.getBytes(StandardCharsets.ISO_8859_1));
        out.write(b);
        out.flush();
    }

    private void writeText(OutputStream out, int status, String text) throws IOException {
        byte[] b = text.getBytes(StandardCharsets.UTF_8);
        String head = "HTTP/1.1 " + status + " Error\r\n"
                + "Content-Type: text/plain; charset=utf-8\r\n"
                + "Content-Length: " + b.length + "\r\n"
                + "Connection: close\r\n\r\n";
        out.write(head.getBytes(StandardCharsets.ISO_8859_1));
        out.write(b);
        out.flush();
    }

    private void writePreflight(OutputStream out) throws IOException {
        String head = "HTTP/1.1 204 No Content\r\n"
                + "Access-Control-Allow-Origin: *\r\n"
                + "Access-Control-Allow-Private-Network: true\r\n"
                + "Access-Control-Allow-Methods: GET, HEAD, OPTIONS\r\n"
                + "Access-Control-Allow-Headers: Range, Content-Type\r\n"
                + "Access-Control-Max-Age: 86400\r\n"
                + "Content-Length: 0\r\n"
                + "Connection: close\r\n\r\n";
        out.write(head.getBytes(StandardCharsets.ISO_8859_1));
        out.flush();
    }

    private static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream(128);
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') buf.write(c);
            if (buf.size() > 8192) break;
        }
        if (c == -1 && buf.size() == 0) return null;
        return new String(buf.toByteArray(), StandardCharsets.ISO_8859_1);
    }

    private static String readAll(InputStream in) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        return new String(bos.toByteArray(), StandardCharsets.UTF_8);
    }

    private static void closeQuietly(Closeable c) {
        if (c == null) return;
        try {
            c.close();
        } catch (Exception ignored) {
        }
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (Exception e) {
            return s;
        }
    }

    private static String decode(String s) {
        try {
            return URLDecoder.decode(s, "UTF-8");
        } catch (Exception e) {
            return s;
        }
    }

    static String describe(Throwable e) {
        if (e == null) return "未知错误";
        String m = e.getMessage();
        String cls = e.getClass().getSimpleName();
        return m == null || m.isEmpty() ? cls : (cls + ": " + m);
    }

    /** 极简的大小写不敏感 header 表。 */
    static class LinkedCaseInsensitiveMap extends ConcurrentHashMap<String, String> {
        @Override
        public String put(String k, String v) {
            return super.put(k == null ? "" : k.toLowerCase(java.util.Locale.US), v);
        }

        @Override
        public String get(Object k) {
            return super.get(k == null ? "" : k.toString().toLowerCase(java.util.Locale.US));
        }
    }
}
