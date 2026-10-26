package com.shun.study;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 本機音頻緩存：把音頻落盤到 App 私有目錄，離線也能聽。
 *
 * 為什麼要它：
 *   原生 MediaPlayer 直連 CDN 拉流，播完什麼都不留 —— 同一集再聽一次又要下 12MB。
 *   落盤之後：① 第二次聽 0 流量 ② 沒網也能聽 ③ 隨時能一次刪光，不佔手機空間。
 *
 * 存哪：getFilesDir()/audio/ —— 不會被系統當垃圾清掉（cacheDir 會被悄悄清掉），
 *       但使用者可以在 App 裡一鍵刪除。
 */
public class AudioCache {

    /**
     * 音頻 URL → 唯一集 id + 真實擴展名。
     *
     * 為什麼不能再用單純的 "ep(\\d+)\\.m4a"：
     *   hub 裡四個項目都用 epNN.m4a 當檔名（魔卡 / 若草 / 哆啦 / あま），
     *   只認檔名 → 魔卡第1話和若草第1話會搶同一個快取檔，下載一集覆蓋另一集，
     *   播放時還會拿到別的項目的音頻。所以 id 必須帶上域名 → 全域唯一。
     *
     *   f3rpiqeouy6c0.space.mcode.cn/media/ep01.m4a → f3rpiqeouy6c0_ep01 (.m4a)
     *   uvueu8gk74hpp.space.mcode.cn/audio/ep01.mp3 → uvueu8gk74hpp_ep01 (.mp3)
     *   tcw0ti0k7p0hd.space.mcode.cn/audio/E01.m4a  → tcw0ti0k7p0hd_E01  (.m4a)
     */
    private static final Pattern URL_ID = Pattern.compile(
            "^(?:https?://)?([A-Za-z0-9]+)\\.[^/]+/(?:media|audio)/([A-Za-z]*[0-9]+)\\.(m4a|mp3|m4s|aac|wav|ogg|flac)$");
    private static final Pattern URL_ANY = Pattern.compile(
            "^(?:https?://)?([A-Za-z0-9]+)\\.[^/]+/(?:media|audio)/([A-Za-z]*[0-9]+)\\.[A-Za-z0-9]+$");

    private final File dir;
    private final AssetManagerSource assets;
    private Thread worker;
    private volatile String state = "idle";   // idle | downloading | done | error
    private volatile String curEp = "";
    private volatile String curErr = "";
    private volatile double progress = 0;

    public AudioCache(Context ctx) {
        this(ctx, null);
    }

    public AudioCache(Context ctx, AssetManagerSource assets) {
        this.dir = new File(ctx.getApplicationContext().getFilesDir(), "audio");
        this.assets = assets;
        if (!dir.exists()) dir.mkdirs();
    }

    /** 内置音频在 APK 里的固定 id 前缀（跟域名无关，换 CDN 域名也不会失效） */
    public static String assetId(String epId) {
        return "shun_" + (epId == null ? "" : epId);
    }

    // ---------------------------------------------------------------- 查詢

    private static String safe(String s) {
        return String.valueOf(s).replaceAll("[^A-Za-z0-9_-]", "_");
    }

    /** 音頻檔的擴展名（魔卡/若草/あま m4a，哆啦 mp3）—— 必須跟源一致，否則解碼會炸 */
    private static final String[] AUD_EXT = { ".m4a", ".mp3", ".m4s", ".aac", ".wav", ".ogg", ".flac" };

    public File fileFor(String epId) {
        return new File(dir, safe(epId) + ".m4a");
    }

    /** 用真實擴展名建檔（mp3 存成 .m4a 會解不出聲） */
    private File fileForExt(String id, String ext) {
        return new File(dir, safe(id) + "." + ext);
    }

    public boolean has(String epId) {
        if (epId == null || epId.isEmpty()) return false;
        for (int i = 0; i < AUD_EXT.length; i++) {
            File f = new File(dir, safe(epId) + AUD_EXT[i]);
            if (f.exists() && f.length() > 1024) return true;
        }
        return false;
    }

    /** 從 URL 派生唯一 id（域名前綴_檔名），認不出就回傳 null */
    public static String idFor(String url) {
        if (url == null) return null;
        Matcher m = URL_ID.matcher(url.trim());
        if (!m.matches()) {
            m = URL_ANY.matcher(url.trim());
            if (!m.matches()) return null;
            return m.group(1) + "_" + m.group(2);
        }
        return m.group(1) + "_" + m.group(2);
    }

    /** 從 URL 派生擴展名（認不出預設 m4a） */
    private static String extFor(String url) {
        if (url == null) return "m4a";
        Matcher m = URL_ID.matcher(url.trim());
        return m.matches() ? m.group(3) : "m4a";
    }

    /**
     * 從任意音頻地址裡认出集號（epNN.m4a），本地有就返回本地文件。
     * 播放時優先用本地 —— 這樣離線也能播。
     *
     * @param epId 集號（ep01）。本项目的内置副本用 shun_epNN 命名，与域名解耦。
     */
    public File localFor(String url, String epId) {
        // ① 内置副本（shun_epNN）—— 最优先，域名换了也不丢
        if (epId != null && !epId.isEmpty()) {
            for (int i = 0; i < AUD_EXT.length; i++) {
                File g = fileForExt(assetId(epId), AUD_EXT[i].substring(1));
                if (g.exists() && g.length() > 1024) return g;
            }
        }
        if (url == null) return null;
        String id = idFor(url);
        if (id == null) return null;
        File f = fileForExt(id, extFor(url));
        if (f.exists() && f.length() > 1024) return f;
        // 擴展名對不上（源站換過格式）→ 挨個試一遍
        for (int i = 0; i < AUD_EXT.length; i++) {
            File g = fileForExt(id, AUD_EXT[i].substring(1));
            if (g.exists() && g.length() > 1024) return g;
        }
        return null;
    }

    /** 兼容老调用（只给地址） */
    public File localFor(String url) {
        return localFor(url, null);
    }

    /** 已緩存的集數 + 總字節 + 明細 */
    public JSONObject stat() {
        JSONObject o = new JSONObject();
        JSONArray items = new JSONArray();
        long total = 0;
        int count = 0;
        File[] fs = dir.listFiles();
        if (fs != null) {
            for (File f : fs) {
                if (!f.isFile()) continue;
                String nm = f.getName();
                if (nm.endsWith(".url")) continue;              // 旁掛的源地址備忘
                boolean audio = false;
                for (int i = 0; i < AUD_EXT.length; i++) {
                    if (nm.endsWith(AUD_EXT[i])) { audio = true; break; }
                }
                if (!audio) continue;
                count++;
                total += f.length();
                try {
                    JSONObject it = new JSONObject();
                    it.put("id", nm.replaceAll("\\.[A-Za-z0-9]+$", ""));
                    it.put("bytes", f.length());
                    it.put("ext", nm.replaceAll(".*\\.", ""));
                    String u = readSidecar(nm);
                    it.put("url", u == null ? "" : u);
                    items.put(it);
                } catch (Exception ignored) { }
            }
        }
        try {
            o.put("count", count);
            o.put("bytes", total);
            o.put("items", items);
        } catch (Exception ignored) { }
        return o;
    }

    /** 旁掛 <音頻檔名>.url，記住這集是從哪個地址下來的 —— 前端靠它判斷「已存」 */
    private void writeSidecar(String id, String url) {
        try {
            File f = new File(dir, safe(id) + ".url");
            FileOutputStream o = new FileOutputStream(f);
            o.write((url == null ? "" : url).getBytes("UTF-8"));
            o.close();
        } catch (Exception ignored) { }
    }

    private String readSidecar(String audioFileName) {
        try {
            File f = new File(dir, audioFileName + ".url");
            if (!f.isFile()) return null;
            FileInputStream i = new FileInputStream(f);
            byte[] b = new byte[1024];
            int n = i.read(b);
            i.close();
            return new String(b, 0, Math.max(0, n), "UTF-8");
        } catch (Exception ignored) { return null; }
    }

    /** 按音頻地址刪掉本地緩存（各項目的「刪除某一集」用） */
    public boolean deleteByUrl(String url) {
        String id = idFor(url);
        if (id == null) return false;
        boolean any = false;
        for (int i = 0; i < AUD_EXT.length; i++) {
            File f = fileForExt(id, AUD_EXT[i].substring(1));
            if (f.isFile() && f.delete()) any = true;
        }
        try { File u = new File(dir, safe(id) + ".url"); if (u.isFile()) u.delete(); } catch (Exception ignored) { }
        return any;
    }

    public int clearAll() {
        int n = 0;
        File[] fs = dir.listFiles();
        if (fs != null) {
            for (File f : fs) {
                if (f.isFile() && f.delete()) n++;
            }
        }
        return n;
    }

    // ---------------------------------------------------------------- 內置音頻 → 本地副本

    /**
     * 把 APK 內置的音頻（assets/audio/epNN.m4a）複製到本地緩存 filesDir/audio/shun_epNN.m4a。
     *
     * 為什麼要複製：原生 MediaPlayer 播本地**文件**最穩（不受本機代理 / Range 影響），
     * 鎖屏後台播放也不會斷。複製完可以在 App 裡「全部刪除」把副本清掉
     * （App 本身仍然能用內置音頻，不受影響）。
     */
    public void cacheEpisode(final String epId, final String url) {
        if (worker != null && worker.isAlive()) return;   // 一次只做一集
        state = "downloading";
        curEp = epId == null ? "" : epId;
        curErr = "";
        progress = 0;
        worker = new Thread(new Runnable() {
            @Override
            public void run() { doCacheEpisode(epId, url); }
        });
        worker.start();
    }

    private void doCacheEpisode(String epId, String url) {
        if (epId == null || epId.isEmpty()) { fail("沒有集號"); return; }
        if (assets == null) { fail("App 裡沒有內置音頻"); return; }
        String name = "audio/" + epId + ".m4a";
        long total = assets.size(name);
        if (total <= 0) { fail("內置音頻不存在：" + name); return; }
        String id = assetId(epId);
        File tmp = new File(dir, safe(id) + ".part");
        InputStream in = null;
        FileOutputStream out = null;
        try {
            in = assets.open(name);
            if (in == null) { fail("讀不到內置音頻"); return; }
            out = new FileOutputStream(tmp);
            byte[] buf = new byte[65536];
            long got = 0;
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                got += n;
                if (total > 0) progress = (double) got / (double) total;
            }
            out.flush();
            out.close();
            out = null;
            in.close();
            in = null;
            File dst = fileForExt(id, "m4a");
            if (dst.exists() && !dst.delete()) { fail("覆蓋舊檔失敗"); return; }
            if (!tmp.renameTo(dst)) { fail("存檔失敗"); return; }
            // 旁掛源地址：前端靠它判「已存」（必須按**地址**比，不能按 id）
            writeSidecar(id, url == null ? "" : url);
            progress = 1.0;
            state = "done";
        } catch (Exception e) {
            String m = e.getMessage();
            fail(m == null ? String.valueOf(e.getClass().getSimpleName()) : m);
        } finally {
            try { if (out != null) out.close(); } catch (Exception ignored) { }
            try { if (in != null) in.close(); } catch (Exception ignored) { }
            if (state.equals("error")) tmp.delete();
        }
    }

    // ---------------------------------------------------------------- 下載

    public void download(final String url, final String epId) {
        if (worker != null && worker.isAlive()) return;   // 一次只下一集
        state = "downloading";
        curEp = epId == null ? "" : epId;
        curErr = "";
        progress = 0;
        worker = new Thread(new Runnable() {
            @Override
            public void run() { doDownload(url, epId); }
        });
        worker.start();
    }

    private void doDownload(String url, String epId) {
        if (url == null || url.isEmpty()) { fail("沒有音頻地址"); return; }
        // id 從 URL 派生（帶域名 → 四個項目不會撞名）；認不出才退回傳進來的 epId
        String id = idFor(url);
        if (id == null) id = (epId == null || epId.isEmpty() ? "audio" : epId);
        String ext = extFor(url);
        curEp = id;
        File tmp = new File(dir, safe(id) + ".part");
        HttpURLConnection c = null;
        InputStream in = null;
        FileOutputStream out = null;
        try {
            c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(20000);
            c.setReadTimeout(90000);
            c.setInstanceFollowRedirects(true);
            c.setRequestProperty("User-Agent",
                    "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36");
            c.connect();
            int code = c.getResponseCode();
            if (code != 200) { fail("HTTP " + code); return; }
            long total = c.getContentLength();
            in = c.getInputStream();
            out = new FileOutputStream(tmp);
            byte[] buf = new byte[65536];
            long got = 0;
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                got += n;
                if (total > 0) progress = (double) got / (double) total;
            }
            out.flush();
            out.close();
            out = null;
            in.close();
            in = null;
            File dst = fileForExt(id, ext);
            if (dst.exists() && !dst.delete()) { fail("覆蓋舊檔失敗"); return; }
            if (!tmp.renameTo(dst)) { fail("存檔失敗"); return; }
            writeSidecar(id, url);
            progress = 1.0;
            state = "done";
        } catch (Exception e) {
            String m = e.getMessage();
            fail(m == null ? String.valueOf(e.getClass().getSimpleName()) : m);
        } finally {
            try { if (out != null) out.close(); } catch (Exception ignored) { }
            try { if (in != null) in.close(); } catch (Exception ignored) { }
            if (c != null) c.disconnect();
            if (state.equals("error")) tmp.delete();
        }
    }

    private void fail(String msg) {
        state = "error";
        curErr = msg == null ? "未知錯誤" : msg;
    }

    public JSONObject status() {
        JSONObject o = new JSONObject();
        try {
            o.put("state", state);
            o.put("epId", curEp);
            o.put("error", curErr);
            o.put("progress", progress);
        } catch (Exception ignored) { }
        return o;
    }
}
