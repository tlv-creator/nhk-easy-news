package com.shun.study;

import android.content.Context;
import android.webkit.WebView;

/**
 * 全局运行时持有者 —— **WebView 播放器**跨 Activity 复用。
 *
 * 🔴🔴 为什么必须静态：
 *   网络版的播放器**就是 WebView 里的 <audio>**（不是原生 MediaPlayer）。
 *   ⇒ Activity 一销毁（用户划掉任务 / 内存回收），WebView 跟着销毁 ⇒ **音频立刻断**。
 *   ⇒ 所以 WebView 必须**静态复用**：Activity 重建时复用同一个，不重建、不销毁。
 *
 * 🔴🔴 为什么用 getApplicationContext() 创建 WebView：
 *   WebView 持有创建它的 Context。静态持有 ⇒ 若用 Activity Context 就是**内存泄漏**。
 *   用 Application Context ⇒ 静态持有 WebView 不泄漏 Activity。
 *   （WebView 加进 Activity 的视图树没问题 —— 视图树持有 View，不会反向持有 Activity。）
 *
 * 🔴 与旧版（v1.2.x 内置版）的区别：
 *   旧版静态持有的是「NativePlayer + 本机代理」，origin 固定在 127.0.0.1。
 *   本版**没有代理**：页面直接加载 https://tlv-creator.github.io/... ⇒
 *   origin 恒为 github.io ⇒ **收藏/进度天然稳定**（不依赖端口）。
 */
final class ShunRuntime {

    private static WebView web;

    private ShunRuntime() { }

    /** 幂等：已创建就直接复用（绝不重复 new —— 重复 new 会有两个播放器同时出声）。 */
    static synchronized void init(Context ctx) {
        if (web != null) return;
        try {
            web = new WebView(ctx.getApplicationContext());
        } catch (Throwable t) {
            web = null;
        }
    }

    static WebView web() { return web; }

    /** 🔴 页面是否已经可用（WebView 还没建出来时不能瞎调）。 */
    static boolean ready() {
        return web != null;
    }

    /**
     * 在 UI 线程执行 JS，返回值经 callback 回传。
     * 必须切到主线程：PlaybackService 在后台线程调用它。
     */
    static void eval(final String js, final ValueCallback cb) {
        final WebView w = web;
        if (w == null) {
            if (cb != null) cb.onValue(null);
            return;
        }
        w.post(new Runnable() {
            @Override
            public void run() {
                try {
                    w.evaluateJavascript(js, new android.webkit.ValueCallback<String>() {
                        @Override
                        public void onReceiveValue(String value) {
                            if (cb != null) cb.onValue(value);
                        }
                    });
                } catch (Throwable t) {
                    if (cb != null) cb.onValue(null);
                }
            }
        });
    }

    interface ValueCallback {
        void onValue(String value);
    }
}
