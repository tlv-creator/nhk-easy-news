package com.shun.study;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Shun 播客 · 日中对照精读 —— **网络版**（v1.3.0）
 *
 * 🔴 与旧版（v1.2.x 内置版）的根本区别：
 *   旧版：APK 内置 13 集音频 + 全部数据 ⇒ 42.6MB；加集要重打 APK
 *   本版：**什么都不内置** ⇒ ~20KB
 *         · 内容从 GitHub Pages 拉（50 集懒加载版，永久地址）
 *         · 音频从官方 CDN 拉（每集 audio 字段在 index.json 里）
 *         · **以后加集不用重打 APK**（内容更新 = 推站点）
 *
 * ⚠️ 包名 `com.shun.study` 与证书**必须沿用旧版**
 *    （换了 ⇒ 用户手机装不上 ⇒ 收藏/学习进度全丢）
 *
 * 🔴🔴 播放器是怎么被本 App 接管的（**没改站点一行代码**）：
 *   站点内部是 `S.au = new Audio()`（懒创建：用户点播放时才 new）。
 *   ⇒ 我在页面加载后覆写 `window.Audio`，把它造出来的实例记到 `window.__shunAu`。
 *   ⇒ 之后原生层就能读播放状态、也能被锁屏按钮控制。
 *   ⇒ 站点本身零改动 ⇒ 网页版与 App 版行为完全一致。
 */
public class MainActivity extends Activity {

    /** 🔴 版本号三处必须一致：build 脚本 / manifest / 这里 */
    public static final String APP_VERSION = "1.3.0";

    /** 线上站点（GitHub Pages 永久地址，不会变） */
    private static final String SITE_URL =
        "https://tlv-creator.github.io/nhk-easy-news/shun-web/";

    /** 站外链接：这些留在 App 内的 WebView 里打开，其余交给系统浏览器 */
    private static final String[] KEEP_IN_APP = {
        "tlv-creator.github.io",
        "www.youtube.com",
        "youtu.be",
    };

    /**
     * 🔴 音频捕获钩子。
     * 覆写 window.Audio，把造出来的实例存到 window.__shunAu。
     * 幂等（__shunAuHooked）—— 页面 onPageStarted / onPageFinished 都会注入一次。
     */
    private static final String JS_HOOK_AUDIO =
        "(function(){try{"
        + "if(window.__shunAuHooked) return 'hooked';"
        + "window.__shunAuHooked=true;"
        + "var O=window.Audio; if(!O) return 'noAudio';"
        // 🔴🔴 2026-10-27 修（**真 bug：会让 App 完全没声音**）：
        //    旧写法 `function W(){var a=new O(); ...}` 把 `new Audio(url)` 变成 `new Audio()`
        //    ⇒ **构造参数（音频 URL）被吞掉** ⇒ 实例.src 为空 ⇒ play() 不报错但一点声音都没有。
        //    50 集站是 `new Audio()`（无参，之后再设 .src）⇒ 没暴露；
        //    309 集站是 `new Audio(d.audio)`（带 URL）⇒ 直接踩中。
        //    ⇒ 修法：**透传构造参数**（Reflect.construct 保 new 语义；无参时行为与旧版完全一致）。
        + "function W(){var a; try{a=Reflect.construct(O,Array.prototype.slice.call(arguments));}"
        + "catch(e){a=(arguments.length>0)?new O(arguments[0]):new O();}"
        + "try{window.__shunAu=a;}catch(e){} return a;}"
        + "W.prototype=O.prototype;"
        + "try{Object.defineProperty(W,'length',{value:O.length});}catch(e){}"
        + "window.Audio=W; return 'ok';"
        + "}catch(e){return 'err:'+e;}})();";

    /**
     * 读播放状态。pos/dur 都是**毫秒**。
     * 🔴 站点里的 fmt() 用秒，但原生层统一用毫秒（和旧版 playerState() 口径一致）。
     */
    private static final String JS_STATE =
        "(function(){try{"
        + "var a=window.__shunAu;"
        + "if(!a) return JSON.stringify({has:false,playing:false,pos:0,dur:0,ended:false});"
        + "var d=(a.duration&&isFinite(a.duration))?a.duration:0;"
        + "return JSON.stringify({has:true,playing:(!a.paused&&!a.ended),pos:Math.round(a.currentTime*1000),"
        + "dur:Math.round(d*1000),ended:!!a.ended});"
        + "}catch(e){return JSON.stringify({has:false,playing:false,pos:0,dur:0,ended:false});}})();";

    private WebView web;
    private TextView tip;
    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());
    private boolean wasPlaying = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 🔴 WebView 静态复用（ShunRuntime）—— Activity 重建不重建播放器，
        //    划掉任务也不销毁 ⇒ 音频不断。
        ShunRuntime.init(this);
        web = ShunRuntime.web();
        if (web == null) {
            Toast("初始化失败，请重开 App");
            return;
        }

        // 状态栏/导航栏纯色（墨水屏不要渐变）
        if (Build.VERSION.SDK_INT >= 21) {
            getWindow().setStatusBarColor(Color.WHITE);
            getWindow().setNavigationBarColor(Color.WHITE);
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        // 🔴🔴 **WebView 是静态复用的**（ShunRuntime）⇒ Activity 被系统重建时（内存回收 /
        //    「不保留活动」）会拿到**同一个** WebView 对象。
        //    而 ViewGroup.addView() 遇到「已经有 parent 的 child」会直接抛
        //    IllegalStateException: The specified child already has a parent.
        //    ⇒ 必须先把旧 parent 摘掉再挂上去。
        ViewGroup oldParent = (ViewGroup) web.getParent();
        if (oldParent != null) {
            try { oldParent.removeView(web); } catch (Throwable ignored) { }
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        root.setLayoutParams(new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // 🔴 断网提示条：不能给一个空白页（用户得知道是断网还是站点挂了）
        tip = new TextView(this);
        tip.setText("正在连接…如果一直空白，请检查网络");
        tip.setTextSize(15);
        tip.setTextColor(Color.BLACK);
        tip.setPadding(24, 24, 24, 24);
        tip.setVisibility(View.GONE);
        root.addView(tip, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // 收藏/进度
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);  // 允许 JS 直接起播
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setTextZoom(100);
        s.setAllowFileAccess(false);           // 本版没有本地资源
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        }

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleUrl(url);
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return handleUrl(req.getUrl().toString());
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap fav) {
                super.onPageStarted(view, url, fav);
                inject(JS_HOOK_AUDIO);          // 早注入，覆盖住站点脚本
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                inject(JS_HOOK_AUDIO);          // 再注入一次兜底（幂等）
                tip.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req != null && req.isForMainFrame()) {
                    tip.setVisibility(View.VISIBLE);
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient());
        web.addJavascriptInterface(new Bridge(), "Android");

        root.addView(web, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);

        web.loadUrl(SITE_URL);
        startWatch();
        requestNotifPermission();
    }

    /**
     * Android 13+ 通知权限。
     * 🔴 不给也能播（前台服务照跑），但**锁屏上的播放控制条不显示** ⇒ 要问。
     */
    private void requestNotifPermission() {
        try {
            if (Build.VERSION.SDK_INT >= 33) {
                requestPermissions(new String[]{
                    "android.permission.POST_NOTIFICATIONS"}, 1001);
            }
        } catch (Throwable ignored) { }
    }

    private void inject(String js) {
        try {
            web.evaluateJavascript(js, null);
        } catch (Throwable ignored) { }
    }

    /** 返回 true = 我处理了（不给系统），false = 交给 WebView 正常加载 */
    private boolean handleUrl(String url) {
        if (url == null) return false;
        for (String h : KEEP_IN_APP) {
            if (url.contains(h)) return false;   // 留在 App 内
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Throwable ignored) { }
        return true;
    }

    /**
     * 播放状态看护：每 500ms 读一次。
     * ① false→true ⇒ 起前台服务（保活 + 锁屏控制）
     * ② 播放结束 ⇒ 通知服务撤掉
     */
    private void startWatch() {
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (web == null) return;
                try {
                    web.evaluateJavascript(JS_STATE, new android.webkit.ValueCallback<String>() {
                        @Override
                        public void onReceiveValue(String value) {
                            onState(value);
                        }
                    });
                } catch (Throwable ignored) { }
                main.postDelayed(this, 500);
            }
        }, 500);
    }

    private void onState(String json) {
        boolean playing = false, ended = false;
        try {
            if (json == null) return;
            String s = json.trim();
            if (s.startsWith("\"") && s.endsWith("\"")) s = s.substring(1, s.length() - 1);
            s = s.replace("\\\"", "\"").replace("\\\\", "\\");
            org.json.JSONObject o = new org.json.JSONObject(s);
            playing = o.optBoolean("playing", false);
            ended = o.optBoolean("ended", false);
        } catch (Throwable ignored) {
            return;
        }
        if (playing && !wasPlaying) {
            // 🔴 只在**真的要播了**才起服务 ⇒ 不在启动路径上 ⇒ 避开前台服务 5 秒红线
            PlaybackService.ensureStarted(this);
        }
        if (ended && wasPlaying) {
            try {
                stopService(new Intent(this, PlaybackService.class));
            } catch (Throwable ignored) { }
        }
        wasPlaying = playing;
    }

    @Override
    protected void onPause() {
        super.onPause();
        // 🔴🔴 **这里绝对不能 web.onPause()** ——
        //   锁屏 / 按 home / 切到别的 App 全都会走 onPause → 一进后台就断声。
        //   播放交给 PlaybackService（前台服务）保活，界面回来时状态还在。
    }

    @Override
    protected void onDestroy() {
        // 🔴🔴 **这里不能 web.destroy()** ——
        //   「划掉界面不断声」要求任务被划掉后播放继续。
        //   WebView 由 ShunRuntime 静态持有，进程真死时才随之回收。
        main.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    private void Toast(String msg) {
        try {
            android.widget.Toast.makeText(this, msg, android.widget.Toast.LENGTH_LONG).show();
        } catch (Throwable ignored) { }
    }

    /**
     * 锁屏 / 通知栏按钮 → 反向控制页面里的 &lt;audio&gt;
     *
     * 🔴🔴 **必须是 static 内部类**：
     *   非 static 内部类隐式持有外部 MainActivity ⇒
     *   ShunRuntime（静态）→ WebView（静态）→ JS 接口 → Activity
     *   ⇒ **Activity 永远无法回收**（泄漏）。
     *   改成 static 后，这里只用 ShunRuntime，不碰 Activity。
     */
    public static class Bridge {
        @JavascriptInterface
        public String playerState() {
            // 同步取不到；PlaybackService 走 poll 路径（异步读页面里的 <audio>）
            return "{\"has\":false,\"playing\":false,\"pos\":0,\"dur\":0,\"ended\":false}";
        }

        @JavascriptInterface
        public String play() {
            ShunRuntime.eval(
                "try{if(window.__shunAu){window.__shunAu.play();return 'ok';}}catch(e){}return 'no';", null);
            return "ok";
        }

        @JavascriptInterface
        public String pause() {
            ShunRuntime.eval(
                "try{if(window.__shunAu){window.__shunAu.pause();return 'ok';}}catch(e){}return 'no';", null);
            return "ok";
        }

        @JavascriptInterface
        public String seekTo(double ms) {
            ShunRuntime.eval(
                "try{if(window.__shunAu){window.__shunAu.currentTime=" + (ms / 1000.0) + ";return 'ok';}}catch(e){}return 'no';", null);
            return "ok";
        }

        @JavascriptInterface
        public String version() {
            return APP_VERSION;
        }
    }
}
