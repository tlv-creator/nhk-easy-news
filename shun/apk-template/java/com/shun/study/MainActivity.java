package com.shun.study;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import org.json.JSONObject;

/**
 * 日語精読 hub —— 一个 App 装下全部日语精读项目。
 *
 * 页面结构：本机代理伺服 /app/index.html（总目录），四个子项目在 /app/<项目>/ 下。
 * 各项目的音频都是**远端绝对地址**（自己的 CDN / 音源站），APK 里不打包音频。
 *
 * 音频有两条路：
 *   1) 原生 MediaPlayer 直连远端 URL   ← 默认，后台播放 / 锁屏不断声
 *   2) WebView 的 &lt;audio&gt; → 同一路地址              ← 原生失败时兜底
 *
 * 三个子项目的原生接口各不相同，这里统一收编成魔卡那套（playAudio/playerState/...）：
 *   若草 window.WakakusaNative  ← hub-shim.js 适配
 *   哆啦 window.DoraNative      ← hub-shim.js 适配
 *   あま window.Android.play     ← 源码已改成 playAudio
 * 而若草/哆啦的播放进度是「原生推送」模型，本类每 500ms 推 __wkTick / __doraTick。
 */
public class MainActivity extends Activity {

    /** 线上站（可选，资源来源切换用） */
    public static final String ONLINE_URL = "";   // 一律用內置資源（換版不必重打）
    /** 本机代理固定端口：origin 稳定 → 观看记录 / 生词本的 localStorage 能持久化 */
    public static final int FIXED_PORT = ShunRuntime.FIXED_PORT;
    public static final String APP_VERSION = "1.2.4";

    private WebView web;
    /* 🔴 这三个字段指向 **ShunRuntime 里的同一批对象**（静态复用）——
     *   Activity 重建时不会 new 出第二个播放器，也不会抢不到固定端口。*/
    private MediaProxy proxy;
    private AudioCache cache;
    private NativePlayer player;
    private final Handler main = new Handler(Looper.getMainLooper());

    private boolean pageLoaded = false;
    private boolean swChecked = false;
    private boolean reloading = false;

    // ---------------------------------------------------------------- 生命周期

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 本地服务：App 内 127.0.0.1 的迷你 HTTP 服务
        //   /media/<epId>.m4a  音频（带桌面 UA + Referer 去 B 站取流）
        //   /app/<path>        内置前端资源（这样页面 origin 稳定，localStorage 才持久）
        // 🔴🔴 走 ShunRuntime：**静态复用**。Activity 重建时如果 new 一遍，
        //   → 两个播放器同时出声 + 代理抢不到固定端口 → origin 变 → localStorage 全丢。
        ShunRuntime.init(this);
        proxy = ShunRuntime.proxy();
        cache = ShunRuntime.cache();
        player = ShunRuntime.player();
        if (proxy == null) {
            Toast.makeText(this, "本地服务启动失败", Toast.LENGTH_LONG).show();
        }
        requestNotifPermission();

        web = new WebView(this);
        setContentView(web);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);   // 允许 JS 直接起播
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setTextZoom(100);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            // 必须在 loadUrl 之前设置：页面是 https，音频走 http://127.0.0.1
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url != null && url.startsWith("http://127.0.0.1")) return false;
                if (url != null && (url.startsWith("http://") || url.startsWith("https://"))) {
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                    } catch (ActivityNotFoundException ignored) {
                    }
                    return true;
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                pageLoaded = true;
                if (!swChecked) cleanupLegacyServiceWorker();
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description,
                                        String failingUrl) {
                if (!reloading && failingUrl != null && failingUrl.contains("127.0.0.1")) {
                    loadAssetFallback();
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient());
        web.addJavascriptInterface(new Bridge(), "Android");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        web.loadUrl(localUrl());
        startTicker();
        // 本地页面 8 秒还没出来 → 退回 file:// 内置资源（最后兜底）
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (!pageLoaded) loadAssetFallback();
            }
        }, 8000);
    }

    /**
     * 播放进度推送：若草(window.__wkTick) 与 哆啦(window.__doraTick) 是「原生推给前端」模型，
     * 魔卡是「前端自己轮询 playerState()」。这里两种都喂，前端各自取用。
     * 另外推一个**通用**的 window.__hubTick(t)（对象）—— 新项目接它即可，不用改原生层。
     * 格式差异：__wkTick 收**字符串**，__doraTick 收**对象**；pos/dur 都是毫秒。
     */
    private void startTicker() {
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (web == null) return;
                try {
                    String js = "(function(){try{var s=JSON.parse(window.Android.playerState());"
                        // 🔴🔴🔴 **pos/dur 必须是毫秒**（2026-10-23 修的真 bug）：
                        //   原生层 playerState() 给的是**秒**（getCurrentPosition()/1000），
                        //   而前端 S.pos / binarySearch() / fmtMs() 期望**毫秒**
                        //   ⇒ 单位差 1000 倍 ⇒ **APK 里高亮整集停在第一句、时间标签恒为 0:00**。
                        //   （网页路径推的是毫秒 ⇒ 只有 App 里坏；测试里 pos=0 看不出来 ⇒ 差点漏过去）
                        + "var t={pos:s.pos*1000,dur:s.dur*1000,playing:s.playing,actualPlaying:s.actualPlaying,"
                        + "ready:s.ready,completed:s.completed,err:s.error||''};"
                        + "if(window.__wkTick){try{window.__wkTick(JSON.stringify(t));}catch(e){}}"
                        + "if(window.__doraTick){try{window.__doraTick(t);}catch(e){}}"
                        // 通用钩子：以后新项目直接用 window.__hubTick(t) 就行，不用再改原生层
                        + "if(window.__hubTick){try{window.__hubTick(t);}catch(e){}}}catch(e){}})();";
                    web.evaluateJavascript(js, null);
                } catch (Throwable ignored) { }
                main.postDelayed(this, 500);
            }
        }, 500);
    }

    /** 本机代理上的内置页面 */
    public String localUrl() {
        return "http://127.0.0.1:" + proxy.getPort() + "/app/index.html";
    }

    private void loadAssetFallback() {
        if (reloading) return;
        reloading = true;
        pageLoaded = false;
        web.loadUrl("file:///android_asset/index.html");
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                reloading = false;
            }
        }, 4000);
    }

    /**
     * 旧版本装过的话，WebView 里会残留 Service Worker / Cache Storage，
     * 它会一直喂旧的 app.js（表现为"改了前端但 App 里没变"）。这里清一次。
     */
    private void cleanupLegacyServiceWorker() {
        if (swChecked) return;
        swChecked = true;
        final String js =
                "(function(){var done=function(v){window.__ccsSW=v;};try{"
                        + "if(!navigator.serviceWorker){done('clean');return;}"
                        + "var n=0,k=0;"
                        + "navigator.serviceWorker.getRegistrations().then(function(rs){"
                        + "n=rs.length;rs.forEach(function(r){r.unregister();});})"
                        + ".then(function(){return caches.keys();})"
                        + ".then(function(ks){k=ks.length;ks.forEach(function(x){caches.delete(x);});})"
                        + ".then(function(){done((n>0||k>0)?'dirty':'clean');})"
                        + ".catch(function(){done('clean');});"
                        + "}catch(e){done('clean');}})();";
        web.evaluateJavascript(js, null);
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                checkSwState(0);
            }
        }, 500);
    }

    private void checkSwState(final int attempt) {
        if (attempt > 10) return;
        web.evaluateJavascript("window.__ccsSW || ''", new android.webkit.ValueCallback<String>() {
            @Override
            public void onReceiveValue(String value) {
                String v = value == null ? "" : value.replace("\"", "").trim();
                if ("dirty".equals(v)) {
                    web.reload();
                } else if (!"clean".equals(v)) {
                    main.postDelayed(new Runnable() {
                        @Override
                        public void run() {
                            checkSwState(attempt + 1);
                        }
                    }, 400);
                }
            }
        });
    }

    @Override
    protected void onPause() {
        super.onPause();
        // 🔴🔴 **这里绝对不能一刀切 pause()** ——
        //   锁屏 / 按 home / 切到别的 App 全都会走 onPause → 一进后台就断声。
        //   播放交给 PlaybackService（前台服务）继续，界面回来时状态还在。
    }

    @Override
    protected void onDestroy() {
        // 🔴🔴 **这里不能 player.stop() / proxy.stop()** ——
        //   「划掉界面不断声」要求任务被划掉后播放继续（前台服务 + START_STICKY）。
        //   播放器与代理都是静态复用的，进程真正死掉时系统会一并回收。
        if (web != null) {
            try {
                web.loadUrl("about:blank");
                web.destroy();
            } catch (Exception ignored) {
            }
        }
        super.onDestroy();
    }

    /** Android 13+ 要通知权限才能显示前台服务的通知（不给也不影响播放）。 */
    private void requestNotifPermission() {
        try {
            if (Build.VERSION.SDK_INT >= 33) {
                java.util.List<String> need = new java.util.ArrayList<String>();
                if (checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                        != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                    need.add("android.permission.POST_NOTIFICATIONS");
                }
                if (!need.isEmpty()) requestPermissions(need.toArray(new String[0]), 1001);
            }
        } catch (Throwable ignored) { }
    }

    @Override
    public void onBackPressed() {
        // 1) 先让前端自己处理（魔卡的「从播放页退回剧集列表」）
        web.evaluateJavascript("window.__ccsHandleBack ? window.__ccsHandleBack() : false",
                new android.webkit.ValueCallback<String>() {
                    @Override
                    public void onReceiveValue(String value) {
                        if ("true".equals(value)) return;
                        // 2) 不是首页 → 退回上一层历史（子项目 → 总目录首页）
                        if (web != null && web.canGoBack()) {
                            web.goBack();
                            return;
                        }
                        // 3) 已经在首页 → 退出
                        finish();
                    }
                });
    }

    // ---------------------------------------------------------------- JS 桥接

    public class Bridge {

        /** 从远端下载一集音频落盘（备用路径；内置音频走 cacheEpisode）。 */
        @JavascriptInterface
        public String downloadAudio(String url, String epId) {
            org.json.JSONObject o = new org.json.JSONObject();
            try {
                cache.download(url == null ? "" : url, epId == null ? "" : epId);
                o.put("ok", true);
            } catch (Throwable t) {
                try { o.put("ok", false); o.put("error", String.valueOf(t.getMessage())); }
                catch (Throwable ignored) { }
            }
            return o.toString();
        }

        /** 删掉某一集的本地缓存（按音頻地址定位）。 */
        @JavascriptInterface
        public String deleteAudio(String url) {
            org.json.JSONObject o = new org.json.JSONObject();
            try {
                boolean ok = cache.deleteByUrl(url == null ? "" : url);
                o.put("ok", ok);
            } catch (Throwable t) {
                try { o.put("ok", false); o.put("error", String.valueOf(t.getMessage())); }
                catch (Throwable ignored) { }
            }
            return o.toString();
        }

        /** 下载进度：state = idle|downloading|done|error。 */
        @JavascriptInterface
        public String downloadStatus() {
            return cache.status().toString();
        }

        /** 已缓存的音频清单：{count, bytes, items:[{id,bytes}]}。 */
        @JavascriptInterface
        public String downloadedList() {
            return cache.stat().toString();
        }

        /** 一键删光所有已下载音频，返回删了几个。 */
        @JavascriptInterface
        public String clearDownloads() {
            org.json.JSONObject o = new org.json.JSONObject();
            try { o.put("removed", cache.clearAll()); } catch (Throwable ignored) { }
            return o.toString();
        }

        /** 返回本机代理的音频地址（页面只跟 127.0.0.1 说话）。 */
        @JavascriptInterface
        public String resolveAudio(String epId) {
            JSONObject o = new JSONObject();
            try {
                o.put("url", "http://127.0.0.1:" + proxy.getPort() + "/media/" + epId + ".m4a");
                o.put("port", proxy.getPort());
                o.put("ok", true);
            } catch (Throwable t) {
                try {
                    o.put("ok", false);
                    o.put("error", String.valueOf(t.getMessage()));
                } catch (Throwable ignored) {
                }
            }
            return o.toString();
        }

        /** 诊断信息：代理端口、最近错误、版本。 */
        @JavascriptInterface
        public String ping() {
            JSONObject o = new JSONObject();
            try {
                o.put("port", proxy.getPort());
                o.put("lastError", proxy.getLastError());
                o.put("version", APP_VERSION);
                o.put("native", true);
                o.put("proxyOk", proxy.getPort() > 0);
            } catch (Throwable ignored) {
            }
            return o.toString();
        }

        /**
         * 播内置的一集（assets/audio/epNN.m4a）—— 本项目的音频在 APK 里，离线可听。
         * 顺序：本地副本 → 内置资源 → 远端地址。
         */
        @JavascriptInterface
        public String playEpisode(String epId, String url, double startMs) {
            double sec = startMs <= 0 ? 0 : startMs / 1000.0;
            // 🔴 前台服务**只在真的要播的时候**才起 —— 不放启动路径，避开 5 秒红线
            PlaybackService.ensureStarted(MainActivity.this);
            if (player != null) player.playEpisode(epId == null ? "" : epId, url == null ? "" : url, sec);
            return "{\"ok\":true}";
        }

        /**
         * 把内置的一集复制到手机本地（filesDir/audio/shun_epNN.m4a）——
         * 复制后原生播放器走本地文件，后台播放最稳；也可以在 App 里一键删掉。
         */
        @JavascriptInterface
        public String cacheEpisode(String epId, String url) {
            org.json.JSONObject o = new org.json.JSONObject();
            try {
                cache.cacheEpisode(epId == null ? "" : epId, url == null ? "" : url);
                o.put("ok", true);
            } catch (Throwable t) {
                try { o.put("ok", false); o.put("error", String.valueOf(t.getMessage())); }
                catch (Throwable ignored) { }
            }
            return o.toString();
        }

        /** 原生播放。startMs 小于 0 表示从头。 */
        @JavascriptInterface
        public String playAudio(String url, double startMs) {
            double sec = startMs <= 0 ? 0 : startMs / 1000.0;
            PlaybackService.ensureStarted(MainActivity.this);
            if (player != null) player.play(url, sec, player.getSpeed());
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String pauseAudio() {
            player.pause();
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String resumeAudio() {
            player.resume();
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String seekAudio(double ms) {
            player.seek(ms <= 0 ? 0 : ms / 1000.0);
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String setSpeed(double s) {
            player.setSpeed((float) (s <= 0 ? 1.0 : s));
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String stopAudio() {
            player.stop();
            return "{\"ok\":true}";
        }

        /** 原生播放器状态（JS 靠它同步字幕）。 */
        @JavascriptInterface
        public String playerState() {
            JSONObject o = new JSONObject();
            try {
                o.put("pos", player.getPos());
                o.put("dur", player.getDur());
                // playing = **用户意图**（准备期也 true）；actualPlaying = 真的在出声
                o.put("playing", player.isPlaying());
                o.put("actualPlaying", player.isActuallyPlaying());
                o.put("wantPlay", player.isWanted());
                o.put("ready", player.isReady());
                o.put("completed", player.isCompleted());
                o.put("error", player.getError());
                o.put("epId", player.getEpId());
                o.put("speed", player.getSpeed());
            } catch (Throwable ignored) {
            }
            return o.toString();
        }

        /** 一键自检：解析 + 首段拉取 + Range 验证。装机后在手机点一下就知道断在哪。 */
        @JavascriptInterface
        public String selfTest(String epId) {
            return proxy.selfTest(epId);
        }

        @JavascriptInterface
        public String version() {
            return APP_VERSION;
        }

        /** 切到线上资源（CDN）—— hub 是自包含的，URL 没配就回本地 */
        @JavascriptInterface
        public String loadOnline() {
            main.post(new Runnable() {
                @Override
                public void run() {
                    String u = ONLINE_URL;
                    if (u == null || u.indexOf("JPHUB_HOST") >= 0) { web.loadUrl(localUrl()); return; }
                    web.loadUrl(u);
                }
            });
            return "{\"ok\":true}";
        }

        /** 切回内置资源 */
        @JavascriptInterface
        public String loadOffline() {
            main.post(new Runnable() {
                @Override
                public void run() {
                    web.loadUrl(localUrl());
                }
            });
            return "{\"ok\":true}";
        }
    }
}
